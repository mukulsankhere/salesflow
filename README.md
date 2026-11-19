# SalesFlow

A lead-to-appointment-to-renewal sales and operations platform for multi-store service
businesses, built from the SalesFlow PRD v1.0. The demo organisation models 5 stores in
Indore (skin, hair, fitness, spa and dental services) with a full 30-day history.

**Stack (per the PRD):** FastAPI (Python 3.12, modular monolith) · React 18 + Vite +
TypeScript + Tailwind CSS · MongoDB (via Motor) · SSE realtime · JWT auth with roles and
store scoping.

The frontend is a built SPA served by the API, so the whole product runs as one service.

---

## Quick Start

**Easiest — one click:**

- **Windows:** double-click `start-windows.bat`
- **macOS / Linux / WSL:** run `./start-mac-linux.sh` (Terminal: `cd salesflow` then `./start-mac-linux.sh`)

The script creates a local `.venv`, installs dependencies, seeds the demo database,
starts the app and opens http://localhost:8000 automatically. Requirements: Python 3.10+.

**Manual (equivalent):**

```bash
# 1. Backend
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
# -> http://localhost:8000  (the database seeds itself on first boot)
```

```bash
# 2. Frontend (only needed to rebuild the UI; dist/ ships prebuilt)
cd frontend
npm install
npm run build   # output in frontend/dist, served automatically by the API
```

Open **http://localhost:8000** and sign in with the single Admin account:

| Role | Login | Scope |
| --- | --- | --- |
| Admin | `admin@salesflow.app` | Everything, incl. Settings + Audit |

Password: `Demo@123`

**Single sign-in deployment:** only the Admin can sign in. Employees (Managers,
Sales, Marketing, Staff) exist as staff records — they are assignable owners on
leads, appear in Store & Team and in Settings → Admin Access — but they have no
password and no sign-in. Employee login attempts are rejected at the API.

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `"python is not recognized"` / script says Python not found | Install Python 3.10+ from python.org; on Windows tick **Add python.exe to PATH**, reopen the terminal, run the start script again |
| `pip install` fails / times out | Check internet / corporate proxy; retry manually: `.venv\Scripts\pip install -r backend\requirements.txt` (Windows) or `./.venv/bin/pip install -r backend/requirements.txt` (mac/Linux) |
| `Error: could not create a venv` | Debian/Ubuntu: `sudo apt install python3-venv`; Windows: re-run the Python installer and keep the default options |
| Port `8000` already in use | Another app owns the port — change the last number in the start script (`--port 8001`) and open http://localhost:8001 |
| UI looks old / missing features after an update | Extract the new zip into a **fresh empty folder** and hard-refresh the browser once (`Ctrl+Shift+R` / `Cmd+Shift+R`); the page itself is sent `Cache-Control: no-cache`, so afterwards a normal refresh is enough |
| Zip won't extract ("corrupt") | Re-download the zip; the original is ~450 KB and unzip-verifies with no errors |

The app runs fully self-contained without MongoDB or Redis (an in-process store seeds
itself with demo data on first boot).

## AI Calling & Automatic Messaging (newest pack)

The Action Queue is now worked by a voice agent instead of a human checklist.

1. **AI Calling** (`/ai-calls`, admin-only) - the "Aarav" voice agent dials the lead
   queue score-first and SLA-aware (same priority rules the human queue used), with a
   live cockpit: streaming transcript, voice waveform, call timer, detected intents and
   sentiment, plus **Listen In / Whisper Tip / Take Over / End Call** at any moment.
2. **Shift control & guardrails** - one master toggle starts/pauses the shift; the
   calling window (default 10:00-19:00 IST, TRAI-safe), daily budget, daily lead cap,
   max attempts per lead and retry gap are editable in the Guardrails dialog. Voicemail
   detection, DND honouring, STOP → instant DNC and consent recording are built in.
3. **Automatic WhatsApp & SMS** - every outcome fires the right follow-up message
   through the same consent-first provider interface the campaigns use: booking
   confirmations, callback confirmations, missed-call nudges, appointment reminders
   (2h before start) and opt-out confirmations. WhatsApp when the person opted in,
   SMS otherwise. The message log with delivery lifecycle (sent → delivered → read →
   replied) and a Test WhatsApp/SMS sender live on the same page.
4. **Everything writes back** - AI outcomes move leads through the same pipeline
   (bookings create real appointments with slot reservations; DNC sets consent;
   handoffs assign the lead to you), so Dashboard, Appointments and Customer 360
   stay truthful. Legacy `/queue` and `/quick` routes redirect to
   `/ai-calls`; the old Action Queue UI was removed.

## Operations Pulse (feature pack)

1. **AI Call Queue** (`/ai-calls`) - the prioritized queue (overdue follow-ups,
   SLA-breached and hot new leads, today's unconfirmed appointments, renewals due)
   is dialed automatically by the AI agent; humans intervene with Take Over. The
   underlying priority + SLA logic is unchanged and still audited/broadcast live.
2. **Lead response SLA tracking** (Leads → Response SLA) - per-source response targets
   from BR-16, average response time, % within SLA per store, open breaches, and an
   "at risk of going cold" list (>=50% of target elapsed) with call-now actions.
3. **Unified customer conversation history** (Customer 360) - calls, messages,
   appointment status changes, campaign responses, payments and consent events on one
   timeline per customer (`GET /customers/{id}/timeline`).
4. **Lead source quality** (Marketing → Source Quality) - sources compared by contacted rate,
   bookings, completed visits and attributed revenue with ROAS against ad spend, plus a
   rule-based budget recommendation. (Moved from Leads to keep the lead workspace focused.)
5. **Manager coaching view** (Store & Team → Manager Coaching) - per-owner follow-up
   completion, conversion and response speed, with drill-down records (overdue
   follow-ups, untouched leads) that deep-link into Leads.
6. **Integrations hub** (Settings → Integrations) - connection status, last sync, sync
   issues with counts, retry controls: rate-limit issues heal on retry, expired
   credentials return a clear "update the credential" failure.
7. **Audit and role controls** - store-scoped access enforced on every new endpoint,
   plus filterable audit history (by action / user) in Settings → Audit Log.
8. **Admin Access** (Settings → Admin Access) - single sign-in deployment: the admin
   account is the only login; the team directory shows employees as staff records
   (assignment + routing) with sign-in disabled, and store scoping is listed.

### Lead management essentials (all rule-based, no AI)

1. **Lead priority** - every lead gets a Hot / Warm / Low tier computed from fixed rules
   (score band, stage, overdue follow-ups, recency, replies, and **prior visits** - a
   phone that already owns a customer record raises the tier as a returning visitor).
   The deciding factors are shown on the lead card and in the drawer ("Priority
   (Rule-Based)").
2. **Today's Follow-Up Queue** - one prioritized list combining overdue follow-ups, new
   leads that breached their response SLA, hot leads waiting, **leads ready to book**
   (interested / offered), unconfirmed appointments and renewals. Every item shows the
   recommended next step; actions: Log Call, Mark Interested, **Book** (opens the
   booking picker with the lead's store and service pre-filled), **Mark Lost** (asks for
   a reason), **Mark Done** (logs an outcome to the lead's history and clears the
   reminder), **Reschedule** (pick an explicit date/time), Snooze. Clicking a lead item
   opens it.
3. **Automatic Lead Assignment and SLA Reminders** (Marketing → Follow-Up Rules) -
   assignment rules match on **source, store and/or service**; the owner is either the
   chosen salesperson or the **least-loaded salesperson of the store**. Rules run on
   lead creation, are created **paused** with a **preview** first, and every action
   lands in the **Automation Activity Log**. If nobody contacts a new lead within its
   response SLA, the alert centre raises a high-severity **SLA breach alert naming the
   owners**, and the lead sits at the top of the Action Queue until handled. Rules only
   assign owners or set follow-up dates - they never send messages or delete anything.
4. **One Customer Timeline** (Customer 360) - the lead phase is now part of the
   relationship history: lead creation, stage moves (new → contacted → offered →
   booked), the calls and notes made before conversion, campaigns, payments, renewals
   and consent events appear on one timeline, with a "Lead" link back to the original
   lead record. The lead drawer links the other way ("Customer Timeline" button) once
   the lead converted.
5. **Lead hygiene** (Leads → Hygiene) - review queue for possible duplicates (same name on
   different numbers; the phone identity index already blocks exact phone duplicates),
   incomplete records (missing store/service/owner/consent) and stale open leads.
   Merge and archive always require confirmation; merging re-points the full activity
   history to the primary record and archives the duplicate with a pointer; archived
   leads can be restored.
6. **Lead detail** - owner, stage, priority with reasons, next follow-up and SLA in the
   header; quick actions for call, WhatsApp, SMS, note (free text), meeting, no-answer;
   explainable score factors; consent state.
7. **Lead-to-Booking Results** (Leads → Analytics, headline on the Dashboard) - for the
   selected window: how many leads were **captured, contacted, booked, attended,
   renewed or lost**, with booking and attendance rates, broken down **by source and by
   staff member**, and filterable **by store (global filter), service, source and
   staff member**. Every count comes from real rows - no estimates.

### Data integrity (everything inter-related)

Every reference in the database points at a real record: leads/customers link through
a unique phone identity, appointments link store + service + trained same-store staff,
payments equal their appointment's final price, customer visits/spend/segment are
derived from real visits, campaign stats are computed from their own messages and
attributed appointments, and slot reservations exist only while a slot is held.
Renewals booked from Customer 360 (re)start the 30-day plan the same way lead
renewals do. Write endpoints validate every incoming id (bogus store/service/person
-> 422/404). Full map + invariants: see **INTEGRITY.md**. Verify anytime:

```bash
python3 scripts/integrity_check.py   # -> INTEGRITY OK - all relationships consistent
```

### End-to-end verification

`scripts/` ships five API test suites that run against the live server
(`uvicorn app.main:app --port 8000`): `test_integrity.py` (31 inter-relation
rules), `test_users.py` (32 auth/scope cases), `test_leadmgmt.py` +
`test_leadmgmt2.py` (63 lead-management cases) and `test_journey.py` - a
33-step journey that books, completes and renews a real lead and asserts every
downstream record (customer, plan, payment, timeline, outcomes) agrees.

### With MongoDB + Redis (production shape)

```bash
docker compose up --build
# MongoDB 7 replica set + Redis + API. Set MONGO_URI/REDIS_URL to switch data layers;
# without them the same code runs on a built-in document store with the same
# unique-index guarantees.
```

---

## What is inside

Seeded demo data (deterministic): 5 stores with a distance matrix, 5 services,
20 employees with skills/shifts/leave, 48 leads across the pipeline, 132+ customers
(including every lead that reached "booked", converted and linked both ways),
~115 appointments today, 30 days of completed visits + payments for trends,
waitlist entries, live + completed campaigns with attributed stats, templates,
automations, consent log, audit log.

A demo realtime simulator emits one event every ~5 seconds (new leads, replies,
follow-ups) so the Live Activity feed and alerts stay alive.

### Screens

1. **Dashboard** – built around three questions: *Who needs attention?* (Needs Attention
   Now, live from the AI call queue), *What should happen next?* (Today's Action Plan
   tiles with live counts), *Which leads are turning into visits and revenue?*
   (Leads To Visits & Revenue funnel strip). Four lead-centric KPI cards with deltas,
   leads/bookings trend (7/14/30 days), lead-source donut with click-through.
2. **Leads** – 5-stage pipeline kanban (+N more), All Leads (search/source/pagination),
   Follow-Ups (overdue highlighting), Hot Leads (score 80+), Lost with reasons + reopen,
   Analytics (stage funnel + source performance with cost per booking), Add Lead,
   CSV import with validation + duplicate check, CSV export, lead detail (stage moves,
   follow-up scheduling, call/WhatsApp/SMS/ad/UGC actions, cross-sell, mark lost, message
   preview, booking picker, activity log, edit with BR-19 locking, explainable score factors).
3. **Customers** – segments (VIP / Loyal / New / At Risk / Inactive / renewal + expired
   stages), search, plan status with next-best-action, Customer 360 (history, payments,
   consent opt-in/out, book renewal, book appointment, source lead, edit).
4. **Appointments** – real calendar (60-day window), day-part + status filters, List view
   with Complete / Reschedule / No-Show / Cancel, hour-by-hour store calendar
   (employees × 12 slots, click-to-book cells), waitlist with offers, holds and renew priority.
5. **Marketing** – lead segments (hot / interested / new / stale offers / overdue /
   by source / by service / lost by reason), customer segments with AI recommendation,
   5-step campaign wizard (audience → message → offer → schedule → review) with
   WhatsApp-style per-lead previews and booking/revenue estimates, active campaigns
   (pause / resume / stop / duplicate / results), automations (6, toggle + run now),
   approved WhatsApp templates, history.
6. **Store & Team** – utilization vs target per store, employee workload, shift roster
   (click to change, capacity updates immediately), add / move / remove employees with
   BR-23 guards, leave management with capacity impact preview, rule-based utilization insights.
7. **Services** (renamed from Products) – service catalogue with duration, staff, price,
   stores offering, trained counts, price breakdown (extra hours / extra staff /
   far-location benefit / cross-store overload offer), plus working search,
   offering-store and sort filters.
8. **AI Calling** (`/ai-calls`) – fully automated shift with guardrails, live call
   cockpit with streaming transcript and Take Over, priority queue (tier + score +
   SLA) with each lead's service, store location and tomorrow's load, cross-store
   overload offers with an explicit % off shown on the call, rerouted-booking chips,
   outcome funnel, recent calls with full transcripts, automatic WhatsApp/SMS message
   log with a test sender, and a Data Sync status chip (manual recheck included).
   Admin-only.

   **Automation loop**: the engine dials leads in priority order using lead + store
   data (pricing, duration, services offered, store area). The AI understands the
   lead's stated slot preference on the call (morning / afternoon / evening) and
   only quotes slots that real employee availability supports - every candidate
   slot comes from the capacity engine (shifts, leaves, existing reservations,
   BR-03/04), preference first, with an honest "earliest available" fallback when
   the preferred part of the day is full. The booking then uses exactly the slot
   quoted on the call, so a morning lead never lands in a 6 PM slot. When the
   lead's preferred store is overloaded for their service tomorrow (utilization
   at/above the BR-06 threshold, service not offered, or no free slot), the call
   offers the nearest alternative stores at a discount - 10% off within 5 km,
   15% beyond (rules AI-OFFER / AI-OFFER-FAR, cap 20%) - with a real available
   slot at the offered store, and books the accepted offer with `rerouted_from`
   + `offer_discount` recorded on the appointment. **Renewal fallback**: when no
   dialable lead is left in the portal, the engine does not go idle - it picks
   expired-plan customers (most overdue first, loyal high-visit customers break
   ties) and calls them to renew their service plan. The renewal call carries the
   same slot rules (stated daypart preference, slots from live employee
   availability, booking exactly the quoted slot); a "renewed" outcome books the
   renewal session with `renew=True`, which restarts the customer's 30-day plan
   (BR-01), assigns the roster staff, sends the WhatsApp confirmation and counts
   in the Renewed KPI. Renewal retries honour call consent and per-customer skip
   windows, and declined renewals rest for 30 days. After every booking,
   completion, cancellation, no-show and reschedule, `sync.verify()` re-derives
   the whole picture (slot reservations, lead pipeline ↔ appointments, customer
   visits/spend, paid payments) and auto-repairs safe mismatches, so the website
   data always matches reality.
9. **Settings** – rule editor with history, integration health, Admin Access (single
   sign-in + team directory), audit log.
10. Cross-cutting: global filter (period + store) on every page and API call, alert
    centre, realtime SSE updates without reload, light theme, Title Case text.
    (The internal step-by-step workflow guide is not shown in the interface.)

### Key business rules implemented (PRD section 6)

- **BR-01/BR-10** 30-day plans, day-25 renewals, plan status stages
- **BR-02..BR-05** 12 hourly slots 10 AM–10 PM, capacity = trained staff free in the
  hour, employee-hours never overlap, services offered only where trained staff suffice
- **BR-06/BR-07** reroute at 90% utilization to the least-busy (then nearest) store,
  10% price benefit beyond 5 km, shown as 3 ranked options with price
- **BR-09** price = base + extra hours × 40% + extra staff × 30%; benefit applies to total
- **BR-12/BR-13/BR-21** consent register, quiet hours 09:00–21:00, 3 promotional
  touches/week — enforced by the dispatch worker, exclusions shown in the wizard
- **BR-14/BR-17/BR-18/BR-19** phone as unique identity, booked leads become customers
  (reversed on cancel), booked leads cannot be lost, store/service locked while booked
- **BR-20** customer segments recomputed live
- **BR-24** revenue from paid payments
- **9.3** double-booking is impossible: unique compound index on
  `(org, store, date, hour, employee)` for slot reservations; a second reservation of the
  same employee-hour returns `409 SLOT_TAKEN` with fresh availability

### API

OpenAPI docs at **/docs**. Base path `/api/v1`, JWT bearer auth, errors shaped
`{"error": {"code", "message", "details"}}` with codes such as `SLOT_TAKEN`,
`PHONE_EXISTS`, `FORBIDDEN`, `VALIDATION_ERROR`. Realtime at `GET /api/v1/events/stream` (SSE).

---

## Architecture notes

```
frontend (React SPA, built to dist/)      backend (FastAPI modular monolith)
  pages/  components/  lib/                 app/
  TanStack Query + Zustand + Recharts       routers/ core people scheduling growth analytics team
  SSE client (lib/rt.ts)                    capacity.py  booking.py  campaigns.py  insights.py
                                            scoring.py  rules.py  realtime.py  seed.py
                                            db.py  → Motor (Mongo) OR built-in document store
```

- **Data layer duality:** `app/db.py` speaks a Mongo-compatible subset. With
  `MONGO_URI` set it runs on MongoDB 7 (Motor, indexes created on boot); without it, an
  in-process store with the same query syntax and unique-index enforcement runs the
  identical service code. Money is stored in paise as integers.
- **Realtime:** writes publish to an in-process bus (Redis pub/sub adapter point in
  production) and to the `events` collection; clients subscribe over SSE scoped by store.
- **Workers:** waitlist offer expiry + queued campaign dispatch run as in-process
  asyncio loops (PRD section 13 maps them to Prefect/Arq workers; quiet hours BR-12 are
  respected - queued messages wait for the 09:00–21:00 window).

### Deliberate deviations from the full PRD (v1 scope)

- **Section 16 (permission matrix) was truncated in the source document**, so the RBAC
  table follows the persona table (section 4); it lives in `app/config.py:PERMISSIONS`.
- Single-tenant demo org (AUTH-07 multi-tenancy and SSO AUTH-06 are P1/P2 and not built).
- Payments are recorded and recognised (BR-24) but no live gateway is wired - provider
  interfaces return demo statuses; integrations are marked `demo` in Settings → Integrations.
- Prefect/Arq pipelines, MLflow model training, MinIO and multi-language are not in this
  build; the insight/chance formulas (PRD 12.3/12.4/12.6) run as the shipped fallbacks
  exactly as the PRD specifies for v1.
- Seed books appointments only inside employee shift hours, so "today" lands at ~93
  appointments (PRD target ~95) - the difference is the shift constraint working.

## Project layout

```
salesflow/
  backend/          FastAPI app (app/main.py boots it; app/seed.py seeds on first run)
  frontend/         Vite + React + TS + Tailwind (dist/ ships prebuilt)
  docker-compose.yml  MongoDB replica set + Redis + API
  .env.example
```
