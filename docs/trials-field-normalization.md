# Design note: normalizing Trials fields

## The problem

Several `Trials` columns (`TextField`/`CharField`) store the raw registry value verbatim,
written by four separate paths:

- `feedreader_trials.py` (EU CTIS RSS, via `EUTrialParser` in `gregory/classes.py`)
- `feedreader_trials_ctgov.py` (ClinicalTrials.gov API v2)
- `importWHOXML.py` (WHO ICTRP XML export)
- API POST via `TrialSerializer`

Each registry uses its own vocabulary for the same real-world concept, so filtering or
counting against the raw column silently misses rows. Two fields are normalized so far:

- **`phase`** — the stage of testing (Phase 1–4, ...).
- **`recruitment_status`** — whether the trial is recruiting, completed, etc.

For both, the raw column must stay untouched — it's the source-of-record value, and
rewriting it in place would break source-sync fidelity (re-importing the same registry
record should reproduce the same raw value). So normalization lives in a **derived
companion field per raw field** (`phase_normalized`, `recruitment_status_normalized`, ...),
each computed by a pure function and kept in lockstep automatically. This doc covers both
fields and the shared machinery; see the per-field sections below for vocabulary and
mapping rules.

## Where the mapper lives

`django/gregory/utils/trial_field_normalizers.py`. It does **not** import
`gregory.models` — `models.py` imports from here, not the other way round, so the module
stays reusable for the next derived field without a circular import.

The module also defines the registry that drives every generic piece of machinery below:

```python
NORMALIZED_TRIAL_FIELDS = (
	("phase", "phase_normalized", normalize_phase),
	("recruitment_status", "recruitment_status_normalized", normalize_recruitment_status),
)
```

Each entry is `(raw field name, derived field name, normalizer function)`. `Trials.save()`,
the backfill command, and the admin "Recompute normalized fields" action all iterate this
tuple instead of hardcoding field names — adding a new field to it (plus the matching model
field) is most of the work of extending this pattern (see the extension recipe at the
bottom).

## Field: `phase` → `phase_normalized`

### Sample raw values

A non-exhaustive sample of raw values seen in the DB for what is, semantically, the same
phase:

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

### Canonical vocabulary

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

### Mapping rules

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

## Field: `recruitment_status` → `recruitment_status_normalized`

Unlike `phase`, the recruitment-status vocabulary is small and closed, so there is
deliberately **no generic token fallback** — only the exact-match table below and `other`.
Guessing at a new registry spelling with a regex would be more likely to mis-map a status
than to help; new spellings should be reviewed and added to the table explicitly (same
"admin tuning workflow" as `phase`, below).

### Canonical vocabulary

`gregory.utils.trial_field_normalizers.TrialRecruitmentStatus` (a `models.TextChoices`):

| Value | Label |
|---|---|
| `not_yet_recruiting` | Not yet recruiting |
| `recruiting` | Recruiting |
| `enrolling_by_invitation` | Enrolling by invitation |
| `active_not_recruiting` | Active, not recruiting |
| `not_recruiting` | Not recruiting |
| `suspended` | Suspended |
| `completed` | Completed |
| `terminated` | Terminated |
| `withdrawn` | Withdrawn |
| `unknown` | Unknown |
| `other` | Other |

### Mapping table

Keys are the whitespace-collapsed, casefolded raw value (so `"Not Recruiting"`,
`"NOT RECRUITING"`, and `"not recruiting"` are all the same key):

| Canonical | Raw values | Source |
|---|---|---|
| `not_yet_recruiting` | `not_yet_recruiting` | ClinicalTrials.gov |
| | `authorised, recruitment pending` | EU CTIS |
| `recruiting` | `recruiting` | ClinicalTrials.gov, WHO ICTRP |
| | `ongoing, recruiting` | EU CTIS |
| | `authorised, recruiting` | EU CTIS |
| `enrolling_by_invitation` | `enrolling_by_invitation` | ClinicalTrials.gov |
| `active_not_recruiting` | `active_not_recruiting` | ClinicalTrials.gov |
| | `ongoing, recruitment ended` | EU CTIS |
| `not_recruiting` | `not recruiting` | WHO ICTRP |
| `suspended` | `suspended` | ClinicalTrials.gov |
| | `temporarily halted` | WHO ICTRP |
| | `temporarily_not_available` | ClinicalTrials.gov (expanded access) |
| `completed` | `completed` | ClinicalTrials.gov, WHO ICTRP |
| | `ended` | EU CTIS |
| `terminated` | `terminated` | ClinicalTrials.gov |
| `withdrawn` | `withdrawn` | ClinicalTrials.gov |
| `unknown` | `unknown` | ClinicalTrials.gov (not verified in >2 years) |
| | `authorised` | EUCTR/CTIS |
| | `not available` | WHO ICTRP |
| `other` | `available`, `no_longer_available`, `approved_for_marketing` | ClinicalTrials.gov (expanded-access program statuses) |

### Judgment calls

Three mappings above are deliberate, non-obvious choices rather than a straight
one-registry-value-to-one-bucket exercise:

1. **WHO's `"Not recruiting"` gets its own bucket (`not_recruiting`), not
   `active_not_recruiting`.** WHO ICTRP's generic status doesn't say whether the trial is
   pre-start, still ongoing, or fully done — folding it into `active_not_recruiting` would
   claim more precision than the source actually provides.
2. **`"Authorised"` (bare, no recruiting/pending suffix) → `unknown`, not `other`.** EUCTR
   /CTIS use it to mean "approved to run, but the feed doesn't say whether it has started
   recruiting" — that's a real "we don't know the recruitment state" case, same bucket as
   ClinicalTrials.gov's `UNKNOWN` (stale/unverified), not the same as the expanded-access
   statuses below.
3. **ClinicalTrials.gov expanded-access program statuses (`available`,
   `no_longer_available`, `approved_for_marketing`) → `other`, not `unknown` or a new
   bucket.** These describe an access *program*, not a trial's recruitment state — they
   aren't really answerable in this vocabulary at all. Left in `other` deliberately so the
   raw string (which is actually informative here) shows on display surfaces instead of a
   generic "Unknown" label.

## The save() guarantee

`Trials.save()` (in `django/gregory/models.py`) recomputes every derived field registered
in `NORMALIZED_TRIAL_FIELDS` from its raw counterpart on every write:

```python
def save(self, *args, **kwargs):
	update_fields = kwargs.get("update_fields")
	extra_update_fields = []
	for raw_field, derived_field, normalizer in NORMALIZED_TRIAL_FIELDS:
		setattr(self, derived_field, normalizer(getattr(self, raw_field)))
		if (
			update_fields is not None
			and raw_field in update_fields
			and derived_field not in update_fields
		):
			extra_update_fields.append(derived_field)
	if extra_update_fields:
		kwargs["update_fields"] = [*update_fields, *extra_update_fields]
	super().save(*args, **kwargs)
```

This covers all four write paths — they all go through `.objects.create()` or `.save()`,
including e.g. `save(update_fields=["phase"])`, which the code above extends to also
persist `phase_normalized` (and, symmetrically, `update_fields=["recruitment_status"]`
extends to persist `recruitment_status_normalized`). Every derived field is
`editable=False`, so none can be set directly through a form or API POST — they are always
derived.

**`bulk_update()` bypasses `save()`** and therefore bypasses this hook. The only place
that matters today is the backfill command below and the admin "Recompute normalized
fields" action, both of which recompute the derived fields explicitly before calling
`bulk_update`.

The derived fields are plain fields on `Trials`, so django-simple-history
(`HistoricalRecords`) picks them up automatically — no extra wiring needed.

## Admin tuning workflow

When registries introduce a new spelling the mapper doesn't recognise, it lands in
`other`:

1. In `/admin/gregory/trials/`, filter **Phase normalized** = `Other` (or **Recruitment
   status normalized** = `Other`).
2. Eyeball the raw values on those rows (or check `logger.info` output / the backfill
   command's OTHER report — see below).
3. Add the new raw value(s) to `_EXACT_MATCHES` (phase) or
   `_RECRUITMENT_STATUS_EXACT_MATCHES` (recruitment status) in
   `gregory/utils/trial_field_normalizers.py`. For phase, prefer this over loosening the
   generic fallback regex, which is meant to stay conservative; recruitment status has no
   fallback at all, so this table is the only way to widen its coverage.
4. Deploy the change.
5. Back in the admin, select the affected rows (still filtered to `Other`, or select all)
   and run the **"Recompute normalized fields"** action — it re-derives every registered
   normalized field for the selected queryset immediately, without waiting for the next
   registry sync to re-save each row.

## Backfill command

```
python manage.py backfill_trial_normalized_fields [--field phase] [--field recruitment_status] [--batch-size 1000] [--dry-run]
```

`--field` is repeatable and/or comma-separated (`--field phase,recruitment_status`);
omitting it backfills every field registered in `NORMALIZED_TRIAL_FIELDS`. Scans every
`Trials` row once, recomputes the selected derived field(s), and `bulk_update`s the rows
whose stored value(s) differ, in batches. Intentionally skips django-simple-history — these
are derived fields recomputed from data already in the row, not a meaningful edit, and
`bulk_update` can't write history anyway (it doesn't call `save()`). Reports total scanned,
total values changed, a per-field per-canonical-value tally, and (per field) every distinct
raw value that mapped to `other` — that list is the review queue for step 2 of the tuning
workflow above. Idempotent: rerunning after the DB is caught up updates nothing.

## Prod runbook

1. Deploy this change (model + migrations
   `0078_historicaltrials_phase_normalized_and_more` and
   `0079_historicaltrials_recruitment_status_normalized_and_more`).
2. `docker exec gregory python manage.py migrate`
3. One-time: `docker exec gregory python manage.py backfill_trial_normalized_fields`
   (backfills both `phase_normalized` and `recruitment_status_normalized` in one pass).
4. Optional: review the OTHER list(s) the backfill prints, extend the relevant mapping
   table, deploy, rerun the backfill (or use the admin action) to pick up the new mappings.

## Extension recipe: study_type

This design is meant to repeat directly for the next raw field with a messy multi-registry
vocabulary:

1. In `gregory/utils/trial_field_normalizers.py`, add a new `models.TextChoices` (e.g.
   `TrialStudyType`), a new pure function (e.g. `normalize_study_type`), and add a new
   `("study_type", "study_type_normalized", normalize_study_type)` entry to
   `NORMALIZED_TRIAL_FIELDS`. Same module — it's named `trial_field_normalizers`, not
   `trial_phase_normalizer`, for exactly this reason.
2. In `models.py`, add `study_type_normalized` next to `study_type` (`CharField`,
   `choices=`, `db_index=True`, `editable=False`). `Trials.save()` needs **no changes** —
   it already iterates `NORMALIZED_TRIAL_FIELDS`, so the new entry is picked up
   automatically, including the `update_fields` shim.
3. `makemigrations gregory`.
4. No new backfill command needed —
   `backfill_trial_normalized_fields` already covers every registered field; the new one
   is included by default, or scoped alone with `--field study_type`.
5. Wire up the API filter/serializer/CSV/XLSX columns and the admin
   list_filter/readonly_fields/fieldset entries the same way as `phase`/`phase_normalized`
   above. The admin "Recompute normalized fields" action needs no changes — it also
   iterates `NORMALIZED_TRIAL_FIELDS`.
6. Test coverage mirrors `gregory/tests/test_trial_phase_normalization.py` /
   `gregory/tests/test_trial_recruitment_status_normalization.py`.

## Cross-reference: multi-source merge

See `docs/trials-multi-source-merge.md` — `phase` and `recruitment_status` are
"genuinely shared / contested" fields where different importers can flip-flop the raw value
depending on import order. Their `_normalized` companions are immune to that: because each
is recomputed from whatever the raw field currently holds on every save, it always reflects
the current raw value's canonical form, never a stale one from a previous importer.

## Tests

- `gregory/tests/test_trial_phase_normalization.py` — parametrized coverage of every raw
  value in `_EXACT_MATCHES`, the EudraCT matrix (all phase combinations, blank cells, label
  variants), the generic fallback, unmapped values, the `Trials.save()` hook (including
  `update_fields`), the generalized backfill command (including `--field`, `--dry-run`, and
  idempotency), and the API filter (`?phase_normalized=phase_3`, invalid choice, response
  body).
- `gregory/tests/test_trial_recruitment_status_normalization.py` — mirrors the above for
  `recruitment_status`: every raw value in `_RECRUITMENT_STATUS_EXACT_MATCHES` (including
  original casings), `None`/empty/whitespace → `None`, unmapped → `other` + logged, the
  `Trials.save()` hook, backfill coverage (including a `--field phase` run leaving
  `recruitment_status_normalized` untouched), and the API filter/serializer tests.
- The admin "Recompute normalized fields" action has a smoke test alongside the other admin
  tests in `gregory/tests/test_trial_phase_normalization.py`.
