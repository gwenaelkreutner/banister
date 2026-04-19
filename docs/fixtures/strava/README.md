# Fixtures de simulation Strava (dev)

Endpoint dev-only: `POST /dev/strava/simulate-activity`

> Pré-requis: `environment=development` et un compte déjà lié à Strava (avec `oauth_connections.provider_user_id` égal à `owner_id`).

## Scénario 1 — Ride endurance (cyclisme outdoor)

```bash
curl -X POST http://localhost:8000/dev/strava/simulate-activity \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --argjson activity "$(cat docs/fixtures/strava/ride_endurance.json)" '{owner_id:123456, object_id:990001, activity:$activity}')"
```

## Scénario 2 — Virtual ride (cyclisme indoor)

```bash
curl -X POST http://localhost:8000/dev/strava/simulate-activity \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --argjson activity "$(cat docs/fixtures/strava/virtual_ride_threshold.json)" '{owner_id:123456, object_id:990002, activity:$activity}')"
```

## Scénario 3 — Activité non-cyclisme (ignorée)

```bash
curl -X POST http://localhost:8000/dev/strava/simulate-activity \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --argjson activity "$(cat docs/fixtures/strava/run_ignored.json)" '{owner_id:123456, object_id:990003, activity:$activity}')"
```
