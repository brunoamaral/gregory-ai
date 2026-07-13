# Design note: normalizing Trials.phase

## The problem

`Trials.phase` (TextField) stores the raw registry value verbatim, written by four
separate paths:

- `feedreader_trials.py` (EU CTIS RSS, via `EUTrialParser` in `gregory/classes.py`)
- `feedreader_trials_ctgov.py` (ClinicalTrials.gov API v2 — `phases` array, joined `", "`)
- `importWHOXML.py` (WHO ICTRP XML export)
- API POST via `TrialSerializer`

Each registry uses its own vocabulary for the same real-world concept, so filtering or
counting "Phase 3 trials" against the raw column silently misses rows. A non-exhaustive
sample of raw values seen in the DB for what is, semantically, the same phase:

```
"Phase 3"            "PHASE3"           "III"              "3"
"Phase III"          "Therapeutic confirmatory  (Phase III)"
```

...and EU CTIS reports phase as a yes/no matrix across all four phases in one string:

```
Human pharmacology (Phase I): no
Therapeutic exploratory (Phase II): yes
Therapeutic confirmatory - (Phase III): no
Therapeutic use (Phase IV): no
```

`phase` must stay untouched — it's the source-of-record value, and rewriting it in place
would break source-sync fidelity (re-importing the same registry record should reproduce
the same raw value). So normalization lives in a **derived companion field**,
`phase_normalized`, computed by a pure function and kept in lockstep automatically.

## Canonical vocabulary

`gregory.utils.trial_field_normalizers.TrialPhase` (a `models.TextChoices`):

| Value | Label |
|---|---|
| `early_phase_1` | Early Phase 1 |
| `phase_1` | Phase 1 |
| `phase_1_2` | Phase 1/2 |
| `phase_2` | Phase 2 |
| `phase_2_3` | Phase 2/3 |
| `phase_3` | Phase 3 |
| `phase_3_4` | Phase 3/4 |
| `phase_4` | Phase 4 |
| `post_market` | Post-market |
| `not_applicable` | Not applicable |
| `other` | Other |

## Mapping rules

`normalize_phase(raw: str | None) -> str | None`, in order:

1. **Empty input.** `None`, `""`, or whitespace-only → `None` (phase_normalized stays
   unset — there's nothing to normalize).
2. **Clean.** Collapse every whitespace run (spaces, tabs, newlines) to a single space,
   strip, casefold. This alone fixes pretty-printed WHO/EU exports and stray double
   spaces (e.g. `"therapeutic confirmatory  (phase iii)"` → matches the table below).
3. **EudraCT/CTIS yes/no matrix.** If the cleaned string contains
   `"human pharmacology (phase i)"` followed by a colon anywhere after it, treat it as
   the matrix format and pull every `(phase <roman>): <yes|no|blank>` pair out with
   `re.findall(r"\(phase (i{1,3}|iv)\)[^:]*:\s*(yes|no)?", cleaned)` — label text in
   front of each `(phase X)` varies ("Therapeutic use (Phase IV)" vs "... - (Phase IV)")
   and is ignored. Collect the phases marked `"yes"` as a set of ints, then apply the
   **span rule** below.
4. **Exact lookup.** Look the cleaned string up in `_EXACT_MATCHES` — a flat dict
   covering every distinct raw `phase` value observed in the DB at the time this was
   built (see the module for the full table).
5. **Conservative fallback.** For formatting variants not yet in the table, extract every
   `phase <roman|digit>` token with `re.findall(r"phase\s*/?\s*(iv|i{1,3}|[0-4])\b",
   cleaned)`, convert to ints (`0` stays `0`), and apply the span rule. This is what
   catches e.g. `"Phase 3/Phase 4"` without needing an explicit table entry.
6. **Otherwise → `TrialPhase.OTHER`**, and the raw value is logged
   (`logger.info("Unmapped trial phase value: %r", raw)`) — filtering the admin on
   `phase_normalized = other` is the human review surface for anything the mapper
   doesn't yet understand.

### Span rule

Shared by steps 3 and 5. Given a set of phase ints extracted from the input:

| Set | Result |
|---|---|
| `{0}` | `early_phase_1` |
| `{1}` | `phase_1` |
| `{2}` | `phase_2` |
| `{3}` | `phase_3` |
| `{4}` | `phase_4` |
| `{1, 2}` | `phase_1_2` |
| `{2, 3}` | `phase_2_3` |
| `{3, 4}` | `phase_3_4` |
| anything else (empty, non-adjacent pairs, `0` combined with another phase, 3+ phases) | `other` |

## Where the mapper lives

`django/gregory/utils/trial_field_normalizers.py`. It does **not** import
`gregory.models` — `models.py` imports `TrialPhase` and `normalize_phase` from here, not
the other way round, so the module stays reusable for the next derived field without a
circular import.

## The save() guarantee

`Trials.save()` (in `django/gregory/models.py`) recomputes `phase_normalized` from
`phase` on every write:

```python
def save(self, *args, **kwargs):
	self.phase_normalized = normalize_phase(self.phase)
	update_fields = kwargs.get("update_fields")
	if update_fields is not None and "phase" in update_fields and "phase_normalized" not in update_fields:
		kwargs["update_fields"] = [*update_fields, "phase_normalized"]
	super().save(*args, **kwargs)
```

This covers all four write paths — they all go through `.objects.create()` or
`.save()`, including `save(update_fields=["phase"])`, which the code above extends to
also persist `phase_normalized`. `phase_normalized` is `editable=False`, so it can never
be set directly through a form or API POST — it is always derived.

**`bulk_update()` bypasses `save()`** and therefore bypasses this hook. The only place
that matters today is the backfill command below and the admin "Recompute normalized
phase" action, both of which recompute `phase_normalized` explicitly before calling
`bulk_update`.

`phase_normalized` is a plain field on `Trials`, so django-simple-history
(`HistoricalRecords`) picks it up automatically — no extra wiring needed.

## Admin tuning workflow

When registries introduce a new spelling the mapper doesn't recognise, it lands in
`other`:

1. In `/admin/gregory/trials/`, filter **Phase normalized** = `Other`.
2. Eyeball the raw `phase` values on those rows (or check `logger.info` output / the
   backfill command's OTHER report — see below).
3. Add the new raw value(s) to `_EXACT_MATCHES` in
   `gregory/utils/trial_field_normalizers.py` (prefer this over loosening the generic
   fallback regex, which is meant to stay conservative).
4. Deploy the change.
5. Back in the admin, select the affected rows (still filtered to `Other`, or select all)
   and run the **"Recompute normalized phase"** action — it re-derives
   `phase_normalized` for the selected queryset immediately, without waiting for the next
   registry sync to re-save each row.

## Backfill command

```
python manage.py backfill_trial_phases [--batch-size 1000] [--dry-run]
```

Scans every `Trials` row, recomputes `phase_normalized`, and `bulk_update`s the rows
whose stored value differs, in batches. Intentionally skips django-simple-history — this
is a derived field recomputed from data already in the row, not a meaningful edit, and
`bulk_update` can't write history anyway (it doesn't call `save()`). Reports total
scanned, total updated, a per-canonical-value tally, and every distinct raw `phase` value
that mapped to `other` — that list is the review queue for step 2 of the tuning workflow
above. Idempotent: rerunning after the DB is caught up updates nothing.

## Prod runbook

1. Deploy this change (model + migration `0078_historicaltrials_phase_normalized_and_more`).
2. `docker exec gregory python manage.py migrate`
3. One-time: `docker exec gregory python manage.py backfill_trial_phases`
4. Optional: review the OTHER list the backfill prints, extend `_EXACT_MATCHES`, deploy,
   rerun the backfill (or use the admin action) to pick up the new mappings.

## Extension recipe: recruitment_status, study_type

This design is meant to repeat directly for the next raw field with a messy multi-registry
vocabulary:

1. In `gregory/utils/trial_field_normalizers.py`, add a new `models.TextChoices` (e.g.
   `RecruitmentStatus`) and a new pure function (e.g. `normalize_recruitment_status`).
   Same module — it's named `trial_field_normalizers`, not `trial_phase_normalizer`, for
   exactly this reason.
2. In `models.py`, add `recruitment_status_normalized` next to `recruitment_status`
   (`CharField`, `choices=`, `db_index=True`, `editable=False`), and extend
   `Trials.save()` to also set it (and extend the `update_fields` shim the same way for
   both `phase` and `recruitment_status`).
3. `makemigrations gregory`.
4. Add a `backfill_recruitment_statuses` command mirroring
   `backfill_trial_phases.py`.
5. Wire up the API filter/serializer/CSV/XLSX columns and the admin
   list_filter/readonly_fields/fieldset/recompute action the same way as `phase`/
   `phase_normalized` above.
6. Test coverage mirrors `gregory/tests/test_trial_phase_normalization.py`.

## Cross-reference: multi-source merge

See `docs/trials-multi-source-merge.md` — `phase` is a "genuinely shared / contested"
field where different importers can flip-flop the raw value depending on import order.
`phase_normalized` is immune to that: because it's recomputed from whatever `phase`
currently holds on every save, it always reflects the current raw value's canonical form,
never a stale one from a previous importer.

## Tests

`gregory/tests/test_trial_phase_normalization.py` — parametrized coverage of every raw
value in `_EXACT_MATCHES`, the EudraCT matrix (all phase combinations, blank cells, label
variants), the generic fallback, unmapped values, the `Trials.save()` hook (including
`update_fields`), the backfill command (including `--dry-run` and idempotency), and the
API filter (`?phase_normalized=phase_3`, invalid choice, response body). The admin
"Recompute normalized phase" action has a smoke test alongside the other admin tests.
