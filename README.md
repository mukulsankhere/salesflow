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

Open **http://localhost:8000** and sign in with a demo role:

| Role | Login | Scope |
| --- | --- | --- |
| Admin | `admin@salesflow.app` | Everything, incl. Settings + Audit |
| Manager | `manager@salesflow.app` | Vijay Nagar store only |
| Sales | `sales@salesflow.app` | Vijay Nagar + Palasia |
| Marketing | `marketing@salesflow.app` | Campaigns + read leads |
| Staff | `staff@salesflow.app` | My Schedule only |
| Read-only | `viewer@salesflow.app` | Dashboard view |

Password for all demo users: `Demo@123`

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
20 employees with skills/shifts/leave, 48 leads across the pipeline, 132 customers with
30-day plans, ~93 appointments today, 30 days of completed visits + payments for trends,
waitlist entries, live + completed campaigns, templates, automations, consent log, audit log.

A demo realtime simulator emits one event every ~5 seconds (new leads, replies,
follow-ups) so the Live Activity feed and alerts stay alive.

### Screens

1. **Dashboard** – 6 KPI cards with deltas, leads/bookings trend (7/14/30 days),
   lead-source donut with click-through, AI insights strip, Live Activity feed,
   Today's Action Plan (4 tiles).
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
   BR-23 guards, leave management with capacity impact preview, AI utilization insights.
7. **Products** – service catalogue with duration, staff, price, stores offering,
   trained counts, price breakdown (extra hours / extra staff / far-location benefit).
8. **AI Insights** – recommendations in 4 groups with plain-language reasons, evidence,
   expected bookings + revenue, one-click apply / dismiss, impact summary. Applied
   insights do not repeat for the same filter scope (AI-03).
9. **My Schedule** (staff), **Settings** (rule editor with history, integration health,
   store scope, audit log).
10. Cross-cutting: global filter (period + store) on every page and API call, alert
    centre, 8-step Workflow menu, realtime SSE updates without reload, light theme,
    Title Case text.

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
