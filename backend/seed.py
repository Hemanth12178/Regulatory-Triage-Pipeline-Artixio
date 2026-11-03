"""Seed the database with a mock regulatory feed.

Roughly two thirds of the rows are well-formed. The rest are the kind of
damage a real ingest produces, and every one of them is annotated with the
flag code the API is expected to raise for it. Run:

    python seed.py            # drop + recreate + seed
    python seed.py --keep     # seed only if the database is empty
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.db import DEFAULT_SQLITE_PATH, Base, SessionLocal, engine
from app.models import ActionItem, Authority, Directive

TODAY = date.today()
random.seed(20251030)  # reproducible mock data

#: Total rows in the mock corpus. The hand-written and extra anomalies are a
#: fixed set; the clean generator fills whatever is left up to this total.
TOTAL_DIRECTIVES = 100


def now() -> datetime:
    """Naive UTC, matching the DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def day(offset: int, fmt: str = "%Y-%m-%d") -> str:
    """A date `offset` days from today, rendered in one of several feed formats."""
    return (TODAY + timedelta(days=offset)).strftime(fmt)


AUTHORITIES = [
    ("FDA", "U.S. Food and Drug Administration", "United States", "Americas"),
    ("EMA", "European Medicines Agency", "European Union", "EMEA"),
    ("MHRA", "Medicines and Healthcare products Regulatory Agency", "United Kingdom", "EMEA"),
    ("PMDA", "Pharmaceuticals and Medical Devices Agency", "Japan", "APAC"),
    ("CDSCO", "Central Drugs Standard Control Organisation", "India", "APAC"),
    ("TGA", "Therapeutic Goods Administration", "Australia", "APAC"),
    ("HC", "Health Canada", "Canada", "Americas"),
    ("ANVISA", "Agencia Nacional de Vigilancia Sanitaria", "Brazil", "Americas"),
]

CLEAN_TOPICS = [
    ("Annual stability testing protocol update", "stability data submission windows for ICH zones IVa/IVb"),
    ("Revised pharmacovigilance master file requirements", "PSMF structure, QPPV contact data and audit trails"),
    ("Electronic submission gateway migration", "eCTD v4.0 validation criteria for new marketing applications"),
    ("Nitrosamine impurity risk assessment extension", "step 2 confirmatory testing deadlines for sartans"),
    ("Updated GMP annex on sterile manufacturing", "contamination control strategy and barrier technology"),
    ("Paediatric investigation plan waiver criteria", "age-appropriate formulation justification requirements"),
    ("Serialisation and track-and-trace reporting", "aggregation data reporting via the national hub"),
    ("Post-authorisation safety study protocol template", "PASS protocol submission and interim reporting cadence"),
    ("Clinical trial transparency disclosure rules", "results posting within 12 months of study completion"),
    ("Biosimilar comparability exercise guidance", "analytical similarity assessment tiering"),
    ("Medical device software classification update", "rule 11 reclassification for clinical decision support"),
    ("Import licence renewal procedure change", "Form 41 renewal lead times and fee schedule"),
    ("Excipient supplier qualification standard", "risk-based audit frequency for high-risk excipients"),
    ("Labelling update for pregnancy warnings", "PLLR-aligned narrative labelling sections"),
    ("Batch release testing site transfer notice", "QP certification continuity during site transfer"),
    ("Cold chain distribution deviation reporting", "temperature excursion documentation thresholds"),
]

#: Aligned index-for-index with CLEAN_TOPICS, so a title and its regulatory
#: domain stay coherent.
TOPIC_CATEGORIES = [
    "Quality & CMC",
    "Pharmacovigilance",
    "Market Authorisation",
    "Quality & CMC",
    "Good Manufacturing Practice",
    "Clinical Trials",
    "Supply Chain",
    "Pharmacovigilance",
    "Clinical Trials",
    "Quality & CMC",
    "Medical Devices",
    "Import & Export",
    "Good Manufacturing Practice",
    "Labelling & Packaging",
    "Good Manufacturing Practice",
    "Supply Chain",
]

OWNERS = [
    "A. Fernandes", "R. Mehta", "S. Okonkwo", "L. Zhang", "D. Mueller",
    "P. Nakamura", "K. O'Brien", "M. Rossi", "T. Dube", "J. Lindqvist",
]

ACTION_TEMPLATES = [
    "Gap-assess current SOP against the new clause",
    "Update site master file section",
    "Brief QA and manufacturing leads",
    "Submit variation dossier to the authority",
    "Re-validate the reporting template",
    "Confirm supplier attestations on file",
    "Schedule internal audit of affected process",
    "File acknowledgement with the national agency",
]


#: Feed vocabulary for well-formed rows. These are the regulators' own words;
#: `sanitize.coerce_directive_status` maps them onto our five workflow states.
CLEAN_FEED_STATUSES = [
    "ACTIVE",
    "ACTIVE",
    "in force",
    "PROPOSED",
    "DRAFT",
    "consultation",
    "superseded",
    "on hold",
]

#: Qualifiers that keep 86 generated titles from reading as 16 repeated ones.
TOPIC_SCOPES = [
    "",
    " — phase 2 rollout",
    " for biologics",
    " for generic applicants",
    " — small manufacturer exemption",
    " for combination products",
]


def build_clean_directives(
    authorities: dict[str, Authority], count: int
) -> list[Directive]:
    """Well-formed rows, so the flagged ones stand out rather than drown."""
    rows: list[Directive] = []
    codes = list(authorities)

    for index in range(count):
        title_base, detail = CLEAN_TOPICS[index % len(CLEAN_TOPICS)]
        scope = TOPIC_SCOPES[(index // len(CLEAN_TOPICS)) % len(TOPIC_SCOPES)]
        title = f"{title_base}{scope}"

        code = codes[index % len(codes)]
        # Forward-looking by construction: an overdue clean row would be
        # indistinguishable from the overdue rows seeded on purpose below.
        published = -90 + (index % 30) * 3
        effective = published + 45
        deadline = effective + 60 + (index % 5) * 14
        severity = ["LOW", "MEDIUM", "HIGH", "CRITICAL"][index % 4]
        status = CLEAN_FEED_STATUSES[index % len(CLEAN_FEED_STATUSES)]

        # Not every directive runs a consultation, so roughly a third carry no
        # comment deadline at all. That absence is normal, not an anomaly.
        comments_by = day(published + 30) if index % 3 else None

        directive = Directive(
            authority=authorities[code],
            reference_code=f"{code}-{2025 + index % 2}-{100 + index:03d}",
            title=title,
            category=TOPIC_CATEGORIES[index % len(TOPIC_CATEGORIES)],
            comments_by_raw=comments_by,
            summary=(
                f"The {code} has published guidance covering {detail}. "
                "Affected marketing authorisation holders must review internal "
                "procedures and document the impact assessment."
            ),
            raw_status=status,
            raw_severity=severity,
            published_at_raw=day(published),
            effective_date_raw=day(effective),
            deadline_raw=day(deadline),
            source_url=f"https://example.org/{code.lower()}/notice/{100 + index}",
            ingested_at=now(),
        )

        # A directive the feed says is finished must not still carry open work,
        # or it would trip STATUS_CONFLICT and stop being a "clean" row.
        directive_closed = status.lower() in {"superseded", "withdrawn", "revoked", "closed"}

        for slot in range(random.randint(1, 3)):
            item_status = (
                "RESOLVED"
                if directive_closed
                else random.choice(["PENDING", "PENDING", "IN_PROGRESS", "RESOLVED", "BLOCKED"])
            )
            resolved_at = (
                now() - timedelta(days=random.randint(1, 30))
                if item_status == "RESOLVED"
                else None
            )
            directive.action_items.append(
                ActionItem(
                    title=f"{random.choice(ACTION_TEMPLATES)} ({directive.reference_code})",
                    owner=random.choice(OWNERS),
                    status=item_status,
                    priority_raw=random.choice(["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
                    due_date_raw=day(deadline - 14 + slot * 5),
                    resolved_at=resolved_at,
                    notes=None,
                )
            )
        rows.append(directive)
    return rows


def build_messy_directives(authorities: dict[str, Authority]) -> list[Directive]:
    """Every row below is a deliberate anomaly. Comments name the expected flag."""
    rows: list[Directive] = []

    # EDGE CASE 1 - ACTIVE directive with no compliance deadline at all.
    # Expect: MISSING_DEADLINE (warning).
    rows.append(
        Directive(
            authority=authorities["FDA"],
            reference_code="FDA-2025-0442",
            title="Draft guidance on continuous manufacturing process validation",
            summary="Applies to oral solid dosage lines converting from batch to continuous flow.",
            raw_status="Active",
            raw_severity="high",
            published_at_raw=day(-120),
            effective_date_raw=day(-60),
            deadline_raw=None,
            source_url="https://example.org/fda/notice/0442",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Confirm scope with the Mumbai OSD site",
                    owner="R. Mehta",
                    status="IN_PROGRESS",
                    priority_raw="HIGH",
                    due_date_raw=day(20),
                ),
            ],
        )
    )

    # EDGE CASE 2 - the feed contradicts itself in a single status field,
    # and the publication date is a quarter label rather than a date.
    # Expect: CONFLICTING_STATUS (error), UNPARSEABLE_DATE (error).
    rows.append(
        Directive(
            authority=authorities["EMA"],
            reference_code="EMA/CHMP/118822/2025",
            title="Annex I revision - product information templates",
            summary="Template changes for sections 4.4 and 4.8 of the SmPC.",
            raw_status="ACTIVE/SUPERSEDED",
            raw_severity="critical",
            published_at_raw="Q3-2025",
            effective_date_raw=day(-30),
            deadline_raw=day(45),
            source_url="https://example.org/ema/notice/118822",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Decide which status the registry should trust",
                    owner="D. Mueller",
                    status="BLOCKED",
                    priority_raw="CRITICAL",
                    due_date_raw=day(10),
                    notes="Waiting on clarification from the agency help desk.",
                ),
            ],
        )
    )

    # EDGE CASE 3 - HTML markup and an unescaped entity in the title,
    # mojibake in the summary, and a truncated sentence.
    # Expect: MALFORMED_TEXT (warning), TRUNCATED_TEXT (warning).
    rows.append(
        Directive(
            authority=authorities["MHRA"],
            reference_code="MHRA-2025-0097",
            title="<p>Variations to UK marketing authorisations &amp; Northern Ireland supply</p>",
            summary=(
                "The agencyâ€™s updated position on the Windsor Framework "
                "affects labelling for products supplied toÂ Northern Ireland..."
            ),
            raw_status="in force",
            raw_severity="HIGH!!",
            published_at_raw="15 Mar 2026",
            effective_date_raw="01/06/2026",
            deadline_raw=day(90),
            source_url="https://example.org/mhra/notice/0097",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Re-artwork NI-supplied packs",
                    owner="K. O'Brien",
                    status="pending ",  # trailing whitespace -> NORMALISED_STATUS
                    priority_raw="2",  # numeric severity -> NORMALISED_SEVERITY
                    due_date_raw=day(70),
                ),
            ],
        )
    )

    # EDGE CASE 4 - withdrawn directive that still has open work attached,
    # i.e. two systems disagreeing about whether anyone needs to act.
    # Expect: STATUS_CONFLICT (error).
    rows.append(
        Directive(
            authority=authorities["PMDA"],
            reference_code="PMDA-2024-0310",
            title="Partial change application timelines for prefilled syringes",
            summary="Superseded by the 2025 consolidated notice; retained for audit history.",
            raw_status="withdrawn",
            raw_severity="medium",
            published_at_raw=day(-400),
            effective_date_raw=day(-340),
            deadline_raw=day(-120),
            source_url="https://example.org/pmda/notice/0310",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Close out the change control raised against this notice",
                    owner="P. Nakamura",
                    status="PENDING",
                    priority_raw="MEDIUM",
                    due_date_raw=day(-90),  # also OVERDUE
                ),
                ActionItem(
                    title="Archive the superseded assessment",
                    owner="P. Nakamura",
                    status="RESOLVED",
                    priority_raw="LOW",
                    due_date_raw=day(-150),
                    resolved_at=now() - timedelta(days=100),
                ),
            ],
        )
    )

    # EDGE CASE 5 - chronology is impossible: effective before publication,
    # and the deadline lands before the effective date.
    # Expect: DATE_CHRONOLOGY (error) twice, on different fields.
    rows.append(
        Directive(
            authority=authorities["CDSCO"],
            reference_code="CDSCO-2026-0021",
            title="Revised Schedule M compliance checkpoints",
            summary="Upgrade timelines for small and medium manufacturing units.",
            raw_status="ACTIVE",
            raw_severity="critical",
            published_at_raw=day(-30),
            effective_date_raw=day(-95),  # before publication
            deadline_raw=day(-120),  # before effective
            source_url="https://example.org/cdsco/notice/0021",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Query the agency on the published dates",
                    owner="",  # UNASSIGNED_OWNER
                    status="PENDING",
                    priority_raw="CRITICAL",
                    due_date_raw=day(-10),
                ),
            ],
        )
    )

    # EDGE CASE 6 - status code nobody recognises, a severity synonym we do
    # normalise ("catastrophic" -> CRITICAL), and a reference code that is
    # missing entirely.
    # Expect: UNKNOWN_STATUS (error), NORMALISED_SEVERITY (warning),
    #         MISSING_FIELD on reference_code (error).
    rows.append(
        Directive(
            authority=authorities["TGA"],
            reference_code=None,
            title="Unique device identification database onboarding",
            summary="Sponsors must register UDI data before the transition window closes.",
            raw_status="PENDING_REVIEW???",
            raw_severity="catastrophic",
            published_at_raw=day(-45),
            effective_date_raw=day(15),
            deadline_raw=day(120),
            source_url="https://example.org/tga/notice/udi",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Map internal catalogue to UDI-DI fields",
                    owner="unassigned",
                    status="ARCHIVED",  # UNKNOWN_STATUS - no such state, needs triage
                    priority_raw="HIGH",
                    due_date_raw=day(100),
                ),
            ],
        )
    )

    # EDGE CASE 7 - all-caps title, empty status, placeholder date strings.
    # Expect: SHOUTING_TEXT (warning), MISSING_STATUS (error).
    rows.append(
        Directive(
            authority=authorities["HC"],
            reference_code="HC-2026-0008",
            title="URGENT NOTICE REGARDING DRUG SHORTAGE REPORTING OBLIGATIONS",
            summary="Mandatory reporting within 5 calendar days of an anticipated shortage.",
            raw_status="",
            raw_severity="",
            published_at_raw="0000-00-00",
            effective_date_raw="TBD",
            deadline_raw="n/a",
            source_url="https://example.org/hc/notice/0008",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Nominate a shortage reporting contact",
                    owner="T. Dube",
                    status="wip",  # NORMALISED_STATUS
                    priority_raw="medium",
                    due_date_raw="31/02/2026",  # UNPARSEABLE_DATE - no such day
                ),
            ],
        )
    )

    # EDGE CASE 8 + 9 - the same reference code ingested twice (an amendment
    # that was never deduplicated). Expect: DUPLICATE_REFERENCE on both rows.
    for variant, (status, severity, offset) in enumerate(
        [("ACTIVE", "high", -75), ("superseded", "3", -210)]
    ):
        rows.append(
            Directive(
                authority=authorities["ANVISA"],
                reference_code="ANVISA-RDC-786",
                title=f"Good distribution practice requirements (revision {variant + 1})",
                summary="Transport qualification and temperature mapping obligations.",
                raw_status=status,
                raw_severity=severity,
                published_at_raw=day(offset),
                effective_date_raw=day(offset + 60),
                deadline_raw=day(offset + 150),
                source_url=f"https://example.org/anvisa/rdc/786?rev={variant + 1}",
                ingested_at=now(),
                action_items=[
                    ActionItem(
                        title="Determine which revision is authoritative",
                        owner="A. Fernandes",
                        status="IN_PROGRESS" if variant == 0 else "PENDING",
                        priority_raw="HIGH",
                        due_date_raw=day(offset + 140),
                    ),
                ],
            )
        )

    # EDGE CASE 10 - title is missing and the summary is a wall of text that
    # exceeds the display budget. Expect: MISSING_FIELD, OVERLONG_TEXT.
    rows.append(
        Directive(
            authority=authorities["EMA"],
            reference_code="EMA/INSP/55120/2026",
            title=None,
            summary=(
                "Inspection findings summary. "
                + "Deviation observed during the routine GMP inspection of the sterile filling suite; "
                * 12
            ),
            raw_status="ACTIVE",
            raw_severity="HIGH",
            published_at_raw=day(-20),
            effective_date_raw=day(-20),
            deadline_raw=day(25),
            source_url="https://example.org/ema/insp/55120",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Draft CAPA response",
                    owner="S. Okonkwo",
                    status="IN_PROGRESS",
                    priority_raw="CRITICAL",
                    due_date_raw=day(60),  # DUE_AFTER_DIRECTIVE_DEADLINE
                ),
            ],
        )
    )

    # EDGE CASE 11 - deadline long past with work still open.
    # Expect: OVERDUE on the directive and on its action items.
    rows.append(
        Directive(
            authority=authorities["FDA"],
            reference_code="FDA-2024-0988",
            title="Drug supply chain security act - saleable returns verification",
            summary="Dispensers must verify product identifiers prior to resale.",
            raw_status="ACTIVE",
            raw_severity="critical",
            published_at_raw=day(-500),
            effective_date_raw=day(-440),
            deadline_raw=day(-180),
            source_url="https://example.org/fda/notice/0988",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Complete verification router integration",
                    owner="L. Zhang",
                    status="BLOCKED",
                    priority_raw="CRITICAL",
                    due_date_raw=day(-200),
                    notes="Vendor contract unsigned.",
                ),
                ActionItem(
                    title="Train warehouse operators",
                    owner="M. Rossi",
                    status="PENDING",
                    priority_raw="HIGH",
                    due_date_raw=day(-60),
                ),
            ],
        )
    )

    # EDGE CASE 12 - resolution bookkeeping that disagrees with itself.
    # Expect: RESOLVED_WITHOUT_TIMESTAMP (warning) and
    #         TIMESTAMP_WITHOUT_RESOLUTION (error) on sibling items.
    rows.append(
        Directive(
            authority=authorities["PMDA"],
            reference_code="PMDA-2026-0144",
            title="Foreign manufacturer accreditation renewal",
            summary="Renewal applications must be filed 6 months before expiry.",
            raw_status="ACTIVE",
            raw_severity="MEDIUM",
            published_at_raw="March 3, 2026",
            effective_date_raw="April 1, 2026",
            deadline_raw=day(140),
            source_url="https://example.org/pmda/notice/0144",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="File renewal for the Osaka site",
                    owner="P. Nakamura",
                    status="RESOLVED",
                    priority_raw="MEDIUM",
                    due_date_raw=day(100),
                    resolved_at=None,  # resolved, but no timestamp
                ),
                ActionItem(
                    title="File renewal for the Kobe site",
                    owner="J. Lindqvist",
                    status="PENDING",
                    priority_raw="MEDIUM",
                    due_date_raw=day(110),
                    resolved_at=now() - timedelta(days=5),  # open, but timestamped
                ),
            ],
        )
    )

    # EDGE CASE 13 - control characters and a title that is nothing but markup.
    # Expect: MALFORMED_TEXT (error) - no readable content survives cleaning.
    rows.append(
        Directive(
            authority=authorities["CDSCO"],
            reference_code="CDSCO-2026-0044\u0007",
            title="<div><span></span></div>",
            summary="Notice body failed to extract during ingest.",
            raw_status="draft",
            raw_severity="low",
            published_at_raw=day(-5),
            effective_date_raw=None,
            deadline_raw=None,
            source_url="https://example.org/cdsco/notice/0044",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Re-fetch the source document",
                    owner="R. Mehta",
                    status="PENDING",
                    priority_raw=None,  # MISSING_SEVERITY
                    due_date_raw=day(7),
                ),
            ],
        )
    )

    # EDGE CASE 15 - the consultation window closes *after* the rule is
    # already in force, and the category arrived wrapped in markup.
    # Expect: MALFORMED_TEXT on category, UNPARSEABLE_DATE on comments_by.
    rows.append(
        Directive(
            authority=authorities["MHRA"],
            reference_code="MHRA-2026-0231",
            title="Consultation on revised clinical trial authorisation timelines",
            summary="Proposes a combined review of regulatory and ethics submissions.",
            category="<b>Clinical Trials</b>",
            raw_status="consultation",
            raw_severity="medium",
            published_at_raw=day(-25),
            comments_by_raw="see agency website",  # UNPARSEABLE_DATE
            effective_date_raw=day(95),
            deadline_raw=day(180),
            source_url="https://example.org/mhra/notice/0231",
            ingested_at=now(),
            action_items=[
                ActionItem(
                    title="Draft the consultation response",
                    owner="S. Okonkwo",
                    status="PENDING",
                    priority_raw="MEDIUM",
                    due_date_raw=day(30),
                ),
            ],
        )
    )

    # EDGE CASE 14 - a directive with no action items at all: nothing is
    # tracking it. Nothing is corrupt, but it is still a triage signal.
    rows.append(
        Directive(
            authority=authorities["TGA"],
            reference_code="TGA-2026-0190",
            title="Advertising code compliance for complementary medicines",
            summary="Revised restrictions on therapeutic claims in consumer advertising.",
            raw_status="PROPOSED",
            raw_severity="LOW",
            published_at_raw=day(-12),
            effective_date_raw=day(80),
            deadline_raw=day(160),
            source_url="https://example.org/tga/notice/0190",
            ingested_at=now(),
            action_items=[],
        )
    )

    # ------------------------------------------------------------------
    # Additional flagged rows. The hand-written cases above each isolate a
    # single anomaly for documentation; these broaden the corpus so the flag
    # filter, the "any flag" option and the badges have a realistic spread to
    # work against. Each entry's `flag` key names the primary code it raises.
    # Several are UNPARSEABLE_DATE on purpose, to give the dedicated
    # "Unparseable date" quick filter something to find.
    # ------------------------------------------------------------------
    extra_rows = [
        # UNPARSEABLE_DATE - a prose deadline the parser cannot read.
        dict(flag="UNPARSEABLE_DATE", authority="FDA", reference_code="FDA-2025-0511",
             title="Quality metrics reporting programme enrolment",
             summary="Establishment-level quality metrics submitted on a voluntary basis.",
             raw_status="ACTIVE", raw_severity="medium", category="Quality & CMC",
             published_at_raw=day(-40), effective_date_raw=day(20), deadline_raw="next quarter",
             owner="R. Mehta", item_status="PENDING", priority_raw="MEDIUM", due_raw=day(60)),
        # UNPARSEABLE_DATE on the effective date - a season, not a date.
        dict(flag="UNPARSEABLE_DATE", authority="EMA", reference_code="EMA/CHMP/22190/2026",
             title="Shortage prevention plan submission format",
             summary="Marketing authorisation holders must file structured shortage plans.",
             raw_status="in force", raw_severity="high", category="Supply Chain",
             published_at_raw=day(-18), effective_date_raw="spring 2026", deadline_raw=day(85),
             owner="L. Zhang", item_status="IN_PROGRESS", priority_raw="HIGH", due_raw=day(50)),
        # UNPARSEABLE_DATE - impossible calendar day.
        dict(flag="UNPARSEABLE_DATE", authority="CDSCO", reference_code="CDSCO-2026-0077",
             title="Revised cosmetic import registration workflow",
             summary="Registration certificates must be renewed before the transition closes.",
             raw_status="ACTIVE", raw_severity="low", category="Import & Export",
             published_at_raw=day(-22), effective_date_raw=day(30), deadline_raw="2026-02-31",
             owner="S. Okonkwo", item_status="PENDING", priority_raw="LOW", due_raw=day(40)),
        # MISSING_DEADLINE - active rule with no compliance deadline.
        dict(flag="MISSING_DEADLINE", authority="MHRA", reference_code="MHRA-2026-0305",
             title="Yellow Card reporting channel consolidation",
             summary="Adverse reaction reports migrate to the single reporting portal.",
             raw_status="in force", raw_severity="medium", category="Pharmacovigilance",
             published_at_raw=day(-60), effective_date_raw=day(-10), deadline_raw=None,
             owner="K. O'Brien", item_status="IN_PROGRESS", priority_raw="MEDIUM", due_raw=day(25)),
        # UNKNOWN_SEVERITY - a severity word the vocabulary does not contain.
        # ("extreme"/"catastrophic" are CRITICAL aliases, so this uses a word
        # that genuinely maps to nothing.)
        dict(flag="UNKNOWN_SEVERITY", authority="TGA", reference_code="TGA-2026-0221",
             title="Biologicals framework classification review",
             summary="Reassessment of class 2 and class 3 biologicals against the new rules.",
             raw_status="PROPOSED", raw_severity="showstopper", category="Market Authorisation",
             published_at_raw=day(-15), effective_date_raw=day(70), deadline_raw=day(150),
             owner="M. Rossi", item_status="PENDING", priority_raw="HIGH", due_raw=day(120)),
        # UNKNOWN_SEVERITY - two levels at once is not a value we recognise.
        dict(flag="UNKNOWN_SEVERITY", authority="EMA", reference_code="EMA/CHMP/55310/2026",
             title="Nitrosamine impurity re-testing scope clarification",
             summary="Scope of the re-testing obligation across legacy marketing authorisations.",
             raw_status="ACTIVE", raw_severity="high/low", category="Quality & CMC",
             published_at_raw=day(-20), effective_date_raw=day(25), deadline_raw=day(95),
             owner="A. Fernandes", item_status="IN_PROGRESS", priority_raw="HIGH", due_raw=day(70)),
        # UNKNOWN_SEVERITY - a prose value ("medium and critical") we cannot place.
        dict(flag="UNKNOWN_SEVERITY", authority="FDA", reference_code="FDA-2026-0914",
             title="Combination product postmarket safety reporting update",
             summary="Aligned reporting timelines for drug-device combination products.",
             raw_status="PENDING", raw_severity="medium and critical", category="Pharmacovigilance",
             published_at_raw=day(-12), effective_date_raw=day(40), deadline_raw=day(110),
             owner="R. Mehta", item_status="PENDING", priority_raw="CRITICAL", due_raw=day(85)),
        # MISSING_SEVERITY - no level supplied at all; treated as UNKNOWN.
        dict(flag="MISSING_SEVERITY", authority="MHRA", reference_code="MHRA-2026-0410",
             title="Registered pharmacy premises standards refresh",
             summary="Updated premises and governance standards for registered pharmacies.",
             raw_status="ACTIVE", raw_severity="", category="Inspections & GMP",
             published_at_raw=day(-30), effective_date_raw=day(15), deadline_raw=day(100),
             owner="K. O'Brien", item_status="PENDING", priority_raw="MEDIUM", due_raw=day(75)),
        # SHOUTING_TEXT - an all-caps title that gets case-normalised.
        dict(flag="SHOUTING_TEXT", authority="HC", reference_code="HC-2026-0019",
             title="IMPORTANT UPDATE TO MEDICAL DEVICE LICENCE RENEWAL OBLIGATIONS",
             summary="Annual licence renewals now require a cybersecurity attestation.",
             raw_status="ACTIVE", raw_severity="high", category="Medical Devices",
             published_at_raw=day(-28), effective_date_raw=day(5), deadline_raw=day(95),
             owner="D. Mueller", item_status="PENDING", priority_raw="HIGH", due_raw=day(80)),
        # AMBIGUOUS_DATE - day-first or month-first, impossible to be sure.
        dict(flag="AMBIGUOUS_DATE", authority="ANVISA", reference_code="ANVISA-RDC-842",
             title="Electronic labelling pilot for hospital products",
             summary="Digital package inserts permitted for restricted hospital-use products.",
             raw_status="ACTIVE", raw_severity="medium", category="Labelling & Packaging",
             published_at_raw=day(-35), effective_date_raw=day(10), deadline_raw="05/06/2026",
             owner="A. Fernandes", item_status="IN_PROGRESS", priority_raw="MEDIUM", due_raw=day(90)),
        # MALFORMED_TEXT - HTML markup left in the title by the extractor.
        dict(flag="MALFORMED_TEXT", authority="EMA", reference_code="EMA/INSP/61034/2026",
             title="<p>Remote GMP inspection procedure &amp; evidence requirements</p>",
             summary="Conditions under which remote inspections substitute for on-site visits.",
             raw_status="in force", raw_severity="high", category="Good Manufacturing Practice",
             published_at_raw=day(-14), effective_date_raw=day(-1), deadline_raw=day(110),
             owner="J. Lindqvist", item_status="PENDING", priority_raw="HIGH", due_raw=day(100)),
        # OVERDUE - the deadline is well past and work is still open.
        dict(flag="OVERDUE", authority="FDA", reference_code="FDA-2024-0771",
             title="Unique facility identifier registration",
             summary="Establishments must maintain a current DUNS-based facility identifier.",
             raw_status="ACTIVE", raw_severity="high", category="Quality & CMC",
             published_at_raw=day(-300), effective_date_raw=day(-240), deadline_raw=day(-45),
             owner="T. Dube", item_status="BLOCKED", priority_raw="HIGH", due_raw=day(-30)),
        # UNASSIGNED_OWNER - nobody is accountable for the only action item.
        dict(flag="UNASSIGNED_OWNER", authority="PMDA", reference_code="PMDA-2026-0208",
             title="Reexamination data package submission update",
             summary="Revised data package structure for post-marketing reexamination.",
             raw_status="ACTIVE", raw_severity="medium", category="Pharmacovigilance",
             published_at_raw=day(-21), effective_date_raw=day(40), deadline_raw=day(130),
             owner="TBD", item_status="PENDING", priority_raw="MEDIUM", due_raw=day(120)),
        # MISSING_STATUS - the feed sent no status at all.
        dict(flag="MISSING_STATUS", authority="CDSCO", reference_code="CDSCO-2026-0102",
             title="Bioequivalence study centre accreditation",
             summary="Only accredited centres may generate pivotal bioequivalence data.",
             raw_status="", raw_severity="high", category="Clinical Trials",
             published_at_raw=day(-19), effective_date_raw=day(55), deadline_raw=day(140),
             owner="R. Mehta", item_status="PENDING", priority_raw="HIGH", due_raw=day(135)),
        # UNKNOWN_STATUS - a status term that maps to nothing we know.
        dict(flag="UNKNOWN_STATUS", authority="TGA", reference_code="TGA-2026-0260",
             title="Prescription medicine advertising pre-approval",
             summary="Pre-vetting requirements for advertising directed at health professionals.",
             raw_status="ratified???", raw_severity="low", category="Advertising & Promotion",
             published_at_raw=day(-11), effective_date_raw=day(65), deadline_raw=day(155),
             owner="K. O'Brien", item_status="PENDING", priority_raw="LOW", due_raw=day(145)),
    ]
    for spec in extra_rows:
        rows.append(
            Directive(
                authority=authorities[spec["authority"]],
                reference_code=spec["reference_code"],
                title=spec["title"],
                summary=spec["summary"],
                category=spec["category"],
                raw_status=spec["raw_status"],
                raw_severity=spec["raw_severity"],
                published_at_raw=spec["published_at_raw"],
                effective_date_raw=spec["effective_date_raw"],
                deadline_raw=spec["deadline_raw"],
                source_url=f"https://example.org/extra/{spec['reference_code']}",
                ingested_at=now(),
                action_items=[
                    ActionItem(
                        title=f"Assess impact ({spec['reference_code']})",
                        owner=spec["owner"],
                        status=spec["item_status"],
                        priority_raw=spec["priority_raw"],
                        due_date_raw=spec["due_raw"],
                    )
                ],
            )
        )

    # Categories and comment windows for the hand-written rows above. Done
    # here rather than inline so each edge case keeps its comment focused on
    # the one anomaly it demonstrates.
    fallback_categories = [
        "Good Manufacturing Practice",
        "Labelling & Packaging",
        "Labelling & Packaging",
        "Medical Devices",
        "Good Manufacturing Practice",
        "Medical Devices",
        "Supply Chain",
        "Supply Chain",
        "Supply Chain",
        "Good Manufacturing Practice",
        "Supply Chain",
        "Market Authorisation",
        None,  # category never arrived from this feed
        "Advertising & Promotion",
    ]
    # Indexing by position breaks the moment a row is inserted in the middle:
    # every later row shifts and the last one silently falls off the end of
    # the list. Consume the list in order for rows that still need a category
    # instead, so inserting an edge case above cannot strand one.
    pending = iter(fallback_categories)
    for position, directive in enumerate(rows):
        if directive.category is None:
            directive.category = next(pending, "Market Authorisation")
        if directive.comments_by_raw is None and position % 2 == 0:
            directive.comments_by_raw = day(-40 + position * 3)

    return rows


def seed(keep_existing: bool = False) -> None:
    if keep_existing:
        Base.metadata.create_all(engine)
        with SessionLocal() as session:
            if session.execute(select(Directive.id)).first() is not None:
                print("Database already seeded; nothing to do (--keep).")
                return
    else:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    with SessionLocal() as session:
        authorities = {
            code: Authority(code=code, name=name, country=country, region=region)
            for code, name, country, region in AUTHORITIES
        }
        session.add_all(authorities.values())
        session.flush()

        messy = build_messy_directives(authorities)
        clean = build_clean_directives(authorities, TOTAL_DIRECTIVES - len(messy))
        directives = clean + messy
        session.add_all(directives)
        session.commit()

        item_count = sum(len(d.action_items) for d in directives)

    print(
        f"Seeded {len(authorities)} authorities, {len(directives)} directives "
        f"and {item_count} action items."
    )
    print(
        f"{len(messy)} directives carry deliberate anomalies - "
        "see the comments in seed.py."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the regulatory triage database.")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave existing data alone; only seed an empty database.",
    )
    try:
        seed(keep_existing=parser.parse_args().keep)
    except OperationalError as exc:
        if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
            raise
        # A stack trace here says nothing useful: the database is simply open
        # somewhere else, and the fix is to close it.
        target = os.getenv("DATABASE_URL", str(DEFAULT_SQLITE_PATH))
        print(
            "\nCannot seed: the database is open in another program.\n"
            f"  {target}\n\n"
            "Close whatever is holding it and run this again. Usual suspects:\n"
            "  - a running `uvicorn` (stop the API first)\n"
            "  - a SQLite viewer / database extension in your editor\n"
            "  - another seed script still finishing\n\n"
            "Or seed somewhere else entirely:\n"
            '  $env:DATABASE_URL = "sqlite:///./regulatory.dev.db"; '
            ".venv\\Scripts\\python seed.py",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
