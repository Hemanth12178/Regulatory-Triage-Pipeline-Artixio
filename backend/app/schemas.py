"""Write-side validation.

Reads are permissive (flag, never crash). Writes are strict: the only values
that ever reach the database through this API are canonical ones.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .sanitize import ASSIGNABLE_ACTION_STATUSES, ActionStatus, clean_text

MAX_NOTE_LEN = 2000


class ActionItemUpdate(BaseModel):
    """PATCH body. Every field optional, but the body may not be empty."""

    model_config = ConfigDict(extra="forbid")

    status: ActionStatus | None = None
    owner: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=MAX_NOTE_LEN)
    #: Optimistic concurrency token echoed back from a previous read.
    expected_updated_at: datetime | None = None

    @field_validator("status")
    @classmethod
    def _status_must_be_assignable(cls, value: ActionStatus | None) -> ActionStatus | None:
        if value is not None and value not in ASSIGNABLE_ACTION_STATUSES:
            allowed = ", ".join(sorted(s.value for s in ASSIGNABLE_ACTION_STATUSES))
            raise ValueError(
                f"{value.value} cannot be assigned by a user. Allowed: {allowed}"
            )
        return value

    @field_validator("owner", "notes")
    @classmethod
    def _sanitise_free_text(cls, value: str | None) -> str | None:
        """An officer's own input is cleaned the same way ingested text is.

        Input that cleans away to nothing ("   ", "<b></b>") is rejected
        rather than quietly becoming None: returning None here would make the
        field look absent, and the request would be refused as "no changes" -
        an error message about something the caller never did.
        """
        if value is None:
            return None
        cleaned, _ = clean_text(value, field="input", max_len=MAX_NOTE_LEN)
        if cleaned is None:
            raise ValueError("contains no readable text once markup is stripped")
        return cleaned

    def has_changes(self) -> bool:
        return any(
            getattr(self, field) is not None
            for field in ("status", "owner", "notes")
        )
