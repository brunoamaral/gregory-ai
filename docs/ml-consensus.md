# ML consensus and thresholds

> Audience: operators and data scientists configuring GregoryAI subject-level ML behaviour.

GregoryAI uses a dual-filter approach to determine article relevance: a probability threshold combined with a consensus requirement across the three ML models. This gives per-subject control over the precision/recall trade-off.

---

## How it works

Every ML prediction has a `probability_score` between 0.0 and 1.0. The dual filter applies in two steps:

1. **Probability threshold** — only predictions at or above the threshold are counted as "votes for relevance".
2. **Consensus type** — the number of votes required to mark the article as ML-relevant.

An article that clears both filters for any of its associated subjects is included in digests and the `?relevant=true` API response.

Manual relevance flags always override ML predictions: a manually marked article is always included, and a manually excluded article is always excluded regardless of ML scores.

### Consensus types

Each subject has an `ml_consensus_type` field with three options:

| Value | Label | Meaning |
|:------|:------|:--------|
| `any` | Any model | At least 1 model above threshold |
| `majority` | Majority vote | At least 2 of 3 models above threshold |
| `all` | Unanimous | All 3 models above threshold |

### Example

Article with predictions: BERT 0.92, LGBM 0.75, LSTM 0.88. Threshold = 0.8.

- BERT: 0.92 ✓ above threshold
- LGBM: 0.75 ✗ below threshold
- LSTM: 0.88 ✓ above threshold

Result: 2 models above threshold.

- `any` → included
- `majority` → included
- `all` → excluded (needs 3)

---

## Recommended settings

| Goal | Setting |
|:-----|:--------|
| High recall (capture more articles) | `any` + threshold 0.7 |
| Balanced (default recommendation) | `majority` + threshold 0.8 |
| High precision (fewer false positives) | `all` + threshold 0.8–0.9 |

---

## Configuration

### In the Django admin

1. Go to **Gregory > Subjects**.
2. Open a subject.
3. Set **ML consensus type** to `any`, `majority`, or `all`.
4. Save.

The change takes effect immediately for the next digest send and for `/articles/?relevant=true` queries.

### Via the API (if enabled)

```bash
PATCH /api/subjects/4/
Content-Type: application/json

{"ml_consensus_type": "majority"}
```

---

## API integration

The `ml_threshold` query parameter on `/articles/` lets callers override the subject's stored threshold at query time:

```bash
# Default threshold (subject's stored value, typically 0.8)
GET /articles/?relevant=true

# Custom threshold
GET /articles/?relevant=true&ml_threshold=0.9

# Combined with team/subject
GET /articles/?relevant=true&team_id=1&subject_id=4&ml_threshold=0.85
```

The `relevance_counts` endpoint shows which threshold was applied:

```bash
GET /api/articles/relevance_counts/?team_id=1&ml_threshold=0.9
```

```json
{
  "manual_relevant": 45,
  "ml_relevant": 67,
  "both_relevant": 12,
  "total_unique_relevant": 100,
  "ml_threshold_used": 0.9,
  "breakdown": {
    "manual_only": 33,
    "ml_only": 55,
    "both": 12
  }
}
```

---

## Effect on weekly summary emails

The `send_weekly_summary` command uses the same consensus logic as the API. Articles are ordered by priority:

1. Manually relevant articles (highest priority)
2. ML consensus strength (more agreeing models = higher score)
3. Discovery date (tie-breaker)

To test without sending:

```bash
python manage.py send_weekly_summary --dry-run --debug
```

The debug output shows the consensus evaluation per article and per subject, and reports which articles would be included or excluded.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|:--------|:-------------|:----|
| No articles in digest | Subject `auto_predict` is off, or consensus too strict | Check `auto_predict=True`; try `any` consensus |
| Too many articles | Consensus too loose or threshold too low | Switch to `majority` or raise threshold |
| Too few articles | Threshold too high | Lower threshold or switch to `any` |
| API and email results differ | Stale ML predictions | Re-run `pipeline` management command |

---

## Database field reference

Model: `Subject` (in `gregory/models.py`)

| Field | Type | Default | Choices |
|:------|:-----|:--------|:--------|
| `ml_consensus_type` | CharField | `'any'` | `'any'`, `'majority'`, `'all'` |

Added in migration `0025_add_ml_consensus_type_to_subject.py`.
