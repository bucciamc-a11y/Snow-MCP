"""Input validation and encoded-query construction.

Every value that reaches a ServiceNow ``sysparm_query`` passes through here and
is constrained to a closed vocabulary or a strict pattern. Free text is limited
to a conservative character set so it cannot inject ``^`` (clause separator),
``^OR``/``^NQ`` operators or other encoded-query syntax.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from .config import CHANGE_STATE_NAMES, PROBLEM_STATE_NAMES

SYS_ID_RE = re.compile(r"^[0-9a-f]{32}\Z")
INCIDENT_NUMBER_RE = re.compile(r"^INC\d{7,10}\Z")
CHANGE_NUMBER_RE = re.compile(r"^CHG\d{7,10}\Z")
PROBLEM_NUMBER_RE = re.compile(r"^PRB\d{7,10}\Z")
_CATEGORY_RE = re.compile(r"^[A-Za-z0-9 _-]{1,40}\Z")
# Letters, digits, space and a few harmless punctuation marks. No ^, no quotes,
# no control characters, no encoded-query metacharacters.
_TEXT_RE = re.compile(r"^[\w .,:;/@#()+_-]{1,100}\Z", re.UNICODE)
_DATETIME_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ")

INCIDENT_STATES = {
    "new": "1",
    "in_progress": "2",
    "on_hold": "3",
    "resolved": "6",
    "closed": "7",
    "canceled": "8",
}
ORDER_FIELDS = {
    "opened_at": "opened_at",
    "updated_at": "sys_updated_on",
    "priority": "priority",
    "number": "number",
}


class ValidationError(ValueError):
    """Input rejected. The message is safe to return to the agent."""


_NUMBER_EXAMPLES = {
    "incident": "an incident number such as INC0010001",
    "change": "a change number such as CHG0030001",
    "problem": "a problem number such as PRB0010001",
}


def validate_identifier(value: str, number_re: re.Pattern[str], label: str) -> tuple[str, str]:
    """Return ("sys_id"|"number", normalised value)."""
    if not isinstance(value, str):
        raise ValidationError(f"{label} identifier must be a string")
    candidate = value.strip()
    if SYS_ID_RE.match(candidate.lower()):
        return "sys_id", candidate.lower()
    if number_re.match(candidate.upper()):
        return "number", candidate.upper()
    example = _NUMBER_EXAMPLES.get(label, "a record number")
    raise ValidationError(f"identifier must be a 32-character hex sys_id or {example}")


def _text(value: str, name: str) -> str:
    value = value.strip()
    if not _TEXT_RE.match(value):
        raise ValidationError(
            f"{name} must be 1-100 characters: letters, digits, spaces and . , : ; / @ # ( ) + _ -"
        )
    return value


def _datetime(value: str, name: str) -> str:
    value = value.strip()
    for fmt in _DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    raise ValidationError(f"{name} must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS (UTC)")


def _int_in(value: int, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer")
    if not lo <= value <= hi:
        raise ValidationError(f"{name} must be between {lo} and {hi}")
    return value


@dataclass(frozen=True)
class IncidentSearch:
    query: str
    limit: int
    offset: int


def build_incident_search(
    *,
    state: str | None = None,
    active: bool | None = None,
    priority: int | None = None,
    urgency: int | None = None,
    impact: int | None = None,
    category: str | None = None,
    assignment_group: str | None = None,
    caller_id: str | None = None,
    unassigned: bool | None = None,
    short_description_contains: str | None = None,
    opened_after: str | None = None,
    opened_before: str | None = None,
    order_by: str = "opened_at",
    descending: bool = True,
    limit: int = 10,
    offset: int = 0,
    max_limit: int = 50,
) -> IncidentSearch:
    clauses: list[str] = []

    if state is not None:
        key = state.strip().lower()
        if key not in INCIDENT_STATES:
            raise ValidationError(f"state must be one of: {', '.join(INCIDENT_STATES)}")
        clauses.append(f"state={INCIDENT_STATES[key]}")
    if active is not None:
        if not isinstance(active, bool):
            raise ValidationError("active must be a boolean")
        clauses.append(f"active={'true' if active else 'false'}")
    for name, val in (("priority", priority), ("urgency", urgency), ("impact", impact)):
        if val is not None:
            clauses.append(f"{name}={_int_in(val, name, 1, 5 if name == 'priority' else 3)}")
    if category is not None:
        category = category.strip()
        if not _CATEGORY_RE.match(category):
            raise ValidationError("category must be 1-40 letters, digits, spaces, _ or -")
        clauses.append(f"category={category}")
    for name, val in (("assignment_group", assignment_group), ("caller_id", caller_id)):
        if val is not None:
            val = val.strip().lower()
            if not SYS_ID_RE.match(val):
                raise ValidationError(f"{name} must be a 32-character hex sys_id")
            clauses.append(f"{name}={val}")
    if unassigned is not None:
        if not isinstance(unassigned, bool):
            raise ValidationError("unassigned must be a boolean")
        clauses.append("assignment_groupISEMPTY" if unassigned else "assignment_groupISNOTEMPTY")
    if short_description_contains is not None:
        clauses.append(
            f"short_descriptionLIKE{_text(short_description_contains, 'short_description_contains')}"
        )
    if opened_after is not None:
        clauses.append(f"opened_at>={_datetime(opened_after, 'opened_after')}")
    if opened_before is not None:
        clauses.append(f"opened_at<={_datetime(opened_before, 'opened_before')}")

    if order_by not in ORDER_FIELDS:
        raise ValidationError(f"order_by must be one of: {', '.join(ORDER_FIELDS)}")
    if not isinstance(descending, bool):
        raise ValidationError("descending must be a boolean")
    clauses.append(f"{'ORDERBYDESC' if descending else 'ORDERBY'}{ORDER_FIELDS[order_by]}")

    return IncidentSearch(
        query="^".join(clauses),
        limit=_int_in(limit, "limit", 1, max_limit),
        offset=_int_in(offset, "offset", 0, 10000),
    )



# ---------------------------------------------------------------------------
# Write-side validation. Values here travel in a JSON request body (never in an
# encoded query), but are still constrained to closed vocabularies / strict patterns.
# ---------------------------------------------------------------------------
_CTL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]")
_CLOSE_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,/_()-]{0,59}\Z")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")

WRITE_STATES = {"in_progress": "2"}  # resolution goes through resolve_incident; closing/cancelling is not offered
BLOCKED_UPDATE_STATES = {"7", "8"}  # closed, canceled
BLOCKED_RESOLVE_STATES = {"6", "7", "8"}


def validate_text(value: str, name: str, max_len: int, *, required: bool = False) -> str:
    """Multi-line plain text: no control or bidirectional-override characters."""
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    text = value.replace("\r\n", "\n").strip()
    if required and not text:
        raise ValidationError(f"{name} is required")
    if len(text) > max_len:
        raise ValidationError(f"{name} must be at most {max_len} characters")
    if _CTL_RE.search(text):
        raise ValidationError(f"{name} contains control characters")
    return text


def _sys_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not SYS_ID_RE.match(value.strip().lower()):
        raise ValidationError(f"{name} must be a 32-character hex sys_id")
    return value.strip().lower()


def validate_sys_id(value: str, name: str) -> str:
    return _sys_id(value, name)


def build_incident_fields(
    *,
    short_description: str | None = None,
    description: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    urgency: int | None = None,
    impact: int | None = None,
    caller_id: str | None = None,
    assignment_group: str | None = None,
    assigned_to: str | None = None,
    business_service: str | None = None,
    cmdb_ci: str | None = None,
    state: str | None = None,
    work_note: str | None = None,
    comment: str | None = None,
    require_short_description: bool = False,
    max_text: int = 4000,
) -> dict[str, str]:
    """Validate and translate incident inputs to ServiceNow field names/raw values."""
    out: dict[str, str] = {}
    if short_description is not None:
        value = validate_text(short_description, "short_description", 160, required=True)
        if "\n" in value:
            raise ValidationError("short_description must be a single line")
        out["short_description"] = value
    elif require_short_description:
        raise ValidationError("short_description is required")
    if description is not None:
        out["description"] = validate_text(description, "description", max_text)
    for name, val in (("category", category), ("subcategory", subcategory)):
        if val is not None:
            val = val.strip()
            if not _CATEGORY_RE.match(val):
                raise ValidationError(f"{name} must be 1-40 letters, digits, spaces, _ or -")
            out[name] = val
    for name, val in (("urgency", urgency), ("impact", impact)):
        if val is not None:
            out[name] = str(_int_in(val, name, 1, 3))
    for name, val in (
        ("caller_id", caller_id), ("assignment_group", assignment_group), ("assigned_to", assigned_to),
        ("business_service", business_service), ("cmdb_ci", cmdb_ci),
    ):
        if val is not None:
            out[name] = _sys_id(val, name)
    if state is not None:
        key = state.strip().lower() if isinstance(state, str) else ""
        if key not in WRITE_STATES:
            raise ValidationError(f"state may only be set to: {', '.join(WRITE_STATES)} (use resolve_incident to resolve)")
        out["state"] = WRITE_STATES[key]
    if work_note is not None:
        out["work_notes"] = validate_text(work_note, "work_note", max_text, required=True)
    if comment is not None:
        out["comments"] = validate_text(comment, "comment", max_text, required=True)
    return out


def build_incident_resolution(close_code: str, close_notes: str, max_text: int = 4000) -> dict[str, str]:
    if not isinstance(close_code, str) or not _CLOSE_CODE_RE.match(close_code.strip()):
        raise ValidationError("close_code must be 1-60 characters: letters, digits, spaces and , / _ ( ) -")
    return {
        "state": "6",
        "close_code": close_code.strip(),
        "close_notes": validate_text(close_notes, "close_notes", max_text, required=True),
    }


def validate_approval_comment(comments: str | None) -> str | None:
    if comments is None:
        return None
    return validate_text(comments, "comments", 1000, required=True)


def validate_date(value: str, name: str) -> str:
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        raise ValidationError(f"{name} must be YYYY-MM-DD")
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        raise ValidationError(f"{name} is not a valid date") from None
    return value.strip()


def build_oncall_member_fields(
    *,
    roster: str | None = None,
    member: str | None = None,
    order: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    if roster is not None:
        out["roster"] = _sys_id(roster, "roster_id")
    if member is not None:
        out["member"] = _sys_id(member, "user_id")
    if order is not None:
        out["order"] = str(_int_in(order, "order", 0, 1000))
    if from_date is not None:
        out["from"] = validate_date(from_date, "from_date")
    if to_date is not None:
        out["to"] = validate_date(to_date, "to_date")
    if "from" in out and "to" in out and out["to"] < out["from"]:
        raise ValidationError("to_date must not be before from_date")
    return out


# ---------------------------------------------------------------------------
# Problem management
# ---------------------------------------------------------------------------
# States an agent may move a problem into with update_problem. Resolving goes through resolve_problem;
# closing/cancelling is not offered at all.
PROBLEM_UPDATE_STATES = ("assess", "root_cause_analysis", "fix_in_progress")


@dataclass(frozen=True)
class ProblemSearch:
    query: str
    limit: int
    offset: int


def build_problem_search(
    *,
    state_map: Mapping[str, str],
    state: str | None = None,
    active: bool | None = None,
    known_error: bool | None = None,
    unassigned: bool | None = None,
    priority: int | None = None,
    urgency: int | None = None,
    impact: int | None = None,
    category: str | None = None,
    assignment_group: str | None = None,
    cmdb_ci: str | None = None,
    short_description_contains: str | None = None,
    opened_after: str | None = None,
    opened_before: str | None = None,
    order_by: str = "opened_at",
    descending: bool = True,
    limit: int = 10,
    offset: int = 0,
    max_limit: int = 50,
) -> ProblemSearch:
    clauses: list[str] = []

    if state is not None:
        key = state.strip().lower() if isinstance(state, str) else ""
        if key not in PROBLEM_STATE_NAMES:
            raise ValidationError(f"state must be one of: {', '.join(PROBLEM_STATE_NAMES)}")
        clauses.append(f"state={state_map[key]}")
    for name, flag in (("active", active), ("known_error", known_error)):
        if flag is not None:
            if not isinstance(flag, bool):
                raise ValidationError(f"{name} must be a boolean")
            clauses.append(f"{name}={'true' if flag else 'false'}")
    for name, val in (("priority", priority), ("urgency", urgency), ("impact", impact)):
        if val is not None:
            clauses.append(f"{name}={_int_in(val, name, 1, 5 if name == 'priority' else 3)}")
    if category is not None:
        category = category.strip()
        if not _CATEGORY_RE.match(category):
            raise ValidationError("category must be 1-40 letters, digits, spaces, _ or -")
        clauses.append(f"category={category}")
    for name, val in (("assignment_group", assignment_group), ("cmdb_ci", cmdb_ci)):
        if val is not None:
            val = val.strip().lower()
            if not SYS_ID_RE.match(val):
                raise ValidationError(f"{name} must be a 32-character hex sys_id")
            clauses.append(f"{name}={val}")
    if unassigned is not None:
        if not isinstance(unassigned, bool):
            raise ValidationError("unassigned must be a boolean")
        clauses.append("assignment_groupISEMPTY" if unassigned else "assignment_groupISNOTEMPTY")
    if short_description_contains is not None:
        clauses.append(
            f"short_descriptionLIKE{_text(short_description_contains, 'short_description_contains')}"
        )
    if opened_after is not None:
        clauses.append(f"opened_at>={_datetime(opened_after, 'opened_after')}")
    if opened_before is not None:
        clauses.append(f"opened_at<={_datetime(opened_before, 'opened_before')}")

    if order_by not in ORDER_FIELDS:
        raise ValidationError(f"order_by must be one of: {', '.join(ORDER_FIELDS)}")
    if not isinstance(descending, bool):
        raise ValidationError("descending must be a boolean")
    clauses.append(f"{'ORDERBYDESC' if descending else 'ORDERBY'}{ORDER_FIELDS[order_by]}")

    return ProblemSearch(
        query="^".join(clauses),
        limit=_int_in(limit, "limit", 1, max_limit),
        offset=_int_in(offset, "offset", 0, 10000),
    )


def build_problem_fields(
    *,
    state_map: Mapping[str, str],
    short_description: str | None = None,
    description: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    urgency: int | None = None,
    impact: int | None = None,
    cmdb_ci: str | None = None,
    business_service: str | None = None,
    assignment_group: str | None = None,
    state: str | None = None,
    cause_notes: str | None = None,
    workaround: str | None = None,
    fix_notes: str | None = None,
    known_error: bool | None = None,
    work_note: str | None = None,
    comment: str | None = None,
    require_short_description: bool = False,
    max_text: int = 4000,
) -> dict[str, str]:
    """Validate and translate problem inputs to ServiceNow field names/raw values."""
    out: dict[str, str] = {}
    if short_description is not None:
        value = validate_text(short_description, "short_description", 160, required=True)
        if "\n" in value:
            raise ValidationError("short_description must be a single line")
        out["short_description"] = value
    elif require_short_description:
        raise ValidationError("short_description is required")
    if description is not None:
        out["description"] = validate_text(description, "description", max_text)
    for name, val in (("category", category), ("subcategory", subcategory)):
        if val is not None:
            val = val.strip()
            if not _CATEGORY_RE.match(val):
                raise ValidationError(f"{name} must be 1-40 letters, digits, spaces, _ or -")
            out[name] = val
    for name, val in (("urgency", urgency), ("impact", impact)):
        if val is not None:
            out[name] = str(_int_in(val, name, 1, 3))
    for name, val in (
        ("cmdb_ci", cmdb_ci), ("business_service", business_service), ("assignment_group", assignment_group),
    ):
        if val is not None:
            out[name] = _sys_id(val, name)
    if state is not None:
        key = state.strip().lower() if isinstance(state, str) else ""
        if key not in PROBLEM_UPDATE_STATES:
            raise ValidationError(
                f"state may only be set to: {', '.join(PROBLEM_UPDATE_STATES)} (use resolve_problem to resolve)"
            )
        out["state"] = state_map[key]
    for name, val in (("cause_notes", cause_notes), ("workaround", workaround), ("fix_notes", fix_notes)):
        if val is not None:
            out[name] = validate_text(val, name, max_text, required=True)
    if known_error is not None:
        if not isinstance(known_error, bool):
            raise ValidationError("known_error must be a boolean")
        out["known_error"] = "true" if known_error else "false"
    if work_note is not None:
        out["work_notes"] = validate_text(work_note, "work_note", max_text, required=True)
    if comment is not None:
        out["comments"] = validate_text(comment, "comment", max_text, required=True)
    return out


def build_problem_resolution(
    cause_notes: str,
    fix_notes: str,
    resolved_value: str,
    resolution_code: str | None = None,
    max_text: int = 4000,
) -> dict[str, str]:
    out = {
        "state": resolved_value,
        "cause_notes": validate_text(cause_notes, "cause_notes", max_text, required=True),
        "fix_notes": validate_text(fix_notes, "fix_notes", max_text, required=True),
    }
    if resolution_code is not None:
        if not isinstance(resolution_code, str) or not _CLOSE_CODE_RE.match(resolution_code.strip()):
            raise ValidationError(
                "resolution_code must be 1-60 characters: letters, digits, spaces and , / _ ( ) -"
            )
        out["resolution_code"] = resolution_code.strip()
    return out


# ---------------------------------------------------------------------------
# Change management
# ---------------------------------------------------------------------------
# Lifecycle phases in forward order. transition_change_request may only move a change to a LATER phase in this
# list; closing and cancelling have their own tools, and nothing moves a change backwards.
CHANGE_PHASES = ("new", "assess", "authorize", "scheduled", "implement", "review")
CHANGE_TRANSITION_TARGETS = CHANGE_PHASES[1:]
CHANGE_TYPES = ("normal", "standard", "emergency")
CHANGE_ORDER_FIELDS = {**ORDER_FIELDS, "start_date": "start_date"}


@dataclass(frozen=True)
class ChangeSearch:
    query: str
    limit: int
    offset: int


def build_change_search(
    *,
    state_map: Mapping[str, str],
    state: str | None = None,
    active: bool | None = None,
    change_type: str | None = None,
    priority: int | None = None,
    category: str | None = None,
    assignment_group: str | None = None,
    cmdb_ci: str | None = None,
    short_description_contains: str | None = None,
    opened_after: str | None = None,
    opened_before: str | None = None,
    order_by: str = "opened_at",
    descending: bool = True,
    limit: int = 10,
    offset: int = 0,
    max_limit: int = 50,
) -> ChangeSearch:
    clauses: list[str] = []
    if state is not None:
        key = state.strip().lower() if isinstance(state, str) else ""
        if key not in CHANGE_STATE_NAMES:
            raise ValidationError(f"state must be one of: {', '.join(CHANGE_STATE_NAMES)}")
        clauses.append(f"state={state_map[key]}")
    if active is not None:
        if not isinstance(active, bool):
            raise ValidationError("active must be a boolean")
        clauses.append(f"active={'true' if active else 'false'}")
    if change_type is not None:
        key = change_type.strip().lower() if isinstance(change_type, str) else ""
        if key not in CHANGE_TYPES:
            raise ValidationError(f"type must be one of: {', '.join(CHANGE_TYPES)}")
        clauses.append(f"type={key}")
    if priority is not None:
        clauses.append(f"priority={_int_in(priority, 'priority', 1, 5)}")
    if category is not None:
        category = category.strip()
        if not _CATEGORY_RE.match(category):
            raise ValidationError("category must be 1-40 letters, digits, spaces, _ or -")
        clauses.append(f"category={category}")
    for name, val in (("assignment_group", assignment_group), ("cmdb_ci", cmdb_ci)):
        if val is not None:
            val = val.strip().lower()
            if not SYS_ID_RE.match(val):
                raise ValidationError(f"{name} must be a 32-character hex sys_id")
            clauses.append(f"{name}={val}")
    if short_description_contains is not None:
        clauses.append(
            f"short_descriptionLIKE{_text(short_description_contains, 'short_description_contains')}"
        )
    if opened_after is not None:
        clauses.append(f"opened_at>={_datetime(opened_after, 'opened_after')}")
    if opened_before is not None:
        clauses.append(f"opened_at<={_datetime(opened_before, 'opened_before')}")
    if order_by not in CHANGE_ORDER_FIELDS:
        raise ValidationError(f"order_by must be one of: {', '.join(CHANGE_ORDER_FIELDS)}")
    if not isinstance(descending, bool):
        raise ValidationError("descending must be a boolean")
    clauses.append(f"{'ORDERBYDESC' if descending else 'ORDERBY'}{CHANGE_ORDER_FIELDS[order_by]}")
    return ChangeSearch(
        query="^".join(clauses),
        limit=_int_in(limit, "limit", 1, max_limit),
        offset=_int_in(offset, "offset", 0, 10000),
    )


def build_change_fields(
    *,
    allowed_types: tuple[str, ...] = (),
    short_description: str | None = None,
    description: str | None = None,
    change_type: str | None = None,
    category: str | None = None,
    impact: int | None = None,
    cmdb_ci: str | None = None,
    business_service: str | None = None,
    assignment_group: str | None = None,
    justification: str | None = None,
    implementation_plan: str | None = None,
    backout_plan: str | None = None,
    test_plan: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    work_note: str | None = None,
    comment: str | None = None,
    require_short_description: bool = False,
    max_text: int = 4000,
) -> dict[str, str]:
    """Validate and translate change inputs to ServiceNow field names/raw values (never the state)."""
    out: dict[str, str] = {}
    if short_description is not None:
        value = validate_text(short_description, "short_description", 160, required=True)
        if "\n" in value:
            raise ValidationError("short_description must be a single line")
        out["short_description"] = value
    elif require_short_description:
        raise ValidationError("short_description is required")
    if change_type is not None:
        key = change_type.strip().lower() if isinstance(change_type, str) else ""
        if key not in CHANGE_TYPES:
            raise ValidationError(f"type must be one of: {', '.join(CHANGE_TYPES)}")
        if key not in allowed_types:
            raise ValidationError(
                "this server only lets agents create changes of type: " + (", ".join(allowed_types) or "(none)")
            )
        out["type"] = key
    for name, val in (
        ("description", description), ("justification", justification),
        ("implementation_plan", implementation_plan), ("backout_plan", backout_plan), ("test_plan", test_plan),
    ):
        if val is not None:
            out[name] = validate_text(val, name, max_text)
    if category is not None:
        category = category.strip()
        if not _CATEGORY_RE.match(category):
            raise ValidationError("category must be 1-40 letters, digits, spaces, _ or -")
        out["category"] = category
    if impact is not None:
        out["impact"] = str(_int_in(impact, "impact", 1, 3))
    for name, val in (("cmdb_ci", cmdb_ci), ("business_service", business_service),
                      ("assignment_group", assignment_group)):
        if val is not None:
            out[name] = _sys_id(val, name)
    if start_date is not None:
        out["start_date"] = _datetime(start_date, "start_date")
    if end_date is not None:
        out["end_date"] = _datetime(end_date, "end_date")
    if "start_date" in out and "end_date" in out and out["end_date"] < out["start_date"]:
        raise ValidationError("end_date must not be before start_date")
    if work_note is not None:
        out["work_notes"] = validate_text(work_note, "work_note", max_text, required=True)
    if comment is not None:
        out["comments"] = validate_text(comment, "comment", max_text, required=True)
    return out


def build_change_transition(target: str, state_map: Mapping[str, str], work_note: str | None = None,
                            max_text: int = 4000) -> dict[str, str]:
    key = target.strip().lower() if isinstance(target, str) else ""
    if key not in CHANGE_TRANSITION_TARGETS:
        raise ValidationError(
            f"to_phase must be one of: {', '.join(CHANGE_TRANSITION_TARGETS)} "
            "(use close_change_request / cancel_change_request to finish a change)"
        )
    out = {"state": state_map[key]}
    if work_note is not None:
        out["work_notes"] = validate_text(work_note, "work_note", max_text, required=True)
    return out


def build_change_close(close_code: str, close_notes: str, closed_value: str, max_text: int = 4000) -> dict[str, str]:
    if not isinstance(close_code, str) or not _CLOSE_CODE_RE.match(close_code.strip()):
        raise ValidationError("close_code must be 1-60 characters: letters, digits, spaces and , / _ ( ) -")
    return {
        "state": closed_value,
        "close_code": close_code.strip(),
        "close_notes": validate_text(close_notes, "close_notes", max_text, required=True),
    }


def build_change_cancel(reason: str, canceled_value: str, max_text: int = 4000) -> dict[str, str]:
    return {"state": canceled_value, "work_notes": validate_text(reason, "reason", max_text, required=True)}
