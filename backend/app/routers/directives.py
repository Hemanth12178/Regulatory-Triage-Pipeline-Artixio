"""Directive listing: filter / sort / paginate over the sanitised projection."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_session
from ..models import ActionItem, Authority, Directive
from ..sanitize import (
    ALLOWED_TRANSITIONS,
    ASSIGNABLE_ACTION_STATUSES,
    FLAG_CODES,
    OPEN_ACTION_STATUSES,
    ActionStatus,
    DirectiveStatus,
    Severity,
    coerce_action_status,
    normalise_owner,
)
from ..services import build_stats, duplicate_reference_codes, serialize_directive

router = APIRouter(prefix="/api", tags=["directives"])

MAX_PAGE_SIZE = 200

#: Sentinel so "who has nothing assigned?" is a first-class filter choice.
#: An owner the sanitiser rejected ("", "unassigned", "TBD") normalises to
#: None, and those items are exactly the ones nobody is accountable for.
UNASSIGNED = "__unassigned__"

#: Sentinel flag value meaning "any flag at all". Lets the UI offer a single
#: "show me only flagged records" choice without the caller having to OR every
#: code together. Kept out of FLAG_CODES on purpose - it is not a real code.
ANY_FLAG = "ANY"

SEVERITY_ORDER = {
    Severity.CRITICAL.value: 0,
    Severity.HIGH.value: 1,
    Severity.MEDIUM.value: 2,
    Severity.LOW.value: 3,
    Severity.UNKNOWN.value: 4,
}
QUALITY_ORDER = {"error": 0, "warning": 1, "ok": 2}


def _load_all(session: Session, today: date | None = None) -> list[dict]:
    """Every directive, fully sanitised.

    Canonical status/severity/dates are derived rather than stored, so filtering
    happens on the projection. At this dataset size that is the honest, simple
    choice; a production ingest would persist canonical columns alongside the
    raw ones and push these predicates into SQL.
    """
    directives = (
        session.execute(
            select(Directive)
            .options(
                selectinload(Directive.authority),
                selectinload(Directive.action_items),
            )
            .order_by(Directive.id)
        )
        .scalars()
        .all()
    )
    duplicates = duplicate_reference_codes(session)
    return [serialize_directive(d, duplicate_codes=duplicates, today=today) for d in directives]


def _matches_search(row: dict, needle: str) -> bool:
    haystack = " ".join(
        str(part or "")
        for part in (
            row["reference_code"],
            row["title"],
            row["summary"],
            row["category"],
            row["authority"]["code"],
            row["authority"]["name"],
            *(item["title"] or "" for item in row["action_items"]),
            *(item["owner"] or "" for item in row["action_items"]),
        )
    ).lower()
    return needle in haystack


def _owner_key(item: dict) -> str:
    """Comparison key for one action item's owner."""
    return item["owner"].strip().lower() if item["owner"] else UNASSIGNED


def _owners_of(row: dict) -> set[str]:
    """Canonical owners on a directive's action items, lower-cased."""
    return {_owner_key(item) for item in row["action_items"]}


@router.get("/directives")
def list_directives(
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=200, description="Free-text search")] = None,
    authority: Annotated[list[str] | None, Query(description="Authority code, repeatable")] = None,
    status: Annotated[list[DirectiveStatus] | None, Query()] = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    quality: Annotated[list[Literal["ok", "warning", "error"]] | None, Query()] = None,
    flag: Annotated[list[str] | None, Query(description="Only rows carrying this flag code")] = None,
    owner: Annotated[
        list[str] | None,
        Query(description=f"Action item owner; {UNASSIGNED} matches items with no owner"),
    ] = None,
    owner_status: Annotated[
        list[ActionStatus] | None,
        Query(description="Owner status: the status of the owner's action items"),
    ] = None,
    only_open: Annotated[bool, Query(description="Only directives with open action items")] = False,
    sort: Annotated[
        Literal["number", "deadline", "published", "severity", "quality", "authority", "reference"],
        Query(),
    ] = "quality",
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
) -> dict:
    rows = _load_all(session)

    if q and q.strip():
        needle = q.strip().lower()
        rows = [r for r in rows if _matches_search(r, needle)]

    if authority:
        wanted = {code.strip().upper() for code in authority if code.strip()}
        if wanted:
            rows = [r for r in rows if r["authority"]["code"].upper() in wanted]

    if status:
        wanted_status = {s.value for s in status}
        rows = [r for r in rows if r["status"] in wanted_status]

    if severity:
        wanted_sev = {s.value for s in severity}
        rows = [r for r in rows if r["severity"] in wanted_sev]

    if owner_status:
        # Keep a directive when any of its action items is in one of the wanted
        # states - "show me everything with blocked work", etc.
        wanted_owner_status = {s.value for s in owner_status}
        rows = [
            r
            for r in rows
            if any(item["status"] in wanted_owner_status for item in r["action_items"])
        ]

    if quality:
        wanted_quality = set(quality)
        rows = [r for r in rows if r["rollup_quality"] in wanted_quality]

    wanted_flags: set[str] = set()
    if flag:
        requested = {f.strip().upper() for f in flag if f.strip()}
        # ANY is a sentinel ("carries any flag"), not a real code, so pull it
        # out before validating the rest against the known vocabulary.
        match_any_flag = ANY_FLAG in requested
        wanted_flags = requested - {ANY_FLAG}
        # An unknown code used to return an empty page, which reads as "no
        # matches" when it really means "you asked for something that cannot
        # exist". status and severity already 422 on a bad value.
        unknown = sorted(wanted_flags - FLAG_CODES)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown flag code(s): {', '.join(unknown)}. "
                    f"Valid codes: {ANY_FLAG}, {', '.join(sorted(FLAG_CODES))}"
                ),
            )
        if match_any_flag or wanted_flags:

            def _codes(row: dict) -> set[str]:
                codes = {f["code"] for f in row["flags"]}
                for item in row["action_items"]:
                    codes |= {f["code"] for f in item["flags"]}
                return codes

            def _matches_flag(row: dict) -> bool:
                codes = _codes(row)
                # "Any flag" matches a row that carries at least one; a specific
                # code list matches on intersection. The two OR together so a
                # caller can combine them, though the UI only sends one.
                if match_any_flag and codes:
                    return True
                return bool(wanted_flags & codes)

            rows = [r for r in rows if _matches_flag(r)]

    wanted_owners: set[str] = set()
    if owner:
        wanted_owners = {o.strip().lower() for o in owner if o.strip()}
        if wanted_owners:
            rows = [r for r in rows if wanted_owners & _owners_of(r)]

    if only_open:
        # Combined with an owner filter this means "*that owner* still has
        # open work", not "someone does". Filtering both at directive level
        # would surface rows where the named person is already finished.
        if wanted_owners:
            rows = [
                r
                for r in rows
                if any(
                    item["is_open"] and _owner_key(item) in wanted_owners
                    for item in r["action_items"]
                )
            ]
        else:
            rows = [r for r in rows if r["open_item_count"] > 0]

    # Sort. Missing values sort last regardless of direction - a record with no
    # deadline is not "the most urgent", it is unschedulable.
    far_future = date.max.isoformat()
    key_funcs = {
        "number": lambda r: (r["id"],),
        "deadline": lambda r: (r["deadline"] is None, r["deadline"] or far_future),
        "published": lambda r: (r["published_at"] is None, r["published_at"] or far_future),
        "severity": lambda r: (SEVERITY_ORDER.get(r["severity"], 99), r["id"]),
        "quality": lambda r: (
            QUALITY_ORDER.get(r["rollup_quality"], 99),
            SEVERITY_ORDER.get(r["severity"], 99),
            r["id"],
        ),
        "authority": lambda r: (r["authority"]["code"], r["id"]),
        "reference": lambda r: (r["reference_code"] is None, r["reference_code"] or "", r["id"]),
    }
    rows.sort(key=key_funcs[sort], reverse=(order == "desc"))

    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    return {
        "items": page_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "stats": build_stats(rows),
    }


@router.get("/directives/{directive_id}")
def get_directive(
    directive_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    directive = session.get(Directive, directive_id)
    if directive is None:
        raise HTTPException(status_code=404, detail=f"Directive {directive_id} not found")
    return serialize_directive(directive, duplicate_codes=duplicate_reference_codes(session))


@router.get("/authorities")
def list_authorities(session: Annotated[Session, Depends(get_session)]) -> list[dict]:
    authorities = (
        session.execute(select(Authority).order_by(Authority.code)).scalars().all()
    )
    return [
        {
            "id": a.id,
            "code": a.code,
            "name": a.name,
            "country": a.country,
            "region": a.region,
        }
        for a in authorities
    ]


@router.get("/owners")
def list_owners(session: Annotated[Session, Depends(get_session)]) -> list[dict]:
    """Distinct action item owners, with workload counts.

    Derived from the sanitised projection rather than from the raw column, so
    " R. Mehta " and "R. Mehta" are one person and the junk values collapse
    into a single `__unassigned__` bucket.
    """
    # Only the two columns needed, rather than serialising every directive:
    # this endpoint loads on each page view and the full projection is an
    # expensive way to answer "who owns what".
    rows = session.execute(select(ActionItem.owner, ActionItem.status)).all()

    tally: dict[str, dict] = {}
    for raw_owner, raw_status in rows:
        owner, _ = normalise_owner(raw_owner)
        status, _ = coerce_action_status(raw_status)
        key = owner.strip() if owner else UNASSIGNED
        entry = tally.setdefault(
            key,
            {
                "value": key,
                "label": "Unassigned" if key == UNASSIGNED else key,
                "total_items": 0,
                "open_items": 0,
            },
        )
        entry["total_items"] += 1
        if status in OPEN_ACTION_STATUSES:
            entry["open_items"] += 1

    # Real people alphabetically; the unassigned bucket pinned last.
    owners = sorted(
        tally.values(),
        key=lambda o: (o["value"] == UNASSIGNED, o["label"].lower()),
    )
    return owners


@router.get("/vocabulary")
def vocabulary() -> dict:
    """Canonical enums, so the frontend never hard-codes a second copy.

    Includes the action item state machine: a copy of those rules in the UI
    drifts the moment the server's change, and the user only finds out when a
    legal transition is greyed out or an illegal one is offered.
    """
    return {
        "directive_status": [s.value for s in DirectiveStatus],
        "severity": [s.value for s in Severity],
        "action_status": [s.value for s in ActionStatus],
        "assignable_action_status": [
            s.value for s in ActionStatus if s in ASSIGNABLE_ACTION_STATUSES
        ],
        "action_transitions": {
            current.value: sorted(target.value for target in targets)
            for current, targets in ALLOWED_TRANSITIONS.items()
        },
        "quality": ["ok", "warning", "error"],
        "flag_codes": sorted(FLAG_CODES),
    }
