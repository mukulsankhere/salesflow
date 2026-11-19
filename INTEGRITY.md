# Data Integrity — how every piece of data inter-relates

Every entity in SalesFlow references real, existing records. This document maps the
relationships, lists the invariants, and explains how to verify them.

## Relationship map

```
organizations (root)
 ├─ users ──────────── store_ids[] ──────────────► stores
 │    └─ linked 1:1 from employees.user_id (staff logins)
 ├─ stores
 │    ├─ store_distances ─► stores (from_store / to_store, both directions)
 │    ├─ store_services ─► stores + services (BR-05: trained staff on shift)
 │    └─ employees ────── store_id, service_ids[] ─► stores, services
 ├─ services ◄───────── service_ids of employees / leads / customers / appointments
 ├─ leads ──── store_id, service_id, owner_id(user) ─► stores, services, users
 │    ├─ identities (phone_norm is the unique identity: BR-14)
 │    ├─ interactions ─► person_id (lead or customer)
 │    ├─ waitlist ─► person + store + service
 │    └─ customer_id ◄──► customers.lead_id (mutual on conversion, stage='booked')
 ├─ customers ── store_id, last_service_id, lead_id ─► stores, services, leads
 │    ├─ visits / spend / no_shows / segment are DERIVED from their appointments
 │    ├─ payments ─► appointment (amount = appointment.price.final)
 │    ├─ consent_log ─► person
 │    └─ campaign_messages ─► campaign + person
 ├─ appointments ── store, service, customer/lead, source_campaign_id ─► all exist
 │    ├─ employee_ids[]: same-store, trained for the service (BR-05)
 │    ├─ slot_reservations: exist ONLY while status='booked'; released on
 │    │   complete / cancel / no-show (booking.py)
 │    └─ completing a visit: customer visits+spend updated, payment row created
 ├─ campaigns ── segment_key ∈ known segments; stats DERIVED from rows:
 │    sent = campaign_messages count; booked/revenue = attributed appointments
 └─ audit_log ── who_id ─► users; entity_id ─► the referenced document
```

## Invariants enforced by code

| Where | Rule |
| --- | --- |
| `POST /leads` | store_id + service_id must exist (422 otherwise); phone dedupe via identities (BR-14) |
| `POST /employees`, `/employees/{id}/move` | store must exist; service_ids must all exist |
| `POST /waitlist` | person_type ∈ {lead, customer}; person, store, service must exist |
| `POST /appointments` (booking.book) | person, store, service validated; staff auto-placed from the store's trained roster |
| `booking.complete` | payment row created; customer visits/spend incremented (BR-24) |
| `booking.cancel` | reservations released; converted customer removed if never visited (BR-17) |
| `POST /auth/users` | roles validated, store_ids must exist, duplicate email/phone rejected (409) |
| `/ingest/{portal}` | unknown service falls back to a real service so leads are never orphaned |

## Verifying

The checker loads the app's own data layer, seeds a fresh database, and runs every
rule (FK edges, denormalized snapshots, campaign stats vs rows, derived customer
totals, reservation lifecycle, audit entity refs):

```bash
python3 scripts/integrity_check.py
# -> INTEGRITY OK - all relationships consistent
```

Runtime mutations are covered too: `scripts/test_integrity.py` (a) hits the API with
bogus references and expects 422/404, (b) runs a full flow — create lead → book →
complete (payment + spend) → cancel (reservations released) — then re-runs the whole
scan on the mutated database.
