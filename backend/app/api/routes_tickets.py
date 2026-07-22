import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_trinity_client
from app.config import Settings
from app.db import get_session
from app.derive import (
    compute_actually_closed_today_ids,
    compute_assignment_flags,
    compute_level_flags,
    compute_needs_attention_ids,
    compute_self_release_flags,
    compute_ticket_flags,
    compute_today_snapshot,
)
from app.models import AssignmentEvent, CsatEvent, Customer, LevelTransition, LocalNote, StatusTransition, Ticket, TicketDuplicate, TicketTag
from app.schemas import (
    AddTicketRequest,
    LocalNoteCreate,
    LocalNoteOut,
    LocalNoteUpdate,
    TicketDetailOut,
    TicketListItem,
    TicketListResponse,
)
from app.sync.engine import TicketNotFoundError, add_ticket_by_number, get_last_own_internal_note
from app.sync.timeutil import utcnow
from app.trinity_client import TrinityClient

router = APIRouter(tags=["tickets"])

SORTABLE = {"last_event_at": Ticket.last_event_at, "num": Ticket.num, "created_at": Ticket.created_at_trinity}


@router.get("/tickets/status-counts")
async def ticket_status_counts(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_app_settings)
):
    rows = (
        await session.execute(select(Ticket.status, func.count()).where(Ticket.is_tracked.is_(True)).group_by(Ticket.status))
    ).all()
    by_status = {status: count for status, count in rows}

    escalated = (
        await session.execute(
            select(func.count()).select_from(Ticket).where(Ticket.level == "L3", Ticket.is_tracked.is_(True))
        )
    ).scalar_one()
    unassigned = (
        await session.execute(
            select(func.count())
            .select_from(Ticket)
            .where(or_(Ticket.assigned_to_email.is_(None), Ticket.assigned_to_email == ""), Ticket.is_tracked.is_(True))
        )
    ).scalar_one()

    today_snapshot = await compute_today_snapshot(session, settings)

    needs_attention = len(await compute_needs_attention_ids(session, settings))

    taken_from_me = (
        await session.execute(
            select(func.count(func.distinct(AssignmentEvent.ticket_id)))
            .select_from(AssignmentEvent)
            .join(Ticket, Ticket.id == AssignmentEvent.ticket_id)
            .where(AssignmentEvent.is_taken_from_tracked_agent.is_(True), Ticket.is_tracked.is_(True))
        )
    ).scalar_one()

    self_released = (
        await session.execute(
            select(func.count(func.distinct(AssignmentEvent.ticket_id)))
            .select_from(AssignmentEvent)
            .join(Ticket, Ticket.id == AssignmentEvent.ticket_id)
            .where(AssignmentEvent.is_self_release_for_tracked_agent.is_(True), Ticket.is_tracked.is_(True))
        )
    ).scalar_one()

    # Only transitions the tracked agent themselves performed count as
    # "escalated"/"de-escalated" - not just any level change that happened to
    # land on a ticket that's/was theirs (e.g. a supervisor correcting the
    # level while they still hold it).
    escalated_count = (
        await session.execute(
            select(func.count(func.distinct(LevelTransition.ticket_id)))
            .select_from(LevelTransition)
            .join(Ticket, Ticket.id == LevelTransition.ticket_id)
            .where(
                LevelTransition.is_escalation.is_(True),
                LevelTransition.performed_by_email == settings.tracked_agent_email,
                Ticket.is_tracked.is_(True),
            )
        )
    ).scalar_one()

    deescalated_count = (
        await session.execute(
            select(func.count(func.distinct(LevelTransition.ticket_id)))
            .select_from(LevelTransition)
            .join(Ticket, Ticket.id == LevelTransition.ticket_id)
            .where(
                LevelTransition.is_deescalation.is_(True),
                LevelTransition.performed_by_email == settings.tracked_agent_email,
                Ticket.is_tracked.is_(True),
            )
        )
    ).scalar_one()

    return {
        "open": by_status.get("OPEN", 0),
        "pending": by_status.get("PENDING", 0),
        "closed": by_status.get("CLOSED", 0),
        "rejected": by_status.get("REJECTED", 0),
        "blocked": by_status.get("BLOCKED", 0),
        "escalated": escalated,
        "unassigned": unassigned,
        "closed_today": today_snapshot["closed_today"],
        "fresh_closed_today": today_snapshot["fresh_closed_today"],
        "reclosed_today": today_snapshot["reclosed_today"],
        "reopened_today": today_snapshot["reopened_today"],
        "customer_reopened_today": today_snapshot["customer_reopened_today"],
        "needs_attention": needs_attention,
        "taken_from_me": taken_from_me,
        "self_released": self_released,
        "escalated_count": escalated_count,
        "deescalated_count": deescalated_count,
    }


@router.get("/tickets", response_model=TicketListResponse)
async def list_tickets(
    status: str | None = None,
    level: str | None = None,
    derived_type: str | None = None,
    tag: str | None = None,
    assigned_to: str | None = None,
    search: str | None = None,
    needs_attention: bool | None = None,
    closed_today: bool | None = None,
    taken_from_me: bool | None = None,
    self_released: bool | None = None,
    escalated: bool | None = None,
    deescalated: bool | None = None,
    sort: str = "last_event_at:desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
):
    stmt = select(Ticket).where(Ticket.is_tracked.is_(True))
    if status:
        stmt = stmt.where(Ticket.status == status)
    if level:
        stmt = stmt.where(Ticket.level == level)
    if derived_type:
        stmt = stmt.where(Ticket.derived_type == derived_type)
    if tag:
        stmt = stmt.join(TicketTag, TicketTag.ticket_id == Ticket.id).where(TicketTag.tag_id == tag)
    if assigned_to == "unassigned":
        stmt = stmt.where(or_(Ticket.assigned_to_email.is_(None), Ticket.assigned_to_email == ""))
    elif assigned_to:
        stmt = stmt.where(Ticket.assigned_to_email == assigned_to)
    if search:
        like = f"%{search}%"
        num_match = Ticket.num == int(search) if search.isdigit() else False
        full_name = func.coalesce(Customer.first_name, "").concat(" ").concat(func.coalesce(Customer.last_name, ""))
        stmt = stmt.outerjoin(Customer, Ticket.customer_id == Customer.id).where(
            or_(Customer.first_name.ilike(like), Customer.last_name.ilike(like), Customer.email.ilike(like), full_name.ilike(like), num_match)
        )

    if closed_today:
        closed_ids = await compute_actually_closed_today_ids(session, settings)
        stmt = stmt.where(Ticket.id.in_(closed_ids or {"__none__"}))

    if needs_attention:
        na_ids = await compute_needs_attention_ids(session, settings)
        stmt = stmt.where(Ticket.id.in_(na_ids or {"__none__"}))

    if taken_from_me:
        taken_ids = (
            (
                await session.execute(
                    select(AssignmentEvent.ticket_id).where(AssignmentEvent.is_taken_from_tracked_agent.is_(True)).distinct()
                )
            )
            .scalars()
            .all()
        )
        stmt = stmt.where(Ticket.id.in_(taken_ids or ["__none__"]))

    if self_released:
        released_ids = (
            (
                await session.execute(
                    select(AssignmentEvent.ticket_id).where(AssignmentEvent.is_self_release_for_tracked_agent.is_(True)).distinct()
                )
            )
            .scalars()
            .all()
        )
        stmt = stmt.where(Ticket.id.in_(released_ids or ["__none__"]))

    if escalated:
        escalated_ids = (
            (
                await session.execute(
                    select(LevelTransition.ticket_id)
                    .where(LevelTransition.is_escalation.is_(True), LevelTransition.performed_by_email == settings.tracked_agent_email)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        stmt = stmt.where(Ticket.id.in_(escalated_ids or ["__none__"]))

    if deescalated:
        deescalated_ids = (
            (
                await session.execute(
                    select(LevelTransition.ticket_id)
                    .where(LevelTransition.is_deescalation.is_(True), LevelTransition.performed_by_email == settings.tracked_agent_email)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        stmt = stmt.where(Ticket.id.in_(deescalated_ids or ["__none__"]))

    sort_field, _, sort_dir = sort.partition(":")
    col = SORTABLE.get(sort_field, Ticket.last_event_at)
    stmt = stmt.order_by(col.asc() if sort_dir == "asc" else col.desc())

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    tickets = (await session.execute(stmt)).scalars().all()

    flags = await compute_ticket_flags(session, [t.id for t in tickets])
    assignment_flags = await compute_assignment_flags(session, [t.id for t in tickets])
    self_release_flags = await compute_self_release_flags(session, [t.id for t in tickets])
    level_flags = await compute_level_flags(session, [t.id for t in tickets], settings)
    na_ids = await compute_needs_attention_ids(session, settings) if not needs_attention else na_ids

    items = []
    for t in tickets:
        customer = None
        if t.customer_id:
            from app.models import Customer

            c = await session.get(Customer, t.customer_id)
            if c:
                customer = c
        f = flags.get(t.id, {})
        af = assignment_flags.get(t.id, {})
        sf = self_release_flags.get(t.id, {})
        lf = level_flags.get(t.id, {})
        items.append(
            TicketListItem(
                id=t.id,
                num=t.num,
                subject=t.subject,
                status=t.status,
                level=t.level,
                tags_cache=t.tags_cache,
                derived_type=t.derived_type,
                assigned_to_email=t.assigned_to_email,
                customer=customer,
                last_event_at=t.last_event_at,
                last_customer_message_at=t.last_customer_message_at,
                created_at_trinity=t.created_at_trinity,
                trinity_url=t.trinity_url,
                overwatch_status=t.overwatch_status,
                reopen_count=f.get("reopen_count", 0),
                last_close_at=f.get("last_close_at"),
                last_reopen_at=f.get("last_reopen_at"),
                needs_attention=t.id in na_ids,
                taken_from_me_count=af.get("taken_from_me_count", 0),
                last_taken_from_me_at=af.get("last_taken_from_me_at"),
                last_taken_from_me_reason=af.get("last_taken_from_me_reason"),
                self_released_count=sf.get("self_released_count", 0),
                last_self_released_at=sf.get("last_self_released_at"),
                last_self_released_reason=sf.get("last_self_released_reason"),
                escalated_count=lf.get("escalated_count", 0),
                last_escalated_at=lf.get("last_escalated_at"),
                deescalated_count=lf.get("deescalated_count", 0),
                last_deescalated_at=lf.get("last_deescalated_at"),
                last_level_change_reason=lf.get("last_level_change_reason"),
            )
        )

    return TicketListResponse(items=items, total=total, page=page, page_size=page_size)


async def load_ticket_detail(session: AsyncSession, ticket_id: str, note_author_email: str) -> TicketDetailOut | None:
    """Shared by the personal `/tickets/{id}` endpoint and Shift Watch's
    `/roster/tickets/{id}` endpoint - identical in every respect except
    which agent's internal note counts as "the" last internal note
    (`get_last_own_internal_note` is already parameterized by email, not
    hardcoded to the tracked agent)."""
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        return None

    customer = None
    if ticket.customer_id:
        from app.models import Customer

        customer = await session.get(Customer, ticket.customer_id)

    transitions = (
        (
            await session.execute(
                select(StatusTransition).where(StatusTransition.ticket_id == ticket_id).order_by(StatusTransition.seq)
            )
        )
        .scalars()
        .all()
    )
    csats = (
        (await session.execute(select(CsatEvent).where(CsatEvent.ticket_id == ticket_id).order_by(CsatEvent.created_at)))
        .scalars()
        .all()
    )
    assignment_events = (
        (
            await session.execute(
                select(AssignmentEvent).where(AssignmentEvent.ticket_id == ticket_id).order_by(AssignmentEvent.seq)
            )
        )
        .scalars()
        .all()
    )
    level_transitions = (
        (await session.execute(select(LevelTransition).where(LevelTransition.ticket_id == ticket_id).order_by(LevelTransition.seq)))
        .scalars()
        .all()
    )
    duplicates = (
        (await session.execute(select(TicketDuplicate).where(TicketDuplicate.ticket_id == ticket_id)))
        .scalars()
        .all()
    )
    local_notes = (
        (
            await session.execute(
                select(LocalNote)
                .where(LocalNote.ticket_id == ticket_id, LocalNote.is_deleted.is_(False))
                .order_by(LocalNote.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    last_note = await get_last_own_internal_note(session, ticket_id, note_author_email)

    return TicketDetailOut(
        id=ticket.id,
        num=ticket.num,
        subject=ticket.subject,
        status=ticket.status,
        level=ticket.level,
        channel=ticket.channel,
        source=ticket.source,
        team=ticket.team,
        tags_cache=ticket.tags_cache,
        derived_type=ticket.derived_type,
        assigned_to_email=ticket.assigned_to_email,
        customer=customer,
        overwatch_status=ticket.overwatch_status,
        ticket_custom_fields=ticket.ticket_custom_fields,
        thread_total_events=ticket.thread_total_events,
        thread_messages=ticket.thread_messages,
        thread_notes=ticket.thread_notes,
        created_at_trinity=ticket.created_at_trinity,
        updated_at_trinity=ticket.updated_at_trinity,
        last_customer_message_at=ticket.last_customer_message_at,
        last_event_at=ticket.last_event_at,
        first_assigned_to_agent_at=ticket.first_assigned_to_agent_at,
        added_to_tracker_at=ticket.added_to_tracker_at,
        trinity_url=ticket.trinity_url,
        status_transitions=transitions,
        assignment_events=assignment_events,
        level_transitions=level_transitions,
        csat_events=csats,
        duplicates=duplicates,
        last_trinity_internal_note=last_note,
        local_notes=local_notes,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketDetailOut)
async def get_ticket_detail(
    ticket_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
):
    detail = await load_ticket_detail(session, ticket_id, settings.tracked_agent_email)
    if detail is None:
        raise HTTPException(404, "Ticket not tracked")
    return detail


@router.post("/tickets/by-number", response_model=TicketDetailOut)
async def add_ticket_by_number_route(
    body: AddTicketRequest,
    session: AsyncSession = Depends(get_session),
    client: TrinityClient = Depends(get_trinity_client),
    settings: Settings = Depends(get_app_settings),
):
    try:
        await add_ticket_by_number(client, session, settings, body.num)
    except TicketNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return await get_ticket_detail(str((await session.execute(select(Ticket.id).where(Ticket.num == body.num))).scalar_one()), session, settings)


@router.get("/tickets/{ticket_id}/comments", response_model=list[LocalNoteOut])
async def list_comments(ticket_id: str, session: AsyncSession = Depends(get_session)):
    rows = (
        (
            await session.execute(
                select(LocalNote)
                .where(LocalNote.ticket_id == ticket_id, LocalNote.is_deleted.is_(False))
                .order_by(LocalNote.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post("/tickets/{ticket_id}/comments", response_model=LocalNoteOut)
async def create_comment(
    ticket_id: str,
    body: LocalNoteCreate,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
):
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not tracked")
    now = utcnow()
    note = LocalNote(
        ticket_id=ticket_id, agent_email=settings.tracked_agent_email, body=body.body, created_at=now, updated_at=now
    )
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return note


@router.patch("/comments/{comment_id}", response_model=LocalNoteOut)
async def update_comment(comment_id: int, body: LocalNoteUpdate, session: AsyncSession = Depends(get_session)):
    note = await session.get(LocalNote, comment_id)
    if note is None or note.is_deleted:
        raise HTTPException(404, "Comment not found")
    note.body = body.body
    note.updated_at = utcnow()
    await session.commit()
    await session.refresh(note)
    return note


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment(comment_id: int, session: AsyncSession = Depends(get_session)):
    note = await session.get(LocalNote, comment_id)
    if note is None:
        raise HTTPException(404, "Comment not found")
    note.is_deleted = True
    note.updated_at = utcnow()
    await session.commit()


@router.get("/queue/needs-attention", response_model=TicketListResponse)
async def needs_attention_queue(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_app_settings)
):
    return await list_tickets(
        needs_attention=True,
        sort="last_event_at:asc",
        page=1,
        page_size=200,
        session=session,
        settings=settings,
    )


@router.get("/export/tickets.csv")
async def export_tickets_csv(session: AsyncSession = Depends(get_session)):
    tickets = (
        await session.execute(select(Ticket).where(Ticket.is_tracked.is_(True)).order_by(Ticket.num))
    ).scalars().all()
    flags = await compute_ticket_flags(session, [t.id for t in tickets])

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["num", "subject", "status", "level", "derived_type", "assigned_to", "reopen_count", "last_close_at", "created_at"]
    )
    for t in tickets:
        f = flags.get(t.id, {})
        writer.writerow(
            [
                t.num,
                t.subject,
                t.status,
                t.level,
                t.derived_type,
                t.assigned_to_email,
                f.get("reopen_count", 0),
                f.get("last_close_at"),
                t.created_at_trinity,
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tickets.csv"},
    )
