"""The sanitisation / flagging layer.

Every record leaves the API in two parts:

  * canonical values  - parsed dates, normalised enums, cleaned text
  * ``flags``         - what was wrong with the row and what we did about it

Nothing is silently dropped and nothing corrupt is silently accepted: a value
we cannot trust becomes ``None`` (or ``UNKNOWN``) *and* raises a flag the UI
can show. That is the whole point of the triage layer.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum

# --------------------------------------------------------------------------
# Canonical vocabularies
# --------------------------------------------------------------------------


class DirectiveStatus(str, Enum):
    """Where a directive sits in *our* triage workflow.

    Deliberately not the regulator's lifecycle vocabulary. Feeds publish
    "draft", "in force", "superseded", "revoked" and a dozen other labels;
    those are mapped onto these five by `_DIRECTIVE_STATUS_ALIASES`, and the
    original string is always kept in `raw_status`.

    QUARANTINED is the honest bucket: a status we could not map, could not
    read, or that the feed contradicted itself about. It never means "fine".
    """

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    BLOCKED = "BLOCKED"
    QUARANTINED = "QUARANTINED"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class ActionStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"
    UNKNOWN = "UNKNOWN"


#: States that still require work from a compliance officer.
OPEN_ACTION_STATUSES = {
    ActionStatus.PENDING,
    ActionStatus.IN_PROGRESS,
    ActionStatus.BLOCKED,
    ActionStatus.UNKNOWN,
}

#: Statuses an officer may *set*. UNKNOWN is read-only - it only ever comes
#: from corrupt ingested data and must be triaged into a real state.
ASSIGNABLE_ACTION_STATUSES = {
    ActionStatus.PENDING,
    ActionStatus.IN_PROGRESS,
    ActionStatus.BLOCKED,
    ActionStatus.RESOLVED,
    ActionStatus.DISMISSED,
}

#: Allowed state machine. Terminal states can only be re-opened into IN_PROGRESS.
ALLOWED_TRANSITIONS: dict[ActionStatus, set[ActionStatus]] = {
    ActionStatus.PENDING: {
        ActionStatus.IN_PROGRESS,
        ActionStatus.BLOCKED,
        ActionStatus.RESOLVED,
        ActionStatus.DISMISSED,
    },
    ActionStatus.IN_PROGRESS: {
        ActionStatus.PENDING,
        ActionStatus.BLOCKED,
        ActionStatus.RESOLVED,
        ActionStatus.DISMISSED,
    },
    ActionStatus.BLOCKED: {
        ActionStatus.PENDING,
        ActionStatus.IN_PROGRESS,
        ActionStatus.RESOLVED,
        ActionStatus.DISMISSED,
    },
    ActionStatus.RESOLVED: {ActionStatus.IN_PROGRESS},
    ActionStatus.DISMISSED: {ActionStatus.IN_PROGRESS},
    # Corrupt rows may be triaged into any real state.
    ActionStatus.UNKNOWN: set(ASSIGNABLE_ACTION_STATUSES),
}

#: Regulator vocabulary -> triage workflow state.
_DIRECTIVE_STATUS_ALIASES = {
    # Published but no work has started on our side yet.
    "draft": DirectiveStatus.PENDING,
    "proposed": DirectiveStatus.PENDING,
    "proposal": DirectiveStatus.PENDING,
    "consultation": DirectiveStatus.PENDING,
    "pending": DirectiveStatus.PENDING,
    "new": DirectiveStatus.PENDING,
    # In force, so compliance work is live.
    "active": DirectiveStatus.IN_PROGRESS,
    "in force": DirectiveStatus.IN_PROGRESS,
    "inforce": DirectiveStatus.IN_PROGRESS,
    "effective": DirectiveStatus.IN_PROGRESS,
    "published": DirectiveStatus.IN_PROGRESS,
    "in progress": DirectiveStatus.IN_PROGRESS,
    "in_progress": DirectiveStatus.IN_PROGRESS,
    # No longer demands action from us.
    "superseded": DirectiveStatus.RESOLVED,
    "replaced": DirectiveStatus.RESOLVED,
    "withdrawn": DirectiveStatus.RESOLVED,
    "revoked": DirectiveStatus.RESOLVED,
    "cancelled": DirectiveStatus.RESOLVED,
    "closed": DirectiveStatus.RESOLVED,
    "resolved": DirectiveStatus.RESOLVED,
    # Waiting on something outside our control.
    "blocked": DirectiveStatus.BLOCKED,
    "on hold": DirectiveStatus.BLOCKED,
    "suspended": DirectiveStatus.BLOCKED,
    "stayed": DirectiveStatus.BLOCKED,
    "under appeal": DirectiveStatus.BLOCKED,
    # Explicitly untrusted.
    "quarantined": DirectiveStatus.QUARANTINED,
}

_SEVERITY_ALIASES = {
    "low": Severity.LOW,
    "minor": Severity.LOW,
    "1": Severity.LOW,
    "medium": Severity.MEDIUM,
    "moderate": Severity.MEDIUM,
    "2": Severity.MEDIUM,
    "high": Severity.HIGH,
    "major": Severity.HIGH,
    "3": Severity.HIGH,
    "critical": Severity.CRITICAL,
    "blocker": Severity.CRITICAL,
    "urgent": Severity.CRITICAL,
    "extreme": Severity.CRITICAL,
    "catastrophic": Severity.CRITICAL,
    "4": Severity.CRITICAL,
}

_ACTION_STATUS_ALIASES = {
    "pending": ActionStatus.PENDING,
    "open": ActionStatus.PENDING,
    "new": ActionStatus.PENDING,
    "todo": ActionStatus.PENDING,
    "in progress": ActionStatus.IN_PROGRESS,
    "in_progress": ActionStatus.IN_PROGRESS,
    "inprogress": ActionStatus.IN_PROGRESS,
    "wip": ActionStatus.IN_PROGRESS,
    "started": ActionStatus.IN_PROGRESS,
    "blocked": ActionStatus.BLOCKED,
    "on hold": ActionStatus.BLOCKED,
    "waiting": ActionStatus.BLOCKED,
    "awaiting input": ActionStatus.BLOCKED,
    "suspended": ActionStatus.BLOCKED,
    "resolved": ActionStatus.RESOLVED,
    "done": ActionStatus.RESOLVED,
    "complete": ActionStatus.RESOLVED,
    "completed": ActionStatus.RESOLVED,
    "closed": ActionStatus.RESOLVED,
    "dismissed": ActionStatus.DISMISSED,
    "not applicable": ActionStatus.DISMISSED,
    "n/a": ActionStatus.DISMISSED,
    "na": ActionStatus.DISMISSED,
    "rejected": ActionStatus.DISMISSED,
}

_UNASSIGNED_OWNERS = {"", "unassigned", "tbd", "tba", "none", "n/a", "-", "???"}


# --------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Flag:
    code: str
    level: str  # "error" | "warning"
    field: str | None
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


#: Every code this module (or services.py) can raise. Exposed through
#: /api/vocabulary so the UI can offer a flag filter without inventing codes,
#: and used to reject unknown ?flag= values instead of returning an empty page.
FLAG_CODES: frozenset[str] = frozenset(
    {
        # text
        "MISSING_FIELD",
        "MALFORMED_TEXT",
        "TRUNCATED_TEXT",
        "SHOUTING_TEXT",
        "OVERLONG_TEXT",
        # dates
        "MISSING_DATE",
        "UNPARSEABLE_DATE",
        "AMBIGUOUS_DATE",
        "DATE_CHRONOLOGY",
        "MISSING_DEADLINE",
        "OVERDUE",
        "DUE_AFTER_DIRECTIVE_DEADLINE",
        # status / severity
        "MISSING_STATUS",
        "UNKNOWN_STATUS",
        "NORMALISED_STATUS",
        "CONFLICTING_STATUS",
        "STATUS_CONFLICT",
        "MISSING_SEVERITY",
        "UNKNOWN_SEVERITY",
        "NORMALISED_SEVERITY",
        # ownership / bookkeeping
        "UNASSIGNED_OWNER",
        "RESOLVED_WITHOUT_TIMESTAMP",
        "TIMESTAMP_WITHOUT_RESOLUTION",
        "DUPLICATE_REFERENCE",
    }
)


def quality_of(flags: list[Flag]) -> str:
    """Roll a list of flags up into one row-level verdict."""
    if any(f.level == "error" for f in flags):
        return "error"
    if flags:
        return "warning"
    return "ok"


def dedupe(flags: list[Flag]) -> list[Flag]:
    """Same (code, field) twice adds nothing for the reader."""
    seen: set[tuple[str, str | None]] = set()
    out: list[Flag] = []
    for flag in flags:
        key = (flag.code, flag.field)
        if key not in seen:
            seen.add(key)
            out.append(flag)
    return out


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

# A tag opens with "<" immediately followed by a name, "/" or "!". Requiring
# that keeps prose like "a < b and c > d" intact - a looser "<[^>]*>" treats
# everything between the comparison operators as a tag and deletes it.
_TAG_RE = re.compile(r"</?[a-zA-Z!][^<>]{0,200}>")
_BROKEN_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]*$")
_WS_RE = re.compile(r"\s+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TRUNCATION_RE = re.compile(r"(\.{3}|…|\[truncated\]|<truncated>)\s*$", re.IGNORECASE)

# Classic UTF-8-decoded-as-latin-1 damage, seen constantly in scraped feeds.
# Built from escapes so the source file stays plain ASCII.
_MOJIBAKE = {
    "â€™": "'",
    "â€˜": "'",
    "â€\u009c": '"',
    "â€\u009d": '"',
    "â€“": "-",
    "â€”": "-",
    "â€¦": "...",
    "Â ": " ",
    "Â": "",
}

MAX_TEXT_LEN = 600


def clean_text(
    value: str | None,
    *,
    field: str,
    required: bool = False,
    max_len: int = MAX_TEXT_LEN,
) -> tuple[str | None, list[Flag]]:
    """Strip markup / mojibake / control characters and report what was repaired."""
    flags: list[Flag] = []

    if value is None or not str(value).strip():
        if required:
            flags.append(
                Flag("MISSING_FIELD", "error", field, f"{field} is missing from the source record")
            )
        return None, flags

    original = str(value)
    text = original

    for bad, good in _MOJIBAKE.items():
        text = text.replace(bad, good)

    text = html.unescape(text)
    had_markup = bool(_TAG_RE.search(text))
    text = _TAG_RE.sub(" ", text)
    # A "<" that opens something never closed: left in place (it may be real
    # prose) but reported, since it usually means the extractor truncated
    # mid-tag. "a < b" does not match - the "<" must start a tag name.
    had_broken_markup = bool(_BROKEN_TAG_RE.search(text))
    text = _CONTROL_RE.sub(" ", text)
    text = unicodedata.normalize("NFKC", text)
    text = _WS_RE.sub(" ", text).strip()

    # Decide "was this repaired?" here, before our own cosmetic transforms
    # below - otherwise clipping or case-folding would accuse the source of
    # damage it did not cause.
    if had_markup or had_broken_markup or text != original.strip():
        detail = (
            "an unclosed tag, so the source may be truncated"
            if had_broken_markup
            else "markup or encoding damage that was repaired"
        )
        flags.append(Flag("MALFORMED_TEXT", "warning", field, f"{field} contained {detail}"))

    if not text:
        return None, [
            Flag(
                "MALFORMED_TEXT",
                "error",
                field,
                f"{field} contained only markup or control characters",
            )
        ]

    if _TRUNCATION_RE.search(text):
        flags.append(
            Flag("TRUNCATED_TEXT", "warning", field, f"{field} looks truncated at the source")
        )

    letters = [c for c in text if c.isalpha()]
    if len(letters) > 15 and all(c.isupper() for c in letters):
        text = text.title()
        flags.append(
            Flag(
                "SHOUTING_TEXT",
                "warning",
                field,
                f"{field} was all-caps and has been case-normalised",
            )
        )

    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
        flags.append(
            Flag(
                "OVERLONG_TEXT",
                "warning",
                field,
                f"{field} exceeded {max_len} characters and was clipped",
            )
        )

    return text, dedupe(flags)


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

#: Day-first formats. Anything matching these may also be a US month-first
#: date, which is why `_AMBIGUOUS_NUMERIC_RE` exists below.
_DAY_FIRST_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    *_DAY_FIRST_FORMATS,
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b, %Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
)

#: Two one-or-two digit components then a year, e.g. 03/04/2026.
_AMBIGUOUS_NUMERIC_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")

_PLACEHOLDER_DATES = {"", "n/a", "na", "null", "none", "tbd", "tba", "unknown", "-", "0000-00-00"}


def parse_date(
    raw: str | None,
    *,
    field: str,
    required: bool = False,
) -> tuple[date | None, list[Flag]]:
    """Best-effort date parsing. Never raises; unparseable input is flagged."""
    flags: list[Flag] = []

    if raw is None or str(raw).strip().lower() in _PLACEHOLDER_DATES:
        if required:
            flags.append(
                Flag(
                    "MISSING_DATE",
                    "error",
                    field,
                    f"{field} is missing - this record cannot be scheduled",
                )
            )
        return None, flags

    text = _WS_RE.sub(" ", str(raw).strip())

    # 03/04/2026 is 3 April here and 4 March in the US. We keep the day-first
    # reading, but a value that could legitimately be either is reported
    # rather than silently trusted - a month's error on a compliance deadline
    # is exactly the kind of mistake this pipeline exists to surface.
    ambiguous = _AMBIGUOUS_NUMERIC_RE.match(text)
    if ambiguous:
        first, second = int(ambiguous.group(1)), int(ambiguous.group(2))
        if first <= 12 and second <= 12 and first != second:
            flags.append(
                Flag(
                    "AMBIGUOUS_DATE",
                    "warning",
                    field,
                    f"{field} {text!r} is day-first or month-first depending on the source; "
                    f"read as day-first ({first:02d} of month {second:02d})",
                )
            )

    # ISO-8601 first, so offsets and a trailing Z are handled rather than
    # rejected as corrupt. Only the calendar date is kept.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date(), flags
    except ValueError:
        pass

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), flags
        except ValueError:
            continue

    flags.append(
        Flag("UNPARSEABLE_DATE", "error", field, f"{field} could not be parsed from {text!r}")
    )
    return None, flags


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


def _normalise_key(raw: str) -> str:
    """Fold a feed's status spelling down to a lookup key.

    Hyphens and underscores become spaces so "in-progress", "in_progress" and
    "In Progress" are one key; edge punctuation ("HIGH!!") is trimmed.
    """
    text = _WS_RE.sub(" ", str(raw).strip().lower())
    text = re.sub(r"[-_]+", " ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text.strip(" .!?*/")


def coerce_directive_status(raw: str | None) -> tuple[DirectiveStatus, list[Flag]]:
    if raw is None or not str(raw).strip():
        return DirectiveStatus.QUARANTINED, [
            Flag("MISSING_STATUS", "error", "status", "Directive has no status code")
        ]

    flags: list[Flag] = []
    key = _normalise_key(raw)

    # "ACTIVE/SUPERSEDED" - the feed disagreeing with itself.
    parts = [p.strip() for p in re.split(r"[/|,;]| and ", key) if p.strip()]
    resolved = [_DIRECTIVE_STATUS_ALIASES[p] for p in parts if p in _DIRECTIVE_STATUS_ALIASES]
    unrecognised = [p for p in parts if p not in _DIRECTIVE_STATUS_ALIASES]

    # Half-understood is not understood: resolving "ACTIVE/GIBBERISH" to
    # IN_PROGRESS would mean trusting a field we only partly parsed.
    if resolved and unrecognised:
        return DirectiveStatus.QUARANTINED, [
            Flag(
                "UNKNOWN_STATUS",
                "error",
                "status",
                f"Status {raw!r} contains an unrecognised term "
                f"({', '.join(repr(u) for u in unrecognised)}) alongside a known one",
            )
        ]

    if len(set(resolved)) > 1:
        flags.append(
            Flag(
                "CONFLICTING_STATUS",
                "error",
                "status",
                f"Source reports mutually exclusive statuses: {raw!r}",
            )
        )
        return DirectiveStatus.QUARANTINED, flags

    if resolved:
        # Mapping the regulator's vocabulary onto ours ("in force" ->
        # IN_PROGRESS) is routine translation, not damage, so it raises
        # nothing. Only genuine noise -- stray punctuation, or several terms
        # crammed into one field -- is worth a reader's attention.
        noisy = len(parts) > 1 or key != str(raw).strip().lower()
        if noisy:
            flags.append(
                Flag(
                    "NORMALISED_STATUS",
                    "warning",
                    "status",
                    f"Status {raw!r} needed cleanup before it mapped to {resolved[0].value}",
                )
            )
        return resolved[0], flags

    return DirectiveStatus.QUARANTINED, [
        Flag("UNKNOWN_STATUS", "error", "status", f"Unrecognised status code {raw!r}")
    ]


def coerce_severity(raw: str | None, *, field: str = "severity") -> tuple[Severity, list[Flag]]:
    if raw is None or not str(raw).strip():
        return Severity.UNKNOWN, [
            Flag("MISSING_SEVERITY", "warning", field, "No severity supplied; treated as UNKNOWN")
        ]

    key = _normalise_key(raw)
    if key in _SEVERITY_ALIASES:
        value = _SEVERITY_ALIASES[key]
        if str(raw).strip() != value.value:
            return value, [
                Flag(
                    "NORMALISED_SEVERITY",
                    "warning",
                    field,
                    f"Severity {raw!r} normalised to {value.value}",
                )
            ]
        return value, []

    return Severity.UNKNOWN, [
        Flag("UNKNOWN_SEVERITY", "warning", field, f"Unrecognised severity {raw!r}")
    ]


def coerce_action_status(raw: str | None) -> tuple[ActionStatus, list[Flag]]:
    if raw is None or not str(raw).strip():
        return ActionStatus.UNKNOWN, [
            Flag("MISSING_STATUS", "error", "status", "Action item has no status - needs triage")
        ]

    key = _normalise_key(raw)
    if key in _ACTION_STATUS_ALIASES:
        value = _ACTION_STATUS_ALIASES[key]
        if str(raw).strip() != value.value:
            return value, [
                Flag(
                    "NORMALISED_STATUS",
                    "warning",
                    "status",
                    f"Status {raw!r} normalised to {value.value}",
                )
            ]
        return value, []

    return ActionStatus.UNKNOWN, [
        Flag("UNKNOWN_STATUS", "error", "status", f"Unrecognised action status {raw!r} - needs triage")
    ]


def normalise_owner(raw: str | None) -> tuple[str | None, list[Flag]]:
    owner, flags = clean_text(raw, field="owner", max_len=120)
    if owner is None or owner.strip().lower() in _UNASSIGNED_OWNERS:
        return None, flags + [
            Flag("UNASSIGNED_OWNER", "warning", "owner", "No accountable owner on this action item")
        ]
    return owner, flags
