# Working in this repository

Home Assistant custom integration for **La Poste** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest. See ha-dpd's or ha-gls's `const.py` for a live example |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
This repo follows it exactly.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific notes

- **Keyless consumer endpoint:** one GET per code to `www.laposte.fr`; the
  edge accepts `User-Agent: PostmanRuntime/7.49.1` from aiohttp but rejects its
  normal identity.
- **Two payload vocabularies:** Colissimo maps `currentState.code`; Chronopost
  and tracked international letters (`lettre et ppi import suivi`) map event
  codes, with a sticky `RE1` return unless `DI1` follows it.
- **Delivery window (Colissimo):** `planned_from`/`planned_to` come from
  `estimHourMin`/`estimHourMax`, moved to the day and UTC offset `estimDate`
  names (the offset matters across a clock change) — live,
  the hours can carry the lookup day while `estimDate` (always 01:00) holds
  the delivery day. The window is built with `planned_window` in
  `Europe/Paris`: a lone `estimHourMin` runs to 23:59:59 that day, a lone
  `estimHourMax` is no window. `estimDate` alone is never a window (contract
  says a date is a whole-day window; here it is left out on purpose, as the
  day-only value was seen on parcels that still moved). A parcel that is
  delivered, returning or at a pickup point has none. An `estimDate` on
  Chronopost still warns once.
- **Pickup:** `pickup` is true once any event maps to `at_pickup_point`, or
  the Colissimo code `DESLIVHD` (collected from a relay/locker) shows as
  current state or event (sticky after collection). No pickup point name has
  been seen in a real payload, so `pickup_point` stays `None` until one shows
  a name (listed in `PENDING_CAPABILITIES`). Chronopost's `AG1` covers both "on its way to" and
  "available at" the point, so `en_route_to_pickup_point` reads 0 until a
  code that separates them shows up; both pickup sensors exist regardless.
- **`delivered` is `status is DELIVERED` only.** `contextData.isParcelBack`
  has never been seen true, so a return stays `returning` and active.
- **Intentional `None` fields:** no receiver, weight, dimensions or
  pickup-point details are exposed. `sender` comes from `merchantName` and is
  redacted in diagnostics.
- **Privacy:** redact tracking IDs, URLs, `contextData.partner` and sender data.
  API mechanics remain in `carrier-research/laposte/api/`.

## Running tests

```
python -m pytest tests/ --cov=custom_components.laposte
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in this carrier's own directory in the private
`carrier-research/<slug>/api/`, never in this repo.
