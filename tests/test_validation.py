import pytest

from servicenow_mcp.validation import (
    CHANGE_NUMBER_RE,
    INCIDENT_NUMBER_RE,
    ValidationError,
    build_incident_search,
    validate_identifier,
)

SYS_ID = "a" * 32


def test_identifier_sys_id_and_number():
    assert validate_identifier(SYS_ID.upper(), INCIDENT_NUMBER_RE, "incident") == ("sys_id", SYS_ID)
    assert validate_identifier(" inc0010001 ", INCIDENT_NUMBER_RE, "incident") == ("number", "INC0010001")
    assert validate_identifier("CHG0030001", CHANGE_NUMBER_RE, "change") == ("number", "CHG0030001")


@pytest.mark.parametrize(
    "bad",
    ["", "INC1", "CHG0030001", "INC0010001^ORactive=true", "../../x", "a" * 31, "a" * 33,
     "INC0010\n001", "INC0010001%5E", "x" * 500, "sys_id=" + SYS_ID],
)
def test_identifier_rejects_bad(bad):
    with pytest.raises(ValidationError):
        validate_identifier(bad, INCIDENT_NUMBER_RE, "incident")


def test_identifier_rejects_non_string():
    with pytest.raises(ValidationError):
        validate_identifier(123, INCIDENT_NUMBER_RE, "incident")  # type: ignore[arg-type]


def test_default_search_is_ordered_only():
    s = build_incident_search()
    assert s.query == "ORDERBYDESCopened_at"
    assert (s.limit, s.offset) == (10, 0)


def test_full_search_query():
    s = build_incident_search(
        state="in_progress", active=True, priority=2, urgency=1, impact=3, category="network",
        assignment_group=SYS_ID, caller_id=SYS_ID, short_description_contains="vpn down",
        opened_after="2024-01-02", opened_before="2024-02-03T04:05:06Z", order_by="priority",
        descending=False, limit=5, offset=10,
    )
    assert s.query.split("^") == [
        "state=2", "active=true", "priority=2", "urgency=1", "impact=3", "category=network",
        f"assignment_group={SYS_ID}", f"caller_id={SYS_ID}", "short_descriptionLIKEvpn down",
        "opened_at>=2024-01-02 00:00:00", "opened_at<=2024-02-03 04:05:06", "ORDERBYpriority",
    ]
    assert (s.limit, s.offset) == (5, 10)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"short_description_contains": "x^ORactive=false"},
        {"short_description_contains": "a^NQb"},
        {"short_description_contains": "line\nbreak"},
        {"short_description_contains": "%5E"},
        {"short_description_contains": "javascript:gs.log(1)'"},
        {"short_description_contains": ""},
        {"short_description_contains": "x" * 101},
        {"category": "net^work"},
        {"category": "a" * 41},
        {"state": "active=true^"},
        {"state": "bogus"},
        {"priority": 0}, {"priority": 6}, {"priority": True}, {"priority": "1"},
        {"urgency": 4}, {"impact": 4},
        {"assignment_group": "not-a-sys-id"}, {"caller_id": SYS_ID + "^x"},
        {"opened_after": "yesterday"}, {"opened_after": "2024-13-01"},
        {"opened_before": "2024-01-01'; DROP"},
        {"order_by": "short_description^"}, {"order_by": "sys_id"},
        {"descending": "yes"}, {"active": "true"},
        {"limit": 0}, {"limit": 51}, {"limit": -1}, {"limit": 1.5},
        {"offset": -1}, {"offset": 10001},
    ],
)
def test_search_rejects_bad_input(kwargs):
    with pytest.raises(ValidationError):
        build_incident_search(**kwargs)


def test_max_limit_is_configurable():
    assert build_incident_search(limit=100, max_limit=100).limit == 100
    with pytest.raises(ValidationError):
        build_incident_search(limit=11, max_limit=10)


def test_no_caret_can_survive_in_values():
    """The only ^ in a built query are our own clause separators."""
    s = build_incident_search(short_description_contains="vpn (prod) #1", category="a b-c")
    assert s.query.count("^") == s.query.count("^") and all(
        "^" not in part for part in s.query.split("^")
    )

