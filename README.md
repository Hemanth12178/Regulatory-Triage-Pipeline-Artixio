# Regulatory Intelligence Triage Pipeline

A decision layer over a deliberately messy regulatory feed: a compliance
officer sees every directive, what is wrong with each record, and moves action
items through a validated workflow.

**Stack:** PostgreSQL 16 · Python / FastAPI / SQLAlchemy 2.0 ORM · React / Vite
/ TanStack Query + TanStack Table. The schema is engine-agnostic (SQLite fallback).

## 1. Run it (≈3 minutes)

Prerequisites: **Docker**, Python 3.11–3.14, Node 18+.

**Terminal 1 — database**

```bash
docker compose up -d
```

Starts PostgreSQL 16 on `localhost:5432` (user / password / db all
`regulatory`), matching the default `DATABASE_URL` — nothing to configure. Data
persists in a named volume; `docker compose down -v` wipes it. Using your own
Postgres? Copy `backend/.env.example` to `backend/.env` and edit `DATABASE_URL`.

**Terminal 2 — backend**

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python seed.py                                       # drops, recreates and seeds all tables
uvicorn app.main:app --reload --port 8000
```

**Terminal 3 — frontend**

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5151**. Vite proxies `/api` to port 8000 (pinned with
`strictPort`), so there is no API URL to configure. Interactive API docs at
http://localhost:8000/docs.

Tests (edge-case focused; they use a throwaway SQLite file, so no running DB):
`cd backend && .venv/Scripts/python -m pytest tests -q`.

**No Docker?** Run on SQLite — set `DATABASE_URL` before seeding, inline or in
`backend/.env` (PowerShell uses `$env:DATABASE_URL = "..."`):

```bash
export DATABASE_URL="sqlite:///./regulatory.db"
python seed.py && uvicorn app.main:app --port 8000
```

## 2. Database schema

Three entities, two one-to-many edges.

```
┌──────────────────────┐
│ authorities          │   Reference data. Clean by definition — a
├──────────────────────┤   regulator is not something a feed invents.
│ id            PK     │
│ code          UNIQUE │   FDA, EMA, MHRA, PMDA, CDSCO, TGA, HC, ANVISA
│ name                 │
│ country              │
│ region               │
└──────────┬───────────┘
           │ 1
           │
           │ N
┌──────────┴───────────────────┐
│ directives                   │   What the regulator published.
├──────────────────────────────┤   Everything from the feed is TEXT and
│ id                 PK        │   nullable — see "raw columns" below.
│ authority_id       FK ──────▶│
│ reference_code     TEXT      │   indexed, NOT unique (duplicates happen)
│ title              TEXT      │
│ summary            TEXT      │
│ category           TEXT      │   "Pharmacovigilance", "Clinical Trials"
│ raw_status         TEXT      │   "ACTIVE/SUPERSEDED", "", "in force"
│                              │   mapped -> PENDING | IN_PROGRESS |
│                              │   RESOLVED | BLOCKED | QUARANTINED
│ raw_severity       TEXT      │   "HIGH!!", "3", "catastrophic"
│ published_at_raw   TEXT      │   "Q3-2025", "0000-00-00", "15 Mar 2026"
│ comments_by_raw    TEXT      │   consultation window close
│ effective_date_raw TEXT      │
│ deadline_raw       TEXT      │
│ source_url         TEXT      │
│ ingested_at        TIMESTAMP │   trusted: we wrote it
└──────────┬───────────────────┘
           │ 1
           │
           │ N
┌──────────┴───────────────────┐
│ action_items                 │   The unit a human actually triages.
├──────────────────────────────┤
│ id                 PK        │
│ directive_id       FK ──────▶│   ON DELETE CASCADE
│ title              TEXT      │
│ owner              TEXT      │
│ status             TEXT      │   corrupt on read, canonical on write
│ priority_raw       TEXT      │
│ due_date_raw       TEXT      │
│ resolved_at        TIMESTAMP │   server-owned, never client-supplied
│ notes              TEXT      │
│ updated_at         TIMESTAMP │   optimistic-concurrency token
└──────────────────────────────┘
```

### Why it is shaped this way

- **Raw columns (`*_raw`), nullable TEXT** — the key decision. An ingest that
  rejects a row because a date reads `"Q3-2025"` loses the directive entirely,
  worse than storing and flagging it. The DB accepts the feed as-is;
  [`app/sanitize.py`](backend/app/sanitize.py) canonicalises at the API
  boundary, so nothing is lost or guessed. `status` is TEXT, not an enum, for
  the same reason — but the *write* path is strict, so corruption enters through
  ingest, never through a user.
- **Directive status is a triage state**, not the regulator's lifecycle: feed
  labels map onto `PENDING` / `IN_PROGRESS` / `RESOLVED` / `BLOCKED` /
  `QUARANTINED` (unmappable), original kept in `raw_status`. Action items have
  their own vocabulary (`…/DISMISSED`). Severity (on the directive) and priority
  (on the action item) are deliberately separate facts.
- **`reference_code` indexed but not unique** — regulators re-publish references
  under amendments; duplicates are flagged (`DUPLICATE_REFERENCE`), not rejected.
  **`resolved_at` is server-owned** — the API sets/clears it; `PATCH` bodies that
  try to set it are rejected (`extra="forbid"`).

## 3. How the messy data is handled

Every record returns in two parts: canonical values, and `flags` saying what
was wrong and what was done. Untrusted values become `null` (or `QUARANTINED`)
**and** raise a flag — never a silent guess, never a crash. Quality rolls up
`ok` / `warning` / `error`, from action items to their directive.

The seed loads **100 directives, 27 carrying deliberate anomalies**, each
annotated in [`backend/seed.py`](backend/seed.py). Representative cases
(✕ = error, ⚠ = warning):

| Anomaly in the feed | Flag | What the API does |
|---|---|---|
| `published_at = "Q3-2025"` | `UNPARSEABLE_DATE` ✕ | parsed `null`, raw string shown |
| `"03/04/2026"` (day/month-first?) | `AMBIGUOUS_DATE` ⚠ | read day-first **and** flagged |
| `status = "ACTIVE/SUPERSEDED"` | `CONFLICTING_STATUS` ✕ | → `QUARANTINED`, refuses to pick |
| `<p>` tags, `&amp;`, mojibake | `MALFORMED_TEXT` ⚠ | stripped, unescaped, re-encoded |
| same reference on two rows | `DUPLICATE_REFERENCE` ⚠ | both flagged, neither dropped |
| deadline passed, items still open | `OVERDUE` ✕ | with the day count |

…plus normalised/missing status and severity, no-such-day and chronology
conflicts, truncated/overlong/shouting text, cross-entity due-date checks, and
resolved/timestamp bookkeeping — the full set is in `seed.py`.

### The write path is strict

Reads are permissive; `PATCH /api/action-items/{id}` is not:

- **Enum-validated** — only the five assignable statuses; `UNKNOWN` is read-only (corrupt data must be triaged *into* a real state).
- **State machine** — `RESOLVED` / `DISMISSED` are terminal; only a reopen to
  `IN_PROGRESS` is allowed. Illegal moves return `409` naming what is allowed.
- **Dismissal requires a note** (→ `422` without one) and stamps `resolved_at`, so a closed item can't keep a contradictory timestamp.
- **Optimistic concurrency** — client echoes `expected_updated_at`; a row changed underneath returns `409`.
- **Input sanitised and validated** — markup stripped from notes/owners; input
  that sanitises to nothing, unknown fields (`extra="forbid"`), empty bodies
  (`400`) and missing ids (`404`) are all rejected, and a catch-all handler
  keeps stack traces off the wire.

## 4. The interface

Two routes: `/` (overview, with live figures from the API) and `/triage` (the
console). The console is a triage surface, not a dashboard — 44px rows, ~18
directives per screen.

- **Colour is spent on one axis — severity** (a red→yellow heat ramp,
  `UNKNOWN` = dashed grey). **Data-quality flags stay neutral slate**, so
  "severe" and "damaged" never compete for the same colour; badges carry their
  meaning in text (`OVERDUE`, `BAD DATE`), deduplicated with a count (`×3`).
- **Click any row** for the audit trail — a published → effective → deadline
  timeline (dashed where unparseable), every flag explained, and its action items.
- **Status dropdowns** apply optimistically, rolling back with a toast if
  rejected. **Header counters are filters**; **one Filters button** opens a
  popover for every dimension (authority, owner, status, severity, quality,
  flag, open-work, page size), applied filters shown as removable chips, `/`
  focuses search. Rows with no deadline always sort last — unschedulable.

## 5. API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | liveness + whether the DB is seeded |
| `GET` | `/api/directives` | filter, sort, paginate; rows + stats |
| `GET` | `/api/directives/{id}` | one directive with its action items |
| `GET` | `/api/action-items/{id}` | one action item |
| `PATCH` | `/api/action-items/{id}` | status / owner / notes — the only write |
| `GET` | `/api/authorities` | filter options |
| `GET` | `/api/owners` | distinct owners with workload counts |
| `GET` | `/api/vocabulary` | canonical enums, state machine, flag codes |

`/api/directives` query params: `q`, `authority`, `status`, `severity`,
`quality`, `flag`, `owner`, `only_open`, `sort`, `order`, `page`, `page_size`
(≤200); repeatable filters accept multiple values. `owner` matches the
*sanitised* owner, and `__unassigned__` selects rows flagged `UNASSIGNED_OWNER`.

## 6. Known trade-offs

- **Filtering on the sanitised projection, not in SQL** — canonical values are
  derived, so the list endpoint sanitises then filters. Fine at this scale; past
  a few thousand rows, ingest should persist canonical columns and push the
  predicates into the database.
- **PostgreSQL by default, SQLite as a fallback** — `DATABASE_URL` switches
  engines. Timestamps are naive UTC (`timestamp without time zone`), not
  `timestamptz`, because the write path and concurrency check normalise to naive
  UTC in one place.
- **No auth** — out of scope; `owner` is free text, not a user FK.
- **No migrations** — `seed.py` recreates the schema; a real deployment needs Alembic.

## 7. Layout

```
docker-compose.yml       PostgreSQL 16 service (matches the default DATABASE_URL)
backend/
  .env.example           DATABASE_URL / CORS_ORIGINS template (copy to .env)
  app/db.py              engine + session (DATABASE_URL picks PostgreSQL or SQLite)
  app/models.py          the three ORM entities
  app/sanitize.py        text/date/enum cleaning, flag vocabulary, state machine
  app/services.py        ORM rows -> validated payloads + cross-field checks
  app/schemas.py         Pydantic write validation
  app/routers/           directives (read) · action_items (write)
  app/main.py            app, CORS, error handlers
  seed.py                mock feed; anomalies annotated inline
  tests/test_api.py
frontend/src/
  App.tsx                routes: / and /triage
  pages/                 HomePage (overview) · TriagePage (console)
  components/            table, filters, stat strip, detail panel, toasts
  lib/api.ts             typed fetch layer · styles.css green/white tokens
```
