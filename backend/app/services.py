"""Read-side projection: ORM rows -> validated, flagged API payloads.

Single-field cleaning lives in ``sanitize``. This module adds the checks that
need more than one field (or more than one row) to make a judgement:
chronology, status/child conflicts, overdue work and duplicate references.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import sanitize as san
from .models import ActionItem, Directive
from .sanitize import Flag


def canonical_reference(raw: str | None) -> str | None:
    """The comparison key for a reference code: cleaned, trimmed, upper-cased."""
    cleaned, _ = san.clean_text(raw, field="reference_code", max_len=64)
    if cleaned is None:
        return None
    return cleaned.strip().upper()


def duplicate_reference_codes(session: Session) -> set[str]:
    """Reference codes carried by more than one directive.

    Grouping in SQL would compare the *raw* strings, so "EMA/CHMP/123",
    "ema/chmp/123" and "EMA/CHMP/123 " would count as three distinct codes and
    no duplicate would ever be reported - which is exactly the re-ingest case
    this check exists to catch. The comparison therefore happens on the same
    canonical form the serialiser shows the user. Only the code column is
    loaded, so this stays a single cheap query.
    """
    codes = session.execute(
        select(Directive.reference_code).where(Directive.reference_code.is_not(None))
    ).scalars()

    tally = Counter(filter(None, (canonical_reference(code) for code in codes)))
    return {code for code, count in tally.items() if count > 1}


# --------------------------------------------------------------------------
# Action items
# --------------------------------------------------------------------------


def serialize_action_item(
    item: ActionItem,
    *,
    directive_deadline: date | None = None,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    flags: list[Flag] = []

    title, f = san.clean_text(item.title, field="title", required=True, max_len=300)
    flags += f
    owner, f = san.normalise_owner(item.owner)
    flags += f
    status, f = san.coerce_action_status(item.status)
    flags += f
    priority, f = san.coerce_severity(item.priority_raw, field="priority")
    flags += f
    due_date, f = san.parse_date(item.due_date_raw, field="due_date")
    flags += f
    notes, f = san.clean_text(item.notes, field="notes")
    flags += f

    is_open = status in san.OPEN_ACTION_STATUSES

    # Resolution bookkeeping must agree with the status.
    if status is san.ActionStatus.RESOLVED and item.resolved_at is None:
        flags.append(
            Flag(
                "RESOLVED_WITHOUT_TIMESTAMP",
                "warning",
                "resolved_at",
                "Marked resolved but carries no resolution timestamp",
            )
        )
    if is_open and item.resolved_at is not None:
        flags.append(
            Flag(
                "TIMESTAMP_WITHOUT_RESOLUTION",
                "error",
                "resolved_at",
                "Has a resolution timestamp while still in an open state",
            )
        )

    if due_date and is_open and due_date < today:
        flags.append(
            Flag(
                "OVERDUE",
                "error",
                "due_date",
                f"Due {due_date.isoformat()} and still open ({(today - due_date).days}d overdue)",
            )
        )

    if due_date and directive_deadline and due_date > directive_deadline:
        flags.append(
            Flag(
                "DUE_AFTER_DIRECTIVE_DEADLINE",
                "error",
                "due_date",
                f"Due {due_date.isoformat()} is after the directive deadline "
                f"{directive_deadline.isoformat()}",
            )
        )

    flags = san.dedupe(flags)

    return {
        "id": item.id,
        "directive_id": item.directive_id,
        "title": title,
        "owner": owner,
        "status": status.value,
        "raw_status": item.status,
        "priority": priority.value,
        "raw_priority": item.priority_raw,
        "due_date": due_date.isoformat() if due_date else None,
        "raw_due_date": item.due_date_raw,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "notes": notes,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "is_open": is_open,
        "quality": san.quality_of(flags),
        "flags": [fl.as_dict() for fl in flags],
    }


# --------------------------------------------------------------------------
# Directives
# --------------------------------------------------------------------------


def serialize_directive(
    directive: Directive,
    *,
    duplicate_codes: set[str] | None = None,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    duplicate_codes = duplicate_codes or set()
    flags: list[Flag] = []

    reference_code, f = san.clean_text(
        directive.reference_code, field="reference_code", required=True, max_len=64
    )
    flags += f
    title, f = san.clean_text(directive.title, field="title", required=True, max_len=300)
    flags += f
    summary, f = san.clean_text(directive.summary, field="summary")
    flags += f
    category, f = san.clean_text(directive.category, field="category", max_len=80)
    flags += f
    status, f = san.coerce_directive_status(directive.raw_status)
    flags += f
    severity, f = san.coerce_severity(directive.raw_severity)
    flags += f

    published_at, f = san.parse_date(directive.published_at_raw, field="published_at")
    flags += f
    comments_by, f = san.parse_date(directive.comments_by_raw, field="comments_by")
    flags += f
    effective_date, f = san.parse_date(directive.effective_date_raw, field="effective_date")
    flags += f
    deadline, f = san.parse_date(directive.deadline_raw, field="deadline")
    flags += f

    if reference_code and reference_code.strip().upper() in duplicate_codes:
        flags.append(
            Flag(
                "DUPLICATE_REFERENCE",
                "warning",
                "reference_code",
                f"{reference_code} is used by more than one directive - possible amendment or re-ingest",
            )
        )

    if published_at and effective_date and effective_date < published_at:
        flags.append(
            Flag(
                "DATE_CHRONOLOGY",
                "error",
                "effective_date",
                f"Effective {effective_date.isoformat()} precedes publication {published_at.isoformat()}",
            )
        )

    if effective_date and deadline and deadline < effective_date:
        flags.append(
            Flag(
                "DATE_CHRONOLOGY",
                "error",
                "deadline",
                f"Deadline {deadline.isoformat()} precedes the effective date "
                f"{effective_date.isoformat()}",
            )
        )

    items = [
        serialize_action_item(item, directive_deadline=deadline, today=today)
        for item in directive.action_items
    ]
    open_items = [i for i in items if i["is_open"]]

    if status is san.DirectiveStatus.IN_PROGRESS and deadline is None:
        flags.append(
            Flag(
                "MISSING_DEADLINE",
                "warning",
                "deadline",
                "Directive is in force with no compliance deadline",
            )
        )

    if status is san.DirectiveStatus.RESOLVED and open_items:
        flags.append(
            Flag(
                "STATUS_CONFLICT",
                "error",
                "status",
                f"Directive is {status.value} but {len(open_items)} action item(s) are still open",
            )
        )

    if deadline and deadline < today and open_items:
        flags.append(
            Flag(
                "OVERDUE",
                "error",
                "deadline",
                f"Compliance deadline passed {(today - deadline).days}d ago with "
                f"{len(open_items)} item(s) open",
            )
        )

    flags = san.dedupe(flags)
    own_quality = san.quality_of(flags)
    qualities = [own_quality] + [i["quality"] for i in items]
    rollup = (
        "error" if "error" in qualities else "warning" if "warning" in qualities else "ok"
    )

    return {
        "id": directive.id,
        "reference_code": reference_code,
        "title": title,
        "summary": summary,
        "category": category,
        "status": status.value,
        "raw_status": directive.raw_status,
        "severity": severity.value,
        "raw_severity": directive.raw_severity,
        "published_at": published_at.isoformat() if published_at else None,
        "comments_by": comments_by.isoformat() if comments_by else None,
        "effective_date": effective_date.isoformat() if effective_date else None,
        "deadline": deadline.isoformat() if deadline else None,
        "raw_dates": {
            "published_at": directive.published_at_raw,
            "comments_by": directive.comments_by_raw,
            "effective_date": directive.effective_date_raw,
            "deadline": directive.deadline_raw,
        },
        "source_url": directive.source_url,
        "authority": {
            "id": directive.authority.id,
            "code": directive.authority.code,
            "name": directive.authority.name,
            "country": directive.authority.country,
            "region": directive.authority.region,
        },
        "action_items": items,
        "open_item_count": len(open_items),
        "total_item_count": len(items),
        "days_to_deadline": (deadline - today).days if deadline else None,
        "quality": own_quality,
        "rollup_quality": rollup,
        "flags": [fl.as_dict() for fl in flags],
    }


# --------------------------------------------------------------------------
# Aggregates for the (deliberately small) header strip
# --------------------------------------------------------------------------


def build_stats(rows: list[dict]) -> dict:
    """Counters computed from the *sanitised* projection, not from raw columns."""
    flag_counter: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()
    by_quality: Counter[str] = Counter()
    open_items = 0
    total_items = 0
    overdue = 0

    for row in rows:
        by_status[row["status"]] += 1
        by_severity[row["severity"]] += 1
        by_quality[row["rollup_quality"]] += 1
        open_items += row["open_item_count"]
        total_items += row["total_item_count"]

        row_flags = list(row["flags"])
        for item in row["action_items"]:
            row_flags += item["flags"]
        for flag in row_flags:
            flag_counter[flag["code"]] += 1
        if any(f["code"] == "OVERDUE" for f in row_flags):
            overdue += 1

    return {
        "directives": len(rows),
        "action_items": total_items,
        "open_action_items": open_items,
        "overdue_directives": overdue,
        "by_status": dict(by_status),
        "by_severity": dict(by_severity),
        "by_quality": {
            "ok": by_quality.get("ok", 0),
            "warning": by_quality.get("warning", 0),
            "error": by_quality.get("error", 0),
        },
        "by_flag": dict(flag_counter.most_common()),
    }
