"""Configuration loading and validation.

All configuration comes from environment variables, which systemd populates from
two files: a non-secret ``config.env`` and a root-only ``secrets.env``. Nothing
is read from the repository and secret values never appear in ``repr``/errors.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DEFAULT_HOST_SUFFIXES = (".service-now.com", ".servicenowservices.com")
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TRUE = {"1", "true", "yes", "on"}


class ConfigError(Exception):
    """Raised for invalid configuration. Messages never contain secret values."""


def _secret(env: Mapping[str, str], key: str) -> str:
    """Value of KEY, or the contents of the file named by KEY_FILE (Docker/systemd credentials)."""
    path = env.get(f"{key}_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip("\r\n")
        except OSError:
            raise ConfigError(f"{key}_FILE could not be read") from None
    return env.get(key, "")


def _bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUE


def _int(env: Mapping[str, str], key: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo} and {hi}")
    return value


def _float(env: Mapping[str, str], key: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo} and {hi}")
    return value


def validate_instance_url(url: str, allow_custom_domain: bool = False) -> str:
    """Return a normalised ``https://host`` base URL or raise ConfigError."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise ConfigError("SERVICENOW_INSTANCE_URL must use https://")
    if parts.username or parts.password:
        raise ConfigError("SERVICENOW_INSTANCE_URL must not contain credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ConfigError("SERVICENOW_INSTANCE_URL must be just https://<host>")
    try:
        port = parts.port
    except ValueError as exc:
        raise ConfigError("SERVICENOW_INSTANCE_URL has an invalid port") from exc
    if port not in (None, 443):
        raise ConfigError("SERVICENOW_INSTANCE_URL must use the default HTTPS port")
    host = (parts.hostname or "").lower()
    if not _HOST_RE.match(host):
        raise ConfigError("SERVICENOW_INSTANCE_URL host name is not a valid DNS name")
    if not allow_custom_domain and not host.endswith(DEFAULT_HOST_SUFFIXES):
        raise ConfigError(
            "SERVICENOW_INSTANCE_URL host must end in .service-now.com or "
            ".servicenowservices.com (set SERVICENOW_ALLOW_CUSTOM_DOMAIN=true "
            "for a custom domain)"
        )
    return f"https://{host}"


# Problem state names the tools understand, and the default raw values (the 101-107 model used by
# current ServiceNow releases). Older instances use different numbers: override with MCP_PROBLEM_STATES.
PROBLEM_STATE_NAMES = ("new", "assess", "root_cause_analysis", "fix_in_progress", "resolved", "closed")
DEFAULT_PROBLEM_STATES = (
    ("new", "101"), ("assess", "102"), ("root_cause_analysis", "103"),
    ("fix_in_progress", "104"), ("resolved", "106"), ("closed", "107"),
)
_STATE_VALUE_RE = re.compile(r"^\d{1,4}\Z")

# Change request phases the tools understand and the default raw values (the model-based lifecycle of
# current releases: negative numbers for the early phases). Override with MCP_CHANGE_STATES if yours differ.
CHANGE_STATE_NAMES = ("new", "assess", "authorize", "scheduled", "implement", "review", "closed", "canceled")
DEFAULT_CHANGE_STATES = (
    ("new", "-5"), ("assess", "-4"), ("authorize", "-3"), ("scheduled", "-2"),
    ("implement", "-1"), ("review", "0"), ("closed", "3"), ("canceled", "4"),
)
_CHANGE_STATE_VALUE_RE = re.compile(r"^-?\d{1,4}\Z")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,80}\Z")
_TYPE_RE = re.compile(r"^[a-z][a-z _-]{0,30}\Z")
_TEAM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.&-]{0,59}\Z")
_SYS_ID_RE = re.compile(r"^[0-9a-f]{32}\Z")


def _parse_teams(raw: str) -> tuple[tuple[str, str], ...]:
    """Parse ``Name=sys_id;Name2=sys_id`` into an allowlist of assignment teams."""
    teams: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in (p for p in raw.split(";") if p.strip()):
        name, sep, sys_id = item.partition("=")
        name, sys_id = name.strip(), sys_id.strip().lower()
        if not sep or not _TEAM_NAME_RE.match(name) or not _SYS_ID_RE.match(sys_id):
            raise ConfigError("MCP_ASSIGNMENT_GROUPS entries must look like 'Network Ops=<32-hex sys_id>'")
        if name.lower() in seen:
            raise ConfigError("MCP_ASSIGNMENT_GROUPS contains a duplicate team name")
        seen.add(name.lower())
        teams.append((name, sys_id))
    return tuple(teams)


def _parse_state_map(
    raw: str,
    var: str,
    example: str,
    names: tuple[str, ...],
    defaults: tuple[tuple[str, str], ...],
    value_re: re.Pattern[str],
) -> tuple[tuple[str, str], ...]:
    """Parse ``name=value;...`` overrides on top of the defaults (every name stays mapped)."""
    mapping = dict(defaults)
    seen: set[str] = set()
    for item in (p for p in raw.split(";") if p.strip()):
        name, sep, value = item.partition("=")
        name, value = name.strip().lower(), value.strip()
        if not sep or name not in names or not value_re.match(value):
            raise ConfigError(
                f"{var} entries must look like '{example}' with a name from: " + ", ".join(names)
            )
        if name in seen:
            raise ConfigError(f"{var} lists a state name twice")
        seen.add(name)
        mapping[name] = value
    if len(set(mapping.values())) != len(mapping):
        raise ConfigError(f"{var} must map every state to a different value")
    return tuple((n, mapping[n]) for n in names)


def _parse_problem_states(raw: str) -> tuple[tuple[str, str], ...]:
    return _parse_state_map(raw, "MCP_PROBLEM_STATES", "resolved=106", PROBLEM_STATE_NAMES,
                            DEFAULT_PROBLEM_STATES, _STATE_VALUE_RE)


def _parse_change_states(raw: str) -> tuple[tuple[str, str], ...]:
    return _parse_state_map(raw, "MCP_CHANGE_STATES", "closed=3", CHANGE_STATE_NAMES,
                            DEFAULT_CHANGE_STATES, _CHANGE_STATE_VALUE_RE)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_private_interface(host: str) -> bool:
    """Loopback or a private (RFC1918/ULA/link-local) address, e.g. the Docker bridge gateway.

    Such an address is not reachable from the internet, so a TLS-terminating reverse proxy
    (Traefik in a container) can reach the service over plain HTTP without exposing it.
    The unspecified addresses (0.0.0.0, ::) are explicitly NOT private.
    """
    if _is_loopback(host):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private and not ip.is_unspecified and not ip.is_global


@dataclass(frozen=True)
class Settings:
    instance_url: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    auth_token_hashes: tuple[str, ...] = field(repr=False)
    public_hostname: str = ""
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    behind_tls_proxy: bool = False
    request_timeout: float = 15.0
    max_page_size: int = 50
    rate_limit_per_minute: int = 60
    max_text_chars: int = 4000
    log_level: str = "INFO"
    # --- optional write capabilities: every one is OFF unless explicitly enabled ---
    enable_incident_writes: bool = False  # create_incident, update_incident, resolve_incident
    enable_incident_assignment: bool = False  # assign_incident (needs assignment_teams)
    enable_change_approval: bool = False  # approve_change_request
    enable_oncall_read: bool = False  # list_oncall_rotas, get_oncall_roster, whois_oncall
    enable_oncall_writes: bool = False  # add_oncall_member, update_oncall_member
    enable_problem_read: bool = False  # search_problems, get_problem
    enable_problem_writes: bool = False  # create/update/resolve_problem, link_incident_to_problem (implies read)
    problem_states: tuple[tuple[str, str], ...] = DEFAULT_PROBLEM_STATES  # state name -> raw ServiceNow value
    enable_change_read: bool = False  # search_change_requests
    enable_change_writes: bool = False  # create/update/transition/close/cancel change (implies read)
    change_states: tuple[tuple[str, str], ...] = DEFAULT_CHANGE_STATES  # phase name -> raw ServiceNow value
    change_create_types: tuple[str, ...] = ("normal", "standard")  # types an agent may create
    assignment_teams: tuple[tuple[str, str], ...] = ()  # (display name, group sys_id) allowlist
    approval_allowed_types: tuple[str, ...] = ("standard", "normal")
    write_rate_limit_per_minute: int = 20

    @property
    def problem_state_map(self) -> dict[str, str]:
        return dict(self.problem_states)

    @property
    def change_state_map(self) -> dict[str, str]:
        return dict(self.change_states)

    @property
    def native_tls(self) -> bool:
        return bool(self.tls_cert_file and self.tls_key_file)


def load_servicenow_settings(env: Mapping[str, str] | None = None) -> tuple[str, str, str]:
    """Load only the ServiceNow part (used by the connectivity check)."""
    env = os.environ if env is None else env
    url = validate_instance_url(
        env.get("SERVICENOW_INSTANCE_URL", ""), _bool(env, "SERVICENOW_ALLOW_CUSTOM_DOMAIN")
    )
    user = _secret(env, "SERVICENOW_USERNAME")
    password = _secret(env, "SERVICENOW_PASSWORD")
    if not user or not password:
        raise ConfigError("SERVICENOW_USERNAME and SERVICENOW_PASSWORD must be set")
    return url, user, password


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    url, user, password = load_servicenow_settings(env)

    hashes = []
    for key in ("MCP_AUTH_TOKEN_SHA256", "MCP_AUTH_TOKEN_SHA256_PREVIOUS"):
        value = _secret(env, key).strip().lower()
        if value:
            if not _SHA256_RE.match(value):
                raise ConfigError(f"{key} must be a 64-character lowercase hex SHA-256 digest")
            hashes.append(value)
    if not hashes or not _secret(env, "MCP_AUTH_TOKEN_SHA256").strip():
        raise ConfigError("MCP_AUTH_TOKEN_SHA256 must be set")

    public_hostname = env.get("MCP_PUBLIC_HOSTNAME", "").strip().lower()
    if not _HOST_RE.match(public_hostname):
        raise ConfigError("MCP_PUBLIC_HOSTNAME must be the DNS name Hermes connects to")

    bind_host = env.get("MCP_BIND_HOST", "127.0.0.1").strip()
    cert = env.get("MCP_TLS_CERT_FILE", "").strip() or None
    tls_key = env.get("MCP_TLS_KEY_FILE", "").strip() or None
    if bool(cert) != bool(tls_key):
        raise ConfigError("MCP_TLS_CERT_FILE and MCP_TLS_KEY_FILE must be set together")
    proxy = _bool(env, "MCP_BEHIND_TLS_PROXY")

    # TLS guard: plain HTTP is only ever accepted on loopback or a private interface
    # (e.g. the Docker bridge), and only when the operator confirms a TLS-terminating
    # reverse proxy fronts it. Public/wildcard binds require native TLS.
    # Inside a container the proxy reaches us over a private Docker network, so the bind
    # must be the wildcard; the operator acknowledges that with MCP_BIND_ALL_IN_CONTAINER and
    # must not publish the port (compose file publishes none).
    wildcard_ok = _bool(env, "MCP_BIND_ALL_IN_CONTAINER") and bind_host in ("0.0.0.0", "::")
    if not cert:
        if not wildcard_ok and not _is_private_interface(bind_host):
            raise ConfigError(
                "refusing to listen on a public or wildcard address without TLS "
                "(set MCP_TLS_CERT_FILE/MCP_TLS_KEY_FILE, or bind to 127.0.0.1 / a private "
                "address such as the Docker bridge gateway)"
            )
        if not proxy:
            raise ConfigError(
                "no TLS configured: set MCP_TLS_CERT_FILE/MCP_TLS_KEY_FILE, or bind to "
                "loopback behind a TLS reverse proxy and set MCP_BEHIND_TLS_PROXY=true"
            )

    log_level = env.get("MCP_LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("MCP_LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")

    enable_assignment = _bool(env, "MCP_ENABLE_INCIDENT_ASSIGNMENT")
    teams = _parse_teams(env.get("MCP_ASSIGNMENT_GROUPS", ""))
    if enable_assignment and not teams:
        raise ConfigError("MCP_ENABLE_INCIDENT_ASSIGNMENT requires MCP_ASSIGNMENT_GROUPS (name=sys_id;...)")

    enable_approval = _bool(env, "MCP_ENABLE_CHANGE_APPROVAL")
    if enable_approval and not _USERNAME_RE.match(user):
        raise ConfigError("change approval needs a ServiceNow user name of letters, digits and . _ @ -")
    types = tuple(
        t.strip().lower() for t in env.get("MCP_APPROVAL_ALLOWED_TYPES", "standard,normal").split(",") if t.strip()
    )
    if enable_approval and (not types or not all(_TYPE_RE.match(t) for t in types)):
        raise ConfigError("MCP_APPROVAL_ALLOWED_TYPES must be a comma list such as standard,normal")

    oncall_writes = _bool(env, "MCP_ENABLE_ONCALL_WRITES")
    problem_writes = _bool(env, "MCP_ENABLE_PROBLEM_WRITES")
    problem_states = _parse_problem_states(env.get("MCP_PROBLEM_STATES", ""))
    change_writes = _bool(env, "MCP_ENABLE_CHANGE_WRITES")
    change_states = _parse_change_states(env.get("MCP_CHANGE_STATES", ""))
    create_types = tuple(
        t.strip().lower() for t in env.get("MCP_CHANGE_CREATE_TYPES", "normal,standard").split(",") if t.strip()
    )
    if change_writes and (not create_types or not all(_TYPE_RE.match(t) for t in create_types)):
        raise ConfigError("MCP_CHANGE_CREATE_TYPES must be a comma list such as normal,standard")

    return Settings(
        enable_incident_writes=_bool(env, "MCP_ENABLE_INCIDENT_WRITES"),
        enable_incident_assignment=enable_assignment,
        enable_change_approval=enable_approval,
        enable_oncall_read=_bool(env, "MCP_ENABLE_ONCALL_READ") or oncall_writes,
        enable_oncall_writes=oncall_writes,
        enable_problem_read=_bool(env, "MCP_ENABLE_PROBLEM_READ") or problem_writes,
        enable_problem_writes=problem_writes,
        problem_states=problem_states,
        enable_change_read=_bool(env, "MCP_ENABLE_CHANGE_READ") or change_writes,
        enable_change_writes=change_writes,
        change_states=change_states,
        change_create_types=create_types or ("normal", "standard"),
        assignment_teams=teams,
        approval_allowed_types=types or ("standard", "normal"),
        write_rate_limit_per_minute=_int(env, "MCP_WRITE_RATE_LIMIT_PER_MINUTE", 20, 1, 300),
        instance_url=url,
        username=user,
        password=password,
        auth_token_hashes=tuple(hashes),
        public_hostname=public_hostname,
        bind_host=bind_host,
        bind_port=_int(env, "MCP_BIND_PORT", 8765, 1, 65535),
        tls_cert_file=cert,
        tls_key_file=tls_key,
        behind_tls_proxy=proxy,
        request_timeout=_float(env, "MCP_REQUEST_TIMEOUT", 15.0, 1.0, 60.0),
        max_page_size=_int(env, "MCP_MAX_PAGE_SIZE", 50, 1, 100),
        rate_limit_per_minute=_int(env, "MCP_RATE_LIMIT_PER_MINUTE", 60, 1, 1000),
        max_text_chars=_int(env, "MCP_MAX_TEXT_CHARS", 4000, 200, 20000),
        log_level=log_level,
    )

