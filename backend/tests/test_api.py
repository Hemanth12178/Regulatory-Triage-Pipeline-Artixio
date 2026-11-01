"""Edge-case coverage for the sanitisation layer and the triage write path.

Runs against a throwaway SQLite file, seeded with the same mock feed the app
uses, so the assertions below are about the real anomalies in seed.py.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="reg-test-"), "test.db"),
)

from fastapi.testclient import TestClient  # noqa: E402

from app import sanitize as san  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ActionItem, Authority, Directive  # noqa: E402
from app.services import (  # noqa: E402
    duplicate_reference_codes,
    serialize_action_item,
    serialize_directive,
)
from seed import seed  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def seeded_database():
    seed()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    return TestClient(app)


def codes(payload: dict) -> set[str]:
    """All flag codes on a row, including its action items."""
    out = {f["code"] for f in payload.get("flags", [])}
    for item in payload.get("action_items", []):
        out |= {f["code"] for f in item["flags"]}
    return out


def find(client: TestClient, reference: str) -> dict:
    rows = client.get("/api/directives", params={"page_size": 200}).json()["items"]
    matches = [r for r in rows if (r["reference_code"] or "") == reference]
    assert matches, f"no directive with reference {reference}"
    return matches[0]


# --------------------------------------------------------------------------
# Unit level: sanitisation never raises, always explains
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "Q3-2025", "0000-00-00", "31/02/2026", "not a date", "TBD", "15/15/2026"],
)
def test_bad_dates_never_raise(raw):
    value, flags = san.parse_date(raw, field="deadline")
    assert value is None
    assert all(isinstance(f, san.Flag) for f in flags)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-03-15", date(2026, 3, 15)),
        ("15 Mar 2026", date(2026, 3, 15)),
        ("March 15, 2026", date(2026, 3, 15)),
        ("15/03/2026", date(2026, 3, 15)),
        ("2026-03-15T09:30:00", date(2026, 3, 15)),
    ],
)
def test_date_formats_are_accepted(raw, expected):
    value, flags = san.parse_date(raw, field="published_at")
    assert value == expected
    assert flags == []


def test_conflicting_status_is_not_silently_resolved():
    status, flags = san.coerce_directive_status("ACTIVE/SUPERSEDED")
    assert status is san.DirectiveStatus.QUARANTINED
    assert {f["code"] for f in (f.as_dict() for f in flags)} == {"CONFLICTING_STATUS"}


def test_unknown_status_is_an_error_not_a_guess():
    status, flags = san.coerce_directive_status("PENDING_REVIEW???")
    assert status is san.DirectiveStatus.QUARANTINED
    assert flags[0].code == "UNKNOWN_STATUS"
    assert flags[0].level == "error"


def test_markup_and_mojibake_are_repaired():
    value, flags = san.clean_text(
        "<p>Variations &amp; the agencyâ€™s position</p>", field="title"
    )
    assert value == "Variations & the agency's position"
    assert "MALFORMED_TEXT" in {f.code for f in flags}


def test_text_that_is_only_markup_is_rejected():
    value, flags = san.clean_text("<div><span></span></div>", field="title", required=True)
    assert value is None
    assert flags[0].level == "error"


def test_clipping_is_not_reported_as_source_damage():
    value, flags = san.clean_text("word " * 400, field="summary")
    found = {f.code for f in flags}
    assert "OVERLONG_TEXT" in found
    assert "MALFORMED_TEXT" not in found  # our own clipping is not the feed's fault
    assert len(value) <= san.MAX_TEXT_LEN


def test_sql_injection_in_search_is_inert(client):
    response = client.get("/api/directives", params={"q": "'; DROP TABLE directives;--"})
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert client.get("/api/health").json()["directives"] > 0


# --------------------------------------------------------------------------
# Seeded anomalies surface on the right rows
# --------------------------------------------------------------------------


def test_active_directive_without_deadline_is_flagged(client):
    assert "MISSING_DEADLINE" in codes(find(client, "FDA-2025-0442"))


def test_contradictory_feed_status_is_flagged(client):
    row = find(client, "EMA/CHMP/118822/2025")
    assert row["status"] == "QUARANTINED"
    assert {"CONFLICTING_STATUS", "UNPARSEABLE_DATE"} <= codes(row)
    assert row["published_at"] is None  # unparseable, so no invented value
    assert row["raw_dates"]["published_at"] == "Q3-2025"  # but the raw value is kept


def test_closed_directive_with_open_work_is_flagged(client):
    row = find(client, "PMDA-2024-0310")
    assert row["status"] == "RESOLVED"
    assert row["open_item_count"] > 0
    assert "STATUS_CONFLICT" in codes(row)


def test_impossible_chronology_is_flagged(client):
    row = find(client, "CDSCO-2026-0021")
    chronology = [f for f in row["flags"] if f["code"] == "DATE_CHRONOLOGY"]
    assert {f["field"] for f in chronology} == {"effective_date", "deadline"}


def test_duplicate_reference_codes_are_detected(client):
    rows = client.get("/api/directives", params={"page_size": 200}).json()["items"]
    dupes = [r for r in rows if (r["reference_code"] or "") == "ANVISA-RDC-786"]
    assert len(dupes) == 2
    assert all("DUPLICATE_REFERENCE" in codes(r) for r in dupes)


def test_resolution_bookkeeping_conflicts_are_flagged(client):
    row = find(client, "PMDA-2026-0144")
    assert {"RESOLVED_WITHOUT_TIMESTAMP", "TIMESTAMP_WITHOUT_RESOLUTION"} <= codes(row)


def test_row_quality_rolls_up_from_action_items(client):
    row = find(client, "PMDA-2026-0144")
    assert row["quality"] == "ok"  # the directive itself is clean
    assert row["rollup_quality"] == "error"  # but a child is not


def test_every_row_carries_a_quality_verdict(client):
    rows = client.get("/api/directives", params={"page_size": 200}).json()["items"]
    assert len(rows) == 100
    assert all(r["rollup_quality"] in {"ok", "warning", "error"} for r in rows)
    assert any(r["rollup_quality"] == "ok" for r in rows)
    assert any(r["rollup_quality"] == "error" for r in rows)


# --------------------------------------------------------------------------
# Filtering, sorting, pagination
# --------------------------------------------------------------------------


def test_filter_by_quality_and_flag(client):
    errors = client.get("/api/directives", params={"quality": "error", "page_size": 200}).json()
    assert errors["total"] > 0
    assert all(r["rollup_quality"] == "error" for r in errors["items"])

    overdue = client.get("/api/directives", params={"flag": "OVERDUE", "page_size": 200}).json()
    assert all("OVERDUE" in codes(r) for r in overdue["items"])


def test_owners_are_derived_from_the_sanitised_projection(client):
    owners = client.get("/api/owners").json()
    labels = [o["label"] for o in owners]

    assert "R. Mehta" in labels
    # The junk owner values ("", "unassigned", "TBD") collapse into one bucket...
    assert labels[-1] == "Unassigned"  # ...pinned last, after the real people
    unassigned = owners[-1]
    assert unassigned["value"] == "__unassigned__"
    assert unassigned["total_items"] == 3

    for owner in owners:
        assert owner["open_items"] <= owner["total_items"]


def test_filter_by_owner(client):
    response = client.get(
        "/api/directives", params={"owner": "R. Mehta", "page_size": 200}
    ).json()
    assert response["total"] > 0
    for row in response["items"]:
        assert any(
            (item["owner"] or "").lower() == "r. mehta" for item in row["action_items"]
        )


def test_owner_filter_is_case_insensitive(client):
    exact = client.get("/api/directives", params={"owner": "R. Mehta"}).json()["total"]
    shouted = client.get("/api/directives", params={"owner": "r. MEHTA"}).json()["total"]
    assert exact == shouted > 0


def test_filter_by_unassigned_owner(client):
    response = client.get(
        "/api/directives", params={"owner": "__unassigned__", "page_size": 200}
    ).json()
    assert response["total"] > 0
    for row in response["items"]:
        assert any(item["owner"] is None for item in row["action_items"])
        # These are exactly the rows the sanitiser flagged as unowned.
        assert any(
            "UNASSIGNED_OWNER" in {f["code"] for f in item["flags"]}
            for item in row["action_items"]
        )


def test_unknown_owner_returns_nothing_rather_than_everything(client):
    response = client.get("/api/directives", params={"owner": "Nobody At All"}).json()
    assert response["total"] == 0


@pytest.mark.parametrize(
    "raw",
    ["2026-03-15T09:30:00Z", "2026-03-15T09:30:00+01:00", "2026-03-15 09:30"],
)
def test_iso8601_with_timezone_is_parsed_not_rejected(raw):
    """A trailing Z or an offset is the commonest machine-readable form."""
    value, flags = san.parse_date(raw, field="published_at")
    assert value == date(2026, 3, 15)
    assert "UNPARSEABLE_DATE" not in {f.code for f in flags}


def test_ambiguous_numeric_date_is_flagged_not_silently_guessed():
    value, flags = san.parse_date("03/04/2026", field="deadline")
    assert value == date(2026, 4, 3)  # day-first reading is kept
    assert "AMBIGUOUS_DATE" in {f.code for f in flags}


def test_unambiguous_numeric_date_raises_nothing():
    value, flags = san.parse_date("13/04/2026", field="deadline")
    assert value == date(2026, 4, 13)
    assert flags == []


def test_half_recognised_status_is_quarantined():
    """Resolving "ACTIVE/GIBBERISH" to ACTIVE would trust a half-parsed field."""
    status, flags = san.coerce_directive_status("ACTIVE/GIBBERISH")
    assert status is san.DirectiveStatus.QUARANTINED
    assert flags[0].code == "UNKNOWN_STATUS"
    assert flags[0].level == "error"


@pytest.mark.parametrize("raw", ["in-progress", "in_progress", "In Progress"])
def test_separator_spellings_map_to_one_status(raw):
    assert san.coerce_action_status(raw)[0] is san.ActionStatus.IN_PROGRESS


def test_comparison_operators_in_prose_survive_cleaning():
    """A loose tag regex eats everything between "<" and ">"."""
    value, flags = san.clean_text("dose < 5 mg and pH > 7", field="summary")
    assert value == "dose < 5 mg and pH > 7"
    assert flags == []


def test_unclosed_tag_is_flagged(client):
    value, flags = san.clean_text("Title with <unclosed", field="title")
    assert value == "Title with <unclosed"  # kept: it may be real prose
    assert "MALFORMED_TEXT" in {f.code for f in flags}


def test_duplicate_detection_ignores_case_and_whitespace():
    """Re-ingest under different casing is the case this check exists for."""
    with SessionLocal() as session:
        authority = session.query(Authority).first()
        rows = [
            Directive(authority=authority, reference_code=code, title="t", raw_status="ACTIVE")
            for code in ("ZZ/DUP/1", "zz/dup/1", "ZZ/DUP/1 ", "ZZ/UNIQUE/1")
        ]
        session.add_all(rows)
        session.commit()
        try:
            duplicates = duplicate_reference_codes(session)
            assert "ZZ/DUP/1" in duplicates
            assert "ZZ/UNIQUE/1" not in duplicates
        finally:
            for row in rows:
                session.delete(row)
            session.commit()


def test_unknown_flag_code_is_rejected_not_silently_empty(client):
    response = client.get("/api/directives", params={"flag": "NOT_A_REAL_FLAG"})
    assert response.status_code == 422
    assert "NOT_A_REAL_FLAG" in response.json()["detail"]


def test_vocabulary_exposes_the_state_machine(client):
    vocab = client.get("/api/vocabulary").json()
    assert set(vocab["directive_status"]) == {
        "PENDING",
        "IN_PROGRESS",
        "RESOLVED",
        "BLOCKED",
        "QUARANTINED",
    }
    assert "UNKNOWN" not in vocab["assignable_action_status"]
    assert vocab["action_transitions"]["RESOLVED"] == ["IN_PROGRESS"]
    assert "OVERDUE" in vocab["flag_codes"]


def test_owner_filter_with_only_open_means_that_owner(client):
    """Not "this person is involved and somebody has open work"."""
    owners = client.get("/api/owners").json()
    name = next(o["value"] for o in owners if o["value"] != "__unassigned__")
    response = client.get(
        "/api/directives",
        params={"owner": name, "only_open": "true", "page_size": 200},
    ).json()
    for row in response["items"]:
        assert any(
            item["is_open"] and (item["owner"] or "").lower() == name.lower()
            for item in row["action_items"]
        )


def test_unknown_enum_value_is_rejected_with_422(client):
    response = client.get("/api/directives", params={"status": "DEFINITELY_NOT_A_STATUS"})
    assert response.status_code == 422
    assert "problems" in response.json()


def test_pagination_bounds(client):
    assert client.get("/api/directives", params={"page_size": 10_000}).status_code == 422
    assert client.get("/api/directives", params={"page": 0}).status_code == 422

    beyond = client.get("/api/directives", params={"page": 99, "page_size": 10}).json()
    assert beyond["items"] == []
    assert beyond["total"] == 100  # total still reflects the whole result set


def test_rows_without_a_deadline_sort_last(client):
    rows = client.get(
        "/api/directives", params={"sort": "deadline", "order": "asc", "page_size": 200}
    ).json()["items"]
    deadlines = [r["deadline"] for r in rows]
    first_none = next((i for i, d in enumerate(deadlines) if d is None), len(deadlines))
    assert all(d is None for d in deadlines[first_none:])


def test_search_covers_action_items(client):
    response = client.get("/api/directives", params={"q": "CAPA"}).json()
    assert response["total"] >= 1


# --------------------------------------------------------------------------
# The write path is strict
# --------------------------------------------------------------------------


def open_item_id(client: TestClient, status: str = "PENDING") -> int:
    rows = client.get("/api/directives", params={"page_size": 200}).json()["items"]
    for row in rows:
        for item in row["action_items"]:
            if item["status"] == status:
                return item["id"]
    raise AssertionError(f"no action item in state {status}")


def test_status_change_sets_the_resolution_timestamp(client):
    item_id = open_item_id(client, "PENDING")
    response = client.patch(f"/api/action-items/{item_id}", json={"status": "RESOLVED"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "RESOLVED"
    assert body["resolved_at"] is not None
    assert body["is_open"] is False
    assert "RESOLVED_WITHOUT_TIMESTAMP" not in {f["code"] for f in body["flags"]}

    # Reopening clears it again rather than leaving a contradiction behind.
    reopened = client.patch(f"/api/action-items/{item_id}", json={"status": "IN_PROGRESS"})
    assert reopened.status_code == 200
    assert reopened.json()["resolved_at"] is None


def test_illegal_transition_is_refused(client):
    item_id = open_item_id(client, "PENDING")
    client.patch(f"/api/action-items/{item_id}", json={"status": "RESOLVED"})
    blocked = client.patch(f"/api/action-items/{item_id}", json={"status": "BLOCKED"})
    assert blocked.status_code == 409
    assert "RESOLVED -> BLOCKED" in blocked.json()["detail"]
    client.patch(f"/api/action-items/{item_id}", json={"status": "IN_PROGRESS"})  # restore


def test_unknown_status_cannot_be_assigned(client):
    item_id = open_item_id(client, "PENDING")
    response = client.patch(f"/api/action-items/{item_id}", json={"status": "UNKNOWN"})
    assert response.status_code == 422


def test_corrupt_status_can_be_triaged_into_a_real_state(client):
    rows = client.get("/api/directives", params={"page_size": 200}).json()["items"]
    corrupt = [
        item
        for row in rows
        for item in row["action_items"]
        if item["status"] == "UNKNOWN"
    ]
    assert corrupt, "seed data should contain an unrecognised action status"
    response = client.patch(
        f"/api/action-items/{corrupt[0]['id']}", json={"status": "IN_PROGRESS"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "IN_PROGRESS"


def test_dismissal_requires_a_justification(client):
    item_id = open_item_id(client, "PENDING")
    bare = client.patch(f"/api/action-items/{item_id}", json={"status": "DISMISSED"})
    assert bare.status_code == 422

    with_note = client.patch(
        f"/api/action-items/{item_id}",
        json={"status": "DISMISSED", "notes": "Out of scope for our product portfolio."},
    )
    assert with_note.status_code == 200
    assert with_note.json()["status"] == "DISMISSED"


def test_input_that_sanitises_to_nothing_is_rejected_clearly(client):
    """Not "no changes" - the caller did send something, it was just empty."""
    item_id = open_item_id(client, "PENDING")
    for value in ("   ", "<b></b>"):
        response = client.patch(f"/api/action-items/{item_id}", json={"owner": value})
        assert response.status_code == 422, value
        assert "readable text" in response.json()["detail"]


def test_dismissing_stamps_the_closure_time(client):
    """A dismissed item used to keep whatever resolved_at it arrived with."""
    item_id = open_item_id(client, "PENDING")
    response = client.patch(
        f"/api/action-items/{item_id}",
        json={"status": "DISMISSED", "notes": "Not applicable to our portfolio."},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DISMISSED"
    assert body["resolved_at"] is not None
    assert body["is_open"] is False

    reopened = client.patch(f"/api/action-items/{item_id}", json={"status": "IN_PROGRESS"})
    assert reopened.json()["resolved_at"] is None


def test_user_input_is_sanitised_too(client):
    item_id = open_item_id(client, "PENDING")
    response = client.patch(
        f"/api/action-items/{item_id}",
        json={"notes": "<script>alert('x')</script>  Checked   with   QA  "},
    )
    assert response.status_code == 200
    assert response.json()["notes"] == "alert('x') Checked with QA"


def test_empty_patch_body_is_rejected(client):
    item_id = open_item_id(client, "PENDING")
    assert client.patch(f"/api/action-items/{item_id}", json={}).status_code == 400


def test_unknown_fields_are_rejected(client):
    item_id = open_item_id(client, "PENDING")
    response = client.patch(
        f"/api/action-items/{item_id}", json={"status": "IN_PROGRESS", "resolved_at": "2020-01-01"}
    )
    assert response.status_code == 422  # resolved_at is server-owned


def test_stale_write_is_refused(client):
    item_id = open_item_id(client, "PENDING")
    stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    response = client.patch(
        f"/api/action-items/{item_id}",
        json={"status": "IN_PROGRESS", "expected_updated_at": stale},
    )
    assert response.status_code == 409


def test_locked_database_reports_503_not_a_mystery_500():
    """Reads keep working while SQLite holds a write lock, so the symptom is
    "the list loads but every status change fails". Name the cause."""
    import asyncio

    from sqlalchemy.exc import OperationalError

    from app.main import database_unavailable_handler

    exc = OperationalError("UPDATE action_items", {}, Exception("database is locked"))
    response = asyncio.run(database_unavailable_handler(None, exc))
    assert response.status_code == 503
    assert b"locked by another process" in response.body


def test_missing_action_item_is_404(client):
    assert client.patch("/api/action-items/999999", json={"status": "RESOLVED"}).status_code == 404
    assert client.get("/api/directives/999999").status_code == 404


# --------------------------------------------------------------------------
# A row with nothing but nulls must still render
# --------------------------------------------------------------------------


def test_entirely_empty_record_is_survivable():
    with SessionLocal() as session:
        authority = session.query(Authority).first()
        directive = Directive(
            authority=authority,
            reference_code=None,
            title=None,
            summary=None,
            raw_status=None,
            raw_severity=None,
            published_at_raw=None,
            effective_date_raw=None,
            deadline_raw=None,
            source_url=None,
        )
        directive.action_items.append(
            ActionItem(title=None, owner=None, status=None, priority_raw=None, due_date_raw=None)
        )
        session.add(directive)
        session.commit()

        payload = serialize_directive(directive)
        assert payload["rollup_quality"] == "error"
        assert payload["title"] is None
        assert payload["status"] == "QUARANTINED"
        assert payload["action_items"][0]["status"] == "UNKNOWN"

        item_payload = serialize_action_item(directive.action_items[0])
        assert item_payload["is_open"] is True

        session.delete(directive)
        session.commit()
