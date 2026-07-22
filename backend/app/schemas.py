from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class CustomerOut(BaseModel):
    id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    custom_fields: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class StatusTransitionOut(BaseModel):
    id: int
    seq: int
    old_status: str | None
    new_status: str | None
    is_close: bool
    is_reopen: bool
    is_customer_triggered_reopen: bool
    event_date: date
    agent_email: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssignmentEventOut(BaseModel):
    id: int
    seq: int
    action: str
    old_assignee: str | None
    new_assignee: str | None
    is_gain_for_tracked_agent: bool
    is_taken_from_tracked_agent: bool
    is_self_release_for_tracked_agent: bool
    is_system_action: bool
    reason: str | None
    performed_by_email: str | None
    event_date: date
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LevelTransitionOut(BaseModel):
    id: int
    seq: int
    old_level: str | None
    new_level: str | None
    is_escalation: bool
    is_deescalation: bool
    is_system_action: bool
    performed_by_email: str | None
    possible_reason: str | None
    event_date: date
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CsatEventOut(BaseModel):
    id: int
    action: str
    close_cycle_index: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketDuplicateOut(BaseModel):
    id: int
    duplicate_of_num: int
    detected_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LocalNoteOut(BaseModel):
    id: int
    ticket_id: str
    agent_email: str
    body: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LocalNoteCreate(BaseModel):
    body: str


class LocalNoteUpdate(BaseModel):
    body: str


class TicketListItem(BaseModel):
    id: str
    num: int
    subject: str | None
    status: str
    level: str | None
    tags_cache: list[str] | None
    derived_type: str | None
    assigned_to_email: str | None
    customer: CustomerOut | None
    last_event_at: datetime
    last_customer_message_at: datetime | None
    created_at_trinity: datetime
    trinity_url: str | None
    overwatch_status: str | None
    reopen_count: int = 0
    last_close_at: datetime | None = None
    last_reopen_at: datetime | None = None
    needs_attention: bool = False
    taken_from_me_count: int = 0
    last_taken_from_me_at: datetime | None = None
    last_taken_from_me_reason: str | None = None
    self_released_count: int = 0
    last_self_released_at: datetime | None = None
    last_self_released_reason: str | None = None
    escalated_count: int = 0
    last_escalated_at: datetime | None = None
    deescalated_count: int = 0
    last_deescalated_at: datetime | None = None
    last_level_change_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)


class TicketListResponse(BaseModel):
    items: list[TicketListItem]
    total: int
    page: int
    page_size: int


class TicketDetailOut(BaseModel):
    id: str
    num: int
    subject: str | None
    status: str
    level: str | None
    channel: str | None
    source: str | None
    team: str | None
    tags_cache: list[str] | None
    derived_type: str | None
    assigned_to_email: str | None
    customer: CustomerOut | None
    overwatch_status: str | None
    ticket_custom_fields: dict[str, Any] | None
    thread_total_events: int | None
    thread_messages: int | None
    thread_notes: int | None
    created_at_trinity: datetime
    updated_at_trinity: datetime
    last_customer_message_at: datetime | None
    last_event_at: datetime
    first_assigned_to_agent_at: datetime | None
    added_to_tracker_at: datetime
    trinity_url: str | None
    status_transitions: list[StatusTransitionOut]
    assignment_events: list[AssignmentEventOut]
    level_transitions: list[LevelTransitionOut]
    csat_events: list[CsatEventOut]
    duplicates: list[TicketDuplicateOut]
    last_trinity_internal_note: str | None
    local_notes: list[LocalNoteOut]

    model_config = ConfigDict(from_attributes=True)


class AddTicketRequest(BaseModel):
    num: int


class SyncRunRequest(BaseModel):
    mode: str = "incremental"
    ticket_id: str | None = None


class SyncRunOut(BaseModel):
    id: int
    run_type: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    tickets_checked: int
    tickets_updated: int
    events_ingested: int
    error_summary: str | None

    model_config = ConfigDict(from_attributes=True)


class SyncStatusOut(BaseModel):
    last_full_backfill_at: datetime | None
    last_incremental_sync_at: datetime | None
    last_incremental_sync_status: str | None
    last_incremental_sync_error: str | None
    next_poll_at: datetime | None
    is_running: bool


class TagOut(BaseModel):
    tag_id: str
    label: str
    ticket_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class TagMappingOut(BaseModel):
    tag_id: str
    type_label: str
    priority: int

    model_config = ConfigDict(from_attributes=True)


class TagMappingUpsert(BaseModel):
    tag_id: str
    type_label: str
    priority: int = 100


class SettingOut(BaseModel):
    key: str
    value: Any


class SettingUpdate(BaseModel):
    value: Any


class AnalyticsSummaryBucket(BaseModel):
    bucket: str
    closed_count: int
    fresh_close_count: int
    reclose_count: int
    reopened_count: int
    customer_reopened_count: int
    avg_time_to_respond_minutes: float | None
    avg_time_to_final_close_minutes: float | None


class TimeseriesPoint(BaseModel):
    bucket: str
    value: float


class RosterUploadResult(BaseModel):
    agents: int
    shift_rows: int
    date_range: list[date | None]


class RosterAgentOut(BaseModel):
    email: str
    name: str
    role: str
    today_shift_code: str | None
    tomorrow_shift_code: str | None


class RosterShiftUpdateRequest(BaseModel):
    """Manual correction for a single roster cell - e.g. someone's shift
    changed since the last sheet upload and Yashwanth knows the new one.
    Targets today/tomorrow (the only two columns Shift Watch's roster table
    shows) rather than a raw date, so the frontend never has to reason about
    the reporting timezone itself."""

    which: Literal["today", "tomorrow"]
    shift_code: str


class RosterOverdueTicket(BaseModel):
    id: str
    num: int
    derived_type: str | None
    assigned_to_email: str
    agent_name: str
    agent_role: str
    is_associate_or_trainer: bool
    shift_code: str | None
    reason: str
    held_since: datetime | None
    last_event_at: datetime
    trinity_url: str | None
    alert_tags: list[str]
