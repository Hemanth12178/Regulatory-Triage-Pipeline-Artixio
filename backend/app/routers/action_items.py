"""Action item triage: the only write path in the application."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import ActionItem
from ..sanitize import ALLOWED_TRANSITIONS, ActionStatus, coerce_action_status, parse_date
from ..schemas import ActionItemUpdate
from ..services import serialize_action_item

router = APIRouter(prefix="/api/action-items", tags=["action-items"])


def _get_or_404(session: Session, item_id: int) -> ActionItem:
    item = session.get(ActionItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Action item {item_id} not found")
    return item


@router.get("/{item_id}")
def get_action_item(
    item_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    item = _get_or_404(session, item_id)
    deadline, _ = parse_date(item.directive.deadline_raw, field="deadline")
    return serialize_action_item(item, directive_deadline=deadline)


@router.patch("/{item_id}")
def update_action_item(
    item_id: int,
    payload: ActionItemUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    item = _get_or_404(session, item_id)

    if not payload.has_changes():
        raise HTTPException(
            status_code=400,
            detail="Request body contains no changes (status, owner or notes required).",
        )

    # Optimistic concurrency: refuse to clobber a concurrent edit.
    if payload.expected_updated_at is not None and item.updated_at is not None:
        current = item.updated_at
        expected = payload.expected_updated_at
        if expected.tzinfo is not None:
            expected = expected.astimezone(timezone.utc).replace(tzinfo=None)
        if current.tzinfo is not None:
            current = current.astimezone(timezone.utc).replace(tzinfo=None)
        # Millisecond tolerance only. A one-second window was wide enough for
        # two edits in the same second to both look fresh, which is precisely
        # the race this check exists to stop; isoformat() round-trips
        # microseconds, so nothing is lost by being strict.
        if abs((current - expected).total_seconds()) > 0.001:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This action item changed since you loaded it "
                    f"(server: {item.updated_at.isoformat()}). Reload before retrying."
                ),
            )

    if payload.status is not None:
        # The stored value may be corrupt; interpret it the same way reads do.
        current_status, _ = coerce_action_status(item.status)
        target = payload.status

        if target is not current_status:
            allowed = ALLOWED_TRANSITIONS.get(current_status, set())
            if target not in allowed:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot move {current_status.value} -> {target.value}. "
                        f"Allowed from {current_status.value}: "
                        f"{', '.join(sorted(s.value for s in allowed)) or 'none'}."
                    ),
                )

        effective_notes = payload.notes if payload.notes is not None else item.notes
        if target is ActionStatus.DISMISSED and not (effective_notes or "").strip():
            raise HTTPException(
                status_code=422,
                detail="Dismissing an action item requires a justification note.",
            )

        item.status = target.value

        # Keep the resolution timestamp consistent with the state machine
        # instead of leaving the inconsistency the seed data demonstrates.
        # DISMISSED closes the item too, so it is stamped like RESOLVED -
        # leaving it untouched let a dismissed item keep a stale timestamp
        # that no validation rule would ever flag.
        if target in (ActionStatus.RESOLVED, ActionStatus.DISMISSED):
            item.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            item.resolved_at = None

    if payload.owner is not None:
        # Empty / markup-only input is already rejected by the schema.
        item.owner = payload.owner.strip()

    if payload.notes is not None:
        item.notes = payload.notes.strip() or None

    session.commit()
    session.refresh(item)

    deadline, _ = parse_date(item.directive.deadline_raw, field="deadline")
    return serialize_action_item(item, directive_deadline=deadline)
