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
class Search:
    """A validated encoded query plus its page window (shared by incident, problem and change searches)."""

    query: str
    limit: int
    offset: int


_RATING_MAX = {"priority": 5, "urgency": 3, "impact": 3}


def _state_value(state: str, states: Mapping[str, str]) -> str:
    key = state.strip().lower() if isinstance(state, str) else ""
    if key not in states:
        raise ValidationError(f"state must be one of: {', '.join(states)}")
    return states[key]


def _flag(value: bool, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{name} must be a boolean")
    return value


def _bool_clause(name: str, value: bool) -> str:
    return f"{name}={'true' if _flag(value, name) else 'false'}"


def _rating_clauses(**ratings: int | None) -> list[str]:
    return [f"{name}={_int_in(value, name, 1, _RATING_MAX[name])}" for name, value in ratings.items()
            if value is not None]


def _category(value: str, name: str = "category") -> str:
    value = value.strip()
    if not _CATEGORY_RE.match(value):
        raise ValidationError(f"{name} must be 1-40 letters, digits, spaces, _ or -")
    return value


def _sys_id_clauses(**refs: str | None) -> list[str]:
    return [f"{name}={_sys_id(value, name)}" for name, value in refs.items() if value is not None]


def _text_and_date_clauses(contains: str | None, opened_after: str | None, opened_before: str | None) -> list[str]:
    clauses = []
    if contains is not None:
        clauses.append(f"short_descriptionLIKE{_text(contains, 'short_description_contains')}")
    if opened_after is not None:
        clauses.append(f"opened_at>={_datetime(opened_after, 'opened_after')}")
    if opened_before is not None:
        clauses.append(f"opened_at<={_datetime(opened_before, 'opened_before')}")
    return clauses


def _finish_search(clauses: list[str], order_fields: Mapping[str, str], order_by: str, descending: bool,
                   limit: int, offset: int, max_limit: int) -> Search:
    if order_by not in order_fields:
        raise ValidationError(f"order_by must be one of: {', '.join(order_fields)}")
    clauses.append(f"{'ORDERBYDESC' if _flag(descending, 'descending') else 'ORDERBY'}{order_fields[order_by]}")
    return Search("^".join(clauses), _int_in(limit, "limit", 1, max_limit), _int_in(offset, "offset", 0, 10000))


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
) -> Search:
    clauses: list[str] = []
    if state is not None:
        clauses.append(f"state={_state_value(state, INCIDENT_STATES)}")
    if active is not None:
        clauses.append(_bool_clause("active", active))
    clauses += _rating_clauses(priority=priority, urgency=urgency, impact=impact)
    if category is not None:
        clauses.append(f"category={_category(category)}")
    clauses += _sys_id_clauses(assignment_group=assignment_group, caller_id=caller_id)
    if unassigned is not None:
        clauses.append("assignment_groupISEMPTY" if _flag(unassigned, "unassigned") else "assignment_groupISNOTEMPTY")
    clauses += _text_and_date_clauses(short_description_contains, opened_after, opened_before)
    return _finish_search(clauses, ORDER_FIELDS, order_by, descending, limit, offset, max_limit)


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
    # Normalise every line ending (including a lone \r) so single-line checks on "\n" cannot be bypassed.
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if required and not text:
        raise ValidationError(f"{name} is required")
    if len(text) > max_len:
        raise ValidationError(f"{name} must be at most {max_len} characters")
    if _CTL_RE.search(text):
        raise ValidationError(f"{name} contains control characters")
    return text


def _single_line(value: str, name: str, max_len: int) -> str:
    text = validate_text(value, name, max_len, required=True)
    if "\n" in text:
        raise ValidationError(f"{name} must be a single line")
    return text


def _short_description(value: str) -> str:
    return _single_line(value, "short_description", 160)


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
        out["short_description"] = _short_description(short_description)
    elif require_short_description:
        raise ValidationError("short_description is required")
    if description is not None:
        out["description"] = validate_text(description, "description", max_text)
    for name, label in (("category", category), ("subcategory", subcategory)):
        if label is not None:
            out[name] = _category(label, name)
    for name, val in (("urgency", urgency), ("impact", impact)):
        if val is not None:
            out[name] = str(_int_in(val, name, 1, 3))
    for name, ref in (
        ("caller_id", caller_id), ("assignment_group", assignment_group), ("assigned_to", assigned_to),
        ("business_service", business_service), ("cmdb_ci", cmdb_ci),
    ):
        if ref is not None:
            out[name] = _sys_id(ref, name)
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
) -> Search:
    clauses: list[str] = []
    if state is not None:
        clauses.append(f"state={_state_value(state, state_map)}")
    if active is not None:
        clauses.append(_bool_clause("active", active))
    if known_error is not None:
        clauses.append(_bool_clause("known_error", known_error))
    clauses += _rating_clauses(priority=priority, urgency=urgency, impact=impact)
    if category is not None:
        clauses.append(f"category={_category(category)}")
    clauses += _sys_id_clauses(assignment_group=assignment_group, cmdb_ci=cmdb_ci)
    if unassigned is not None:
        clauses.append("assignment_groupISEMPTY" if _flag(unassigned, "unassigned") else "assignment_groupISNOTEMPTY")
    clauses += _text_and_date_clauses(short_description_contains, opened_after, opened_before)
    return _finish_search(clauses, ORDER_FIELDS, order_by, descending, limit, offset, max_limit)


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
        out["short_description"] = _short_description(short_description)
    elif require_short_description:
        raise ValidationError("short_description is required")
    if description is not None:
        out["description"] = validate_text(description, "description", max_text)
    for name, label in (("category", category), ("subcategory", subcategory)):
        if label is not None:
            out[name] = _category(label, name)
    for name, val in (("urgency", urgency), ("impact", impact)):
        if val is not None:
            out[name] = str(_int_in(val, name, 1, 3))
    for name, ref in (
        ("cmdb_ci", cmdb_ci), ("business_service", business_service), ("assignment_group", assignment_group),
    ):
        if ref is not None:
            out[name] = _sys_id(ref, name)
    if state is not None:
        key = state.strip().lower() if isinstance(state, str) else ""
        if key not in PROBLEM_UPDATE_STATES:
            raise ValidationError(
                f"state may only be set to: {', '.join(PROBLEM_UPDATE_STATES)} (use resolve_problem to resolve)"
            )
        out["state"] = state_map[key]
    for name, text in (("cause_notes", cause_notes), ("workaround", workaround), ("fix_notes", fix_notes)):
        if text is not None:
            out[name] = validate_text(text, name, max_text, required=True)
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
) -> Search:
    clauses: list[str] = []
    if state is not None:
        clauses.append(f"state={_state_value(state, state_map)}")
    if active is not None:
        clauses.append(_bool_clause("active", active))
    if change_type is not None:
        key = change_type.strip().lower() if isinstance(change_type, str) else ""
        if key not in CHANGE_TYPES:
            raise ValidationError(f"type must be one of: {', '.join(CHANGE_TYPES)}")
        clauses.append(f"type={key}")
    clauses += _rating_clauses(priority=priority)
    if category is not None:
        clauses.append(f"category={_category(category)}")
    clauses += _sys_id_clauses(assignment_group=assignment_group, cmdb_ci=cmdb_ci)
    clauses += _text_and_date_clauses(short_description_contains, opened_after, opened_before)
    return _finish_search(clauses, CHANGE_ORDER_FIELDS, order_by, descending, limit, offset, max_limit)


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
        out["short_description"] = _short_description(short_description)
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
    for name, text in (
        ("description", description), ("justification", justification),
        ("implementation_plan", implementation_plan), ("backout_plan", backout_plan), ("test_plan", test_plan),
    ):
        if text is not None:
            out[name] = validate_text(text, name, max_text)
    if category is not None:
        out["category"] = _category(category)
    if impact is not None:
        out["impact"] = str(_int_in(impact, "impact", 1, 3))
    for name, ref in (("cmdb_ci", cmdb_ci), ("business_service", business_service),
                      ("assignment_group", assignment_group)):
        if ref is not None:
            out[name] = _sys_id(ref, name)
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


# ---------------------------------------------------------------------------
# Service catalog: items, record producers and their form variables
# ---------------------------------------------------------------------------
# Variable type -> raw item_option_new.type value. A deliberately small set of the common types.
VARIABLE_TYPES = {
    "yes_no": "1", "multi_line_text": "2", "select_box": "5", "single_line_text": "6",
    "checkbox": "7", "reference": "8", "date": "9", "date_time": "10",
}
CATALOG_KINDS = {"item": "sc_cat_item", "record_producer": "sc_cat_item_producer"}
CATALOG_ORDER_FIELDS = {"name": "name", "updated_at": "sys_updated_on"}
_VARIABLE_KEYS = {"name", "label", "type", "mandatory", "default_value", "help_text", "choices",
                  "reference_table", "maps_to_field"}
_VARIABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}\Z")
_CHOICE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}\Z")
MAX_CHOICES = 25


def build_catalog_search(
    *,
    name_contains: str | None = None,
    kind: str | None = None,
    active: bool | None = None,
    category: str | None = None,
    catalog: str | None = None,
    order_by: str = "name",
    descending: bool = False,
    limit: int = 10,
    offset: int = 0,
    max_limit: int = 50,
) -> Search:
    clauses: list[str] = []
    if name_contains is not None:
        clauses.append(f"nameLIKE{_text(name_contains, 'name_contains')}")
    if kind is not None:
        if kind not in CATALOG_KINDS:
            raise ValidationError(f"kind must be one of: {', '.join(CATALOG_KINDS)}")
        clauses.append(f"sys_class_name={CATALOG_KINDS[kind]}")
    if active is not None:
        clauses.append(_bool_clause("active", active))
    clauses += _sys_id_clauses(category=category)
    if catalog is not None:  # sc_catalogs is a list field
        clauses.append(f"sc_catalogsLIKE{_sys_id(catalog, 'catalog')}")
    return _finish_search(clauses, CATALOG_ORDER_FIELDS, order_by, descending, limit, offset, max_limit)


def build_catalog_item_fields(
    *,
    name: str,
    short_description: str,
    category: str,
    catalog: str,
    description: str | None = None,
    target_table: str | None = None,
    allowed_target_tables: tuple[str, ...] = (),
    max_text: int = 4000,
) -> dict[str, str]:
    """Fields for a new catalog item (or, with ``target_table``, a record producer). Always created INACTIVE."""
    out = {
        "name": _single_line(name, "name", 100),
        "short_description": _short_description(short_description),
        "category": _sys_id(category, "category"),
        "sc_catalogs": _sys_id(catalog, "catalog"),
        "active": "false",  # drafts only: a person reviews and activates the item in ServiceNow
    }
    if description is not None:
        out["description"] = validate_text(description, "description", max_text)
    if target_table is not None:
        table = target_table.strip().lower() if isinstance(target_table, str) else ""
        if not _TABLE_NAME_RE.match(table):
            raise ValidationError("target_table must be a table name such as incident")
        if table not in allowed_target_tables:
            raise ValidationError(
                "record producers may only create records in: " + (", ".join(allowed_target_tables) or "(none)")
            )
        out["table_name"] = table
    return out


@dataclass(frozen=True)
class VariableBuild:
    """One validated form variable (item_option_new row, minus the item link) and its choices."""

    fields: dict[str, str]
    choices: tuple[dict[str, str], ...] = ()


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]


def _build_choices(raw: object, var: str) -> tuple[dict[str, str], ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CHOICES:
        raise ValidationError(f"variable {var}: a select_box needs 1-{MAX_CHOICES} choices")
    out, seen = [], set()
    for j, choice in enumerate(raw, start=1):
        if not isinstance(choice, Mapping) or set(choice) - {"label", "value"} or "label" not in choice:
            raise ValidationError(f"variable {var}: each choice is {{label, value (optional)}}")
        label = _single_line(choice["label"], "choice label", 80)
        value = choice.get("value") or _slug(label)
        if not isinstance(value, str) or not _CHOICE_VALUE_RE.match(value):
            raise ValidationError(f"variable {var}: choice value must be 1-40 letters, digits, . _ -")
        if value in seen:
            raise ValidationError(f"variable {var}: duplicate choice value {value}")
        seen.add(value)
        out.append({"text": label, "value": value, "order": str(100 * j)})
    return tuple(out)


def build_catalog_variables(
    variables: object, *, producer: bool = False, start_order: int = 0, max_text: int = 4000
) -> list[VariableBuild]:
    """Validate the form variables of a catalog item / record producer (no scripts, closed vocabulary)."""
    if not isinstance(variables, list) or not variables:
        raise ValidationError("provide at least one variable")
    out: list[VariableBuild] = []
    names: set[str] = set()
    mapped: set[str] = set()
    for i, raw in enumerate(variables, start=1):
        if not isinstance(raw, Mapping) or set(raw) - _VARIABLE_KEYS:
            raise ValidationError("each variable allows only: " + ", ".join(sorted(_VARIABLE_KEYS)))
        name = raw.get("name")
        if not isinstance(name, str) or not _VARIABLE_NAME_RE.match(name):
            raise ValidationError("variable name must be 1-40 lowercase letters, digits or _, starting with a letter")
        if name in names:
            raise ValidationError(f"duplicate variable name {name}")
        names.add(name)
        vtype = raw.get("type")
        if vtype not in VARIABLE_TYPES:
            raise ValidationError(f"variable {name}: type must be one of: {', '.join(VARIABLE_TYPES)}")
        mandatory = raw.get("mandatory", False)
        if not isinstance(mandatory, bool):
            raise ValidationError(f"variable {name}: mandatory must be a boolean")
        fields = {
            "name": name,
            "question_text": _single_line(raw.get("label", ""), f"variable {name} label", 160),
            "type": VARIABLE_TYPES[vtype],
            "mandatory": "true" if mandatory else "false",
            "order": str(start_order + 100 * i),
        }
        for key, field, limit in (("default_value", "default_value", 200), ("help_text", "help_text", 400)):
            if raw.get(key) not in (None, ""):
                fields[field] = validate_text(raw[key], f"variable {name} {key}", min(limit, max_text))
        if vtype == "reference":
            table = raw.get("reference_table")
            if not isinstance(table, str) or not _TABLE_NAME_RE.match(table):
                raise ValidationError(f"variable {name}: a reference needs reference_table, e.g. sys_user")
            fields["reference"] = table
        elif raw.get("reference_table") is not None:
            raise ValidationError(f"variable {name}: reference_table is only for type reference")
        if vtype == "select_box":
            choices = _build_choices(raw.get("choices"), name)
        elif raw.get("choices") is not None:
            raise ValidationError(f"variable {name}: choices are only for type select_box")
        else:
            choices = ()
        column = raw.get("maps_to_field")
        if column is not None:
            if not producer:
                raise ValidationError(f"variable {name}: maps_to_field only applies to record producers")
            if not isinstance(column, str) or not _TABLE_NAME_RE.match(column) or column in mapped:
                raise ValidationError(f"variable {name}: maps_to_field must be a unique column name such as short_description")
            mapped.add(column)
            fields["map_to_field"] = "true"
            fields["field"] = column
        out.append(VariableBuild(fields, choices))
    return out


def catalog_record_count(builds: list[VariableBuild]) -> int:
    """Number of ServiceNow records (variables + choices) the builds will create."""
    return sum(1 + len(b.choices) for b in builds)
