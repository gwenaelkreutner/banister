# Plugging a nutrition source into Banister

Banister is a cycling coach: it does not log meals, estimate calories or store food data. It only
**reads** daily intake when that helps the training analysis, through the `get_nutrition_summary`
tool of the coach. You choose where those figures come from.

| Your setup | What to do | What the coach sees |
|---|---|---|
| Nothing | Nothing | "nothing logged" |
| You log food in an app that syncs to intervals.icu | Nothing: the `kcalConsumed`, `carbohydrates`, `protein`, `fatTotal` and `hydrationVolume` wellness fields are already read | Those daily values |
| Your own app or database | Expose the endpoint below and set `NUTRITION_API_URL` | Your daily values |

Banister never connects to your database directly: a small endpoint keeps your schema yours, and
a failure on your side can never affect the coach.

## The contract

```
GET {NUTRITION_API_URL}/v1/days?from=YYYY-MM-DD&to=YYYY-MM-DD
Authorization: Bearer {NUTRITION_API_TOKEN}      (only if a token is configured)
```

Answer `200` with a JSON array, one object per day of the range:

```json
[
  {"date": "2026-11-02", "status": "empty", "kcal": null},
  {"date": "2026-11-03", "status": "complete", "kcal": 2340, "kcal_min": 2110, "kcal_max": 2570,
   "protein_g": 118, "carbs_g": 290, "fat_g": 72, "hydration_ml": 1500}
]
```

| Field | Required | Meaning |
|---|---|---|
| `date` | yes | The athlete's local day, `YYYY-MM-DD` |
| `status` | no | `complete`, `partial`, `empty`, `not_tracked`, or `estimated_default` for a day filled with a typical value. Tells the coach whether a low total means anything |
| `kcal` | no | Estimated intake. **`null` means nothing logged, never zero** |
| `kcal_min`, `kcal_max` | no | Uncertainty range, if you have one |
| `protein_g`, `carbs_g`, `fat_g`, `hydration_ml` | no | `null` or absent when unknown |

Rules Banister relies on:

- The endpoint is read-only and fast: Banister gives up after 4 seconds and carries on without it.
- Unknown fields are ignored, so you can return more.
- Any error answers "unavailable" to the coach, which then says so instead of guessing.
- Figures are presented to the athlete as estimates, whatever their source.

## A minimal source

```python
from fastapi import FastAPI, Query
import sqlite3

app = FastAPI()

@app.get("/v1/days")
def days(from_: str = Query(alias="from"), to: str = Query()):
    with sqlite3.connect("food.db") as db:
        rows = db.execute(
            "SELECT day, SUM(kcal) FROM meals WHERE day BETWEEN ? AND ? GROUP BY day", (from_, to)
        ).fetchall()
    return [{"date": day, "status": "complete", "kcal": kcal} for day, kcal in rows]
```

Days missing from the answer are simply not shown to the coach.

## Connecting it

```dotenv
NUTRITION_API_URL=http://your-service:8012
NUTRITION_API_TOKEN=a-read-only-token
```

If it runs in another Docker Compose project, put both on a shared network from a
`docker-compose.override.yml`, so the base file keeps working for everyone else:

```yaml
services:
  app:
    networks: [default, nutrition_net]
networks:
  nutrition_net:
    external: true
```

Give Banister a token that can only read. Food logs are health data: keep the endpoint off the
public internet.

## Going the other way

A nutrition app can adapt to training through Banister's internal API. Set `INTERNAL_API_TOKEN`, then:

```
GET {BANISTER_URL}/internal/v1/context?days=14&ahead=14
Authorization: Bearer {INTERNAL_API_TOKEN}
```

It returns recent rides (`duration_min`, `tss`, and `kj` when a power meter measured it), planned
sessions, `ftp`, dated weigh-ins (`weight`, `weight_history`) and `profile` (`age`, `sex`,
`height_cm`). Treat it as optional: your app should keep working when Banister is off.
