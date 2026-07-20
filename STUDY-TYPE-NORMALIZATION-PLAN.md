# Plan: `study_type` normalization (P2)

Executor plan (written for Sonnet); every design decision is made — follow the spec,
don't re-litigate. This is the **fourth and last** field to go through the established
companion-column pattern, and `docs/trials-field-normalization.md` already contains a
step-by-step extension recipe naming `study_type` explicitly (end of the doc, "Adding a
new normalized field"). **Follow that recipe** — this plan supplies the vocabulary, the
mapping table, and the decisions it can't.

## Why

`study_type` is the last unnormalized categorical field on `Trials`. Verified on the dev
DB (= prod) 2026-07-20: **16,618 of 29,798 trials populated, 27 distinct raw spellings**
for what is essentially three concepts. `?study_type=Interventional` is an `icontains`
filter, so it silently misses the 9,708 rows spelled `INTERVENTIONAL`… except that
`icontains` is case-insensitive, so it *does* match those — but it also matches
`Interventional clinical trial of medicinal product` and `Interventional study`, while
missing `Intervention` (91 rows, REBEC) entirely, and there is no way to count by study
type or to exclude expanded-access records. Same class of problem `phase` had.

## The raw data (complete inventory — all 27 values, dev DB 2026-07-20)

| Count | Raw value | Registry |
|------:|:----------|:---------|
| 9,708 | `INTERVENTIONAL` | ClinicalTrials.gov |
| 3,033 | `OBSERVATIONAL` | ClinicalTrials.gov |
| 1,017 | `Interventional` | ANZCTR, NL-OMON, ISRCTN, JPRN |
| 913 | `Interventional clinical trial of medicinal product` | EU CTR, EU CTIS |
| 697 | `interventional` | IRCT |
| 402 | `Interventional study` | ChiCTR |
| 191 | `Observational` | mixed |
| 171 | `Observational study` | ChiCTR |
| 125 | `Observational non invasive` | NL-OMON |
| 109 | `observational` | DRKS |
| 91 | `Intervention` | REBEC |
| 64 | `Observational invasive` | NL-OMON |
| 31 | `Diagnostic test` | mixed |
| 21 | `EXPANDED_ACCESS` | ClinicalTrials.gov |
| 8 | `Basic Science` | — |
| 7 | `Cause/Relative factors study` | — |
| 7 | `Relative factors research` | — |
| 6 | `Treatment study` | — |
| 6 | `Cause` | — |
| 3 | `BA/BE` | — |
| 2 | `Screening` | — |
| 2 | `Expanded Access` | — |
| 1 | `Epidemilogical research` (sic — registry typo) | — |
| 1 | `Prognosis study` | — |
| 1 | `Basic science` | — |
| 1 | `Other` | — |

Missing-value provenance: 13,079 of the 13,180 blanks are legacy rows with
`source_register IS NULL` (the same legacy population as the old sponsor/country gaps);
every ClinicalTrials.gov, EU CTR, IRCT, ChiCTR, ANZCTR row has a value. **Out of scope:**
no refetch/backfill-from-registry command here — this plan only normalizes what exists.

## Canonical vocabulary (decided)

`TrialStudyType` (`models.TextChoices`, in `gregory/utils/trial_field_normalizers.py`
next to `TrialPhase`/`TrialRecruitmentStatus`/`TrialRegion`):

| Value | Label | Maps from |
|:------|:------|:----------|
| `interventional` | Interventional | `INTERVENTIONAL`, `Interventional`, `interventional`, `Intervention`, `Interventional study`, `Interventional clinical trial of medicinal product`, `Treatment study`, `BA/BE` |
| `observational` | Observational | `OBSERVATIONAL`, `Observational`, `observational`, `Observational study`, `Observational non invasive`, `Observational invasive`, `Cause`, `Cause/Relative factors study`, `Relative factors research`, `Epidemilogical research`, `Prognosis study`, `Screening`, `Diagnostic test` |
| `expanded_access` | Expanded access | `EXPANDED_ACCESS`, `Expanded Access` |
| `basic_science` | Basic science | `Basic Science`, `basic science` |
| `other` | Other | `Other`, plus any unmapped value (logged) |
| *(null)* | — | missing/blank raw value |

Decisions behind the non-obvious rows — implement as written, don't second-guess:

- **`Diagnostic test` (31) → `observational`.** ClinicalTrials.gov classifies diagnostic
  accuracy studies as observational unless they assign an intervention; the WHO registries
  that emit this string use it as a study *purpose*, not an assignment model. Grouping it
  with observational is the least-wrong bucket. (`Screening` follows the same logic.)
- **`BA/BE` (3) → `interventional`.** Bioavailability/bioequivalence studies dose subjects
  by protocol — interventional by definition.
- **`Basic Science` gets its own bucket, not `other`.** Only 9 rows, but they are
  meaningfully non-clinical; folding them into `other` (the "we don't know" bucket) would
  lose that. Same reasoning that gave `post_market` its own phase bucket.
- **`Observational invasive`/`non invasive` collapse to `observational`.** The
  invasiveness qualifier is NL-OMON-specific and orthogonal to study type; preserving it
  would fragment the vocabulary for one registry. The raw column keeps it.
- **Expanded access stays separate from both.** It is an access program, not a study —
  consistent with how `available`/`no_longer_available` are handled in
  `_RECRUITMENT_STATUS_EXACT_MATCHES`.

## Implementation (follow `docs/trials-field-normalization.md` extension recipe)

1. **Normalizer** — `gregory/utils/trial_field_normalizers.py`:
   - `TrialStudyType` TextChoices as above.
   - `_STUDY_TYPE_EXACT_MATCHES: dict[str, str]` keyed by the **whitespace-collapsed,
     casefolded** raw value (so `INTERVENTIONAL`/`Interventional`/`interventional` are one
     key — the table above lists spellings, the dict holds ~16 distinct casefolded keys).
   - `normalize_study_type(raw)` — mirror `normalize_recruitment_status` exactly: `None`
     for blank; collapse whitespace + casefold; exact-match lookup; **no generic token
     fallback** (vocabulary is small and closed — guessing on a new registry spelling is
     riskier than surfacing it); unmapped → `TrialStudyType.OTHER` via the shared
     `_resolve(...)` helper so it is logged and reviewable in the admin.
   - Register it: add `("study_type", "study_type_normalized", normalize_study_type)` to
     `NORMALIZED_TRIAL_FIELDS`.
2. **Model** — `gregory/models.py`: `study_type_normalized` next to `study_type`
   (`CharField(max_length=20, null=True, blank=True, choices=TrialStudyType.choices,
   db_index=True, editable=False)` + the help_text convention used by the siblings).
   **`Trials.save()` needs no changes** — it iterates the registry, including the
   `update_fields` shim.
3. **Migration**: `makemigrations gregory` — one additive nullable indexed column, instant.
   New migration only; never edit an applied one.
4. **Backfill**: none to write — `backfill_trial_normalized_fields` covers every registered
   field automatically (`--field study_type` to scope it). The admin "Recompute normalized
   fields" action likewise needs no change.
5. **API surface** — mirror `phase`/`phase_normalized` everywhere it appears:
   - `TrialFilter`: `study_type_normalized = filters.ChoiceFilter(choices=TrialStudyType.choices)`;
     keep the legacy `study_type` icontains filter, documented as legacy.
   - `TrialSerializer`: add `study_type_normalized` to `fields`.
   - CSV (`api/direct_streaming.py`) and XLSX (`export_trials_xlsx`) column lists.
   - `TrialViewSet` docstring: new param + legacy note; `docs/03-api-and-rss-feeds.md`.
   - Admin: `list_filter` + readonly/fieldset entry alongside the other normalized fields.
6. **Facet — YES, add `by_study_type`.** Append to `build_stats_payload` after
   `by_modality`, following the shipped idioms (`.order_by()` before GROUP BY,
   `Count("trial_id", distinct=True)`, every enum key present at 0, plus `no_study_type`
   for null). It is a single GROUP BY on an indexed column and completes the filter rail;
   `no_study_type` will be large (~13.2k global) for the legacy-row reason above — document
   that, same as `no_modality`/`no_sponsor`.

## Tests

Mirror `gregory/tests/test_trial_recruitment_status_normalization.py` (the closest sibling —
same "exact matches, no fallback" shape):

- Every raw value from the 27-row inventory maps to its expected canonical value, in its
  original casing (parametrized — copy the inventory into the test as the source of truth).
- `None`/empty/whitespace → `None`; unmapped value → `other` **and logged**.
- `Trials.save()` hook, including `update_fields=["study_type"]` pulling the derived field
  into the update.
- Backfill: `--field study_type` populates; a `--field phase` run leaves
  `study_type_normalized` untouched; idempotent; `--dry-run` writes nothing.
- API: `?study_type_normalized=interventional` filters; invalid choice → 400; serializer
  field present; facet shape (all keys at 0 when empty, `no_study_type` counts nulls,
  respects filters).

## Runbook

1. `migrate`
2. `backfill_trial_normalized_fields --field study_type --dry-run`, review the OTHER report,
   then run it.
3. Dev, then prod (same two commands).
4. Sanity: `/trials/stats/?subject_id=1` → `by_study_type.interventional` should land near
   the current raw-substring count (4,472 for MS) but strictly ≥ it, since `Intervention`
   (REBEC) and other stragglers now count.
5. Update `~/Desktop/trials-visualizations-data-audit.md`: mark study_type normalization
   done; note the new facet.

## Out of scope

- Refetching `study_type` for the 13,079 legacy blanks (a CTGov-refetch-style command —
  separate, and low value: the legacy rows are mostly already covered by other fields).
- Preserving the NL-OMON invasiveness qualifier as structured data.
- Eligibility (`inclusion_gender`, age) normalization — its own item.
- Any change to the shipped facets beyond appending `by_study_type`.

## Process checklist

- Branch off up-to-date `main` in `~/Labs/gregory`; never commit to `main` (check
  `git branch --show-current`).
- Full test suite before every commit; new migrations only.
- After review rounds: push fixes, resolve each addressed PR comment thread on GitHub.
