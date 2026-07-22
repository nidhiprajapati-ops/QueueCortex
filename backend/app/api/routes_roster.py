"""Shift Watch: upload an L2 shift roster and surface tickets held by a
roster agent who is currently off-shift (shift ended, hasn't started yet
today, or they're off/on leave), so Yashwanth can step in and resolve them.
See app/roster.py for the CSV parsing and shift-status logic; the tickets
themselves come from the same tables the personal sync uses, but are never
marked `is_tracked` - see the note on that flag in sync/engine.py."""

import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_sync_manager
from app.api.routes_tickets import load_ticket_detail
from app.config import Settings
from app.db import get_session
from app.derive import last_assignment_at_for_agent
from app.models import RosterAgent, RosterShift, Ticket
from app.roster import classify_tags, compute_shift_status, parse_roster_csv, parse_roster_xlsx
from app.schemas import RosterAgentOut, RosterOverdueTicket, RosterShiftUpdateRequest, RosterUploadResult, TicketDetailOut
from app.sync.manager import SyncManager
from app.sync.timeutil import to_reporting_datetime, utcnow

router = APIRouter(tags=["roster"])

UPSERT_CHUNK_SIZE = 300


def _is_associate_or_trainer(role: str) -> bool:
    r = role.lower()
    return "associate" in r or "trainer" in r


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def _upsert_roster(session: AsyncSession, agents: list[dict], shifts: list[dict], now: datetime) -> None:
    for batch in _chunks(agents, UPSERT_CHUNK_SIZE):
        if not batch:
            continue
        stmt = sqlite_insert(RosterAgent).values(
            [{"email": a["email"], "name": a["name"], "role": a["role"], "updated_at": now} for a in batch]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["email"],
            set_={"name": stmt.excluded.name, "role": stmt.excluded.role, "updated_at": stmt.excluded.updated_at},
        )
        await session.execute(stmt)

    for batch in _chunks(shifts, UPSERT_CHUNK_SIZE):
        if not batch:
            continue
        stmt = sqlite_insert(RosterShift).values(
            [{"agent_email": s["agent_email"], "shift_date": s["shift_date"], "shift_code": s["shift_code"]} for s in batch]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["agent_email", "shift_date"],
            set_={"shift_code": stmt.excluded.shift_code},
        )
        await session.execute(stmt)

    await session.commit()


def _decode_csv_text(raw: bytes) -> str:
    """Excel's plain "CSV (Comma delimited)" save option writes the file in
    the system's ANSI codepage (cp1252 on typical Windows setups), not
    UTF-8, unless the user specifically picks "CSV UTF-8" - so a roster
    re-saved from Excel is a completely normal case to fall back for, not
    an error. latin-1 never raises (every byte maps to something), so it's
    the last-resort catch-all rather than a real "guess"."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


@router.post("/roster/upload", response_model=RosterUploadResult)
async def upload_roster(
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    sync_manager: SyncManager = Depends(get_sync_manager),
):
    raw = await file.read()
    filename = (file.filename or "").lower()
    now = utcnow()

    try:
        if filename.endswith((".xlsx", ".xlsm")):
            agents, shifts = parse_roster_xlsx(raw, base_year=now.year)
        elif filename.endswith(".xls"):
            raise HTTPException(400, "The old .xls format isn't supported - please save as .xlsx or .csv and try again")
        else:
            agents, shifts = parse_roster_csv(_decode_csv_text(raw), base_year=now.year)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # Any parsing failure (corrupt file, unexpected sheet shape, etc.)
        # should be a clear 400 the user can act on, never a raw 500 - this
        # endpoint takes arbitrary user-uploaded files, so it must not trust
        # them to always match the expected shape.
        raise HTTPException(400, f"Couldn't read that file as a roster: {exc}") from exc

    if not agents:
        raise HTTPException(400, "No agent rows found - check the file matches the expected roster format")

    await _upsert_roster(session, agents, shifts, now)

    # Best-effort immediate refresh so Shift Watch isn't stale until the next
    # poll tick; doesn't block the upload response on Trinity round-trips.
    asyncio.create_task(sync_manager.run_roster_sync_now())

    dates = [s["shift_date"] for s in shifts]
    return RosterUploadResult(
        agents=len(agents),
        shift_rows=len(shifts),
        date_range=[min(dates), max(dates)] if dates else [None, None],
    )


@router.get("/roster/agents", response_model=list[RosterAgentOut])
async def list_roster_agents(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_app_settings)
):
    agents = (await session.execute(select(RosterAgent).order_by(RosterAgent.name))).scalars().all()
    if not agents:
        return []

    now_local = to_reporting_datetime(utcnow(), settings.reporting_timezone)
    today = now_local.date()
    tomorrow = today + timedelta(days=1)

    shift_rows = (
        await session.execute(
            select(RosterShift).where(
                RosterShift.agent_email.in_([a.email for a in agents]), RosterShift.shift_date.in_((today, tomorrow))
            )
        )
    ).scalars().all()
    shift_map: dict[tuple[str, date], str] = {(r.agent_email, r.shift_date): r.shift_code for r in shift_rows}

    return [
        RosterAgentOut(
            email=a.email,
            name=a.name,
            role=a.role,
            today_shift_code=shift_map.get((a.email, today)),
            tomorrow_shift_code=shift_map.get((a.email, tomorrow)),
        )
        for a in agents
    ]


@router.put("/roster/agents/{email}/shift", response_model=RosterAgentOut)
async def update_roster_agent_shift(
    email: str,
    body: RosterShiftUpdateRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
):
    """Manual override for one agent's today/tomorrow shift cell - the
    uploaded sheet is only as fresh as the last upload, and a shift change
    someone mentions in the meantime shouldn't have to wait for a
    re-upload. Same upsert path as the CSV import (agent_email,
    shift_date) unique constraint), so a later roster upload for the same
    date simply overwrites this the same way it always would."""
    agent = await session.get(RosterAgent, email)
    if agent is None:
        raise HTTPException(404, "Agent not found in roster")

    code = body.shift_code.strip()
    if not code:
        raise HTTPException(400, "Shift code can't be empty")

    now_local = to_reporting_datetime(utcnow(), settings.reporting_timezone)
    today = now_local.date()
    tomorrow = today + timedelta(days=1)
    target_date = today if body.which == "today" else tomorrow

    stmt = sqlite_insert(RosterShift).values(agent_email=email, shift_date=target_date, shift_code=code)
    stmt = stmt.on_conflict_do_update(index_elements=["agent_email", "shift_date"], set_={"shift_code": stmt.excluded.shift_code})
    await session.execute(stmt)
    await session.commit()

    shift_rows = (
        await session.execute(select(RosterShift).where(RosterShift.agent_email == email, RosterShift.shift_date.in_((today, tomorrow))))
    ).scalars().all()
    shift_map = {r.shift_date: r.shift_code for r in shift_rows}
    return RosterAgentOut(
        email=agent.email,
        name=agent.name,
        role=agent.role,
        today_shift_code=shift_map.get(today),
        tomorrow_shift_code=shift_map.get(tomorrow),
    )


@router.get("/roster/overdue-tickets", response_model=list[RosterOverdueTicket])
async def roster_overdue_tickets(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_app_settings)
):
    roster_agents = {a.email: a for a in (await session.execute(select(RosterAgent))).scalars().all()}
    if not roster_agents:
        return []

    # No level/tag filtering here - roster_sync.py only ever ingests tickets
    # from the two "L2, non-Expo" Trinity buckets, so anything in `tickets`
    # is already correctly scoped by Trinity's own bucket rules.
    tickets = (
        await session.execute(
            select(Ticket).where(
                Ticket.assigned_to_email.in_(list(roster_agents.keys())), Ticket.status.in_(("OPEN", "PENDING"))
            )
        )
    ).scalars().all()
    if not tickets:
        return []

    now_local = to_reporting_datetime(utcnow(), settings.reporting_timezone)
    today = now_local.date()
    yesterday = today - timedelta(days=1)

    emails_involved = {t.assigned_to_email for t in tickets}
    shift_rows = (
        await session.execute(
            select(RosterShift).where(RosterShift.agent_email.in_(emails_involved), RosterShift.shift_date.in_((today, yesterday)))
        )
    ).scalars().all()
    shift_by_agent_date: dict[tuple[str, date], str] = {(r.agent_email, r.shift_date): r.shift_code for r in shift_rows}

    by_email: dict[str, list[str]] = defaultdict(list)
    for t in tickets:
        by_email[t.assigned_to_email].append(t.id)
    held_since: dict[str, datetime] = {}
    for email, ids in by_email.items():
        held_since.update(await last_assignment_at_for_agent(session, ids, email))

    out: list[RosterOverdueTicket] = []
    for t in tickets:
        email = t.assigned_to_email
        today_code = shift_by_agent_date.get((email, today))
        yesterday_code = shift_by_agent_date.get((email, yesterday))
        status = compute_shift_status(now_local, today_code, yesterday_code)
        if status.on_shift:
            continue

        agent = roster_agents.get(email)
        type_override, alert_tags = classify_tags(t.tags_cache)
        out.append(
            RosterOverdueTicket(
                id=t.id,
                num=t.num,
                derived_type=type_override or t.derived_type,
                assigned_to_email=email,
                agent_name=agent.name if agent else email,
                agent_role=agent.role if agent else "",
                is_associate_or_trainer=_is_associate_or_trainer(agent.role) if agent else False,
                shift_code=status.shift_label,
                reason=status.reason,
                held_since=held_since.get(t.id),
                last_event_at=t.last_event_at,
                trinity_url=t.trinity_url,
                alert_tags=alert_tags,
            )
        )

    out.sort(key=lambda r: r.held_since or datetime.min)
    return out


@router.get("/roster/tickets/{ticket_id}", response_model=TicketDetailOut)
async def get_roster_ticket_detail(ticket_id: str, session: AsyncSession = Depends(get_session)):
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    detail = await load_ticket_detail(session, ticket_id, ticket.assigned_to_email or "")
    if detail is None:
        raise HTTPException(404, "Ticket not found")
    return detail
