"""Relational schema: Authority -> Directive -> ActionItem.

Design note on "raw" columns
----------------------------
Anything that arrives from an external regulatory feed is stored exactly as
received (`*_raw` columns, plain TEXT, nullable). An ingest pipeline that
rejects a row because a date reads "Q3-2024" loses the row entirely, which is
worse than storing it and flagging it. Canonicalisation happens in
`sanitize.py` at the API boundary, and only canonical values are ever written
back by our own endpoints.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Authority(Base):
    """A regulatory body (FDA, EMA, PMDA, ...). Reference data: kept clean."""

    __tablename__ = "authorities"
    __table_args__ = (UniqueConstraint("code", name="uq_authority_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    country: Mapped[str] = mapped_column(String(80), nullable=False)
    region: Mapped[str] = mapped_column(String(40), nullable=False)

    directives: Mapped[list["Directive"]] = relationship(
        back_populates="authority", cascade="all, delete-orphan"
    )


class Directive(Base):
    """A compliance directive / regulatory update issued by an Authority.

    `reference_code` is deliberately NOT unique: real feeds re-publish the same
    reference under amendments, and detecting those duplicates is part of the
    triage job rather than something to reject at write time.
    """

    __tablename__ = "directives"
    __table_args__ = (
        Index("ix_directives_authority", "authority_id"),
        Index("ix_directives_reference_code", "reference_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    authority_id: Mapped[int] = mapped_column(
        ForeignKey("authorities.id", ondelete="CASCADE"), nullable=False
    )

    reference_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Regulatory domain ("Pharmacovigilance", "Good Manufacturing Practice").
    #: Free text, because feeds disagree on their own taxonomies.
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Free-text as received from the feed; normalised in sanitize.py.
    raw_status: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_severity: Mapped[str | None] = mapped_column(String(120), nullable=True)

    published_at_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Consultation response deadline ("comments by"), distinct from the
    #: compliance deadline below: one closes the comment window, the other
    #: is when the obligation bites.
    comments_by_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_date_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deadline_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)

    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    authority: Mapped[Authority] = relationship(back_populates="directives")
    action_items: Mapped[list["ActionItem"]] = relationship(
        back_populates="directive",
        cascade="all, delete-orphan",
        order_by="ActionItem.id",
    )


class ActionItem(Base):
    """The unit a compliance officer actually triages."""

    __tablename__ = "action_items"
    __table_args__ = (
        Index("ix_action_items_directive", "directive_id"),
        Index("ix_action_items_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    directive_id: Mapped[int] = mapped_column(
        ForeignKey("directives.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # TEXT, not an enum: seeded rows may carry corrupt codes ("DONE?", "pending ").
    # Our own endpoints only ever write canonical ActionStatus values.
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)

    due_date_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    directive: Mapped[Directive] = relationship(back_populates="action_items")
