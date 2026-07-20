# Plan: `inclusion_gender` normalization

Executor plan (written for Sonnet); every design decision is made — follow the spec,
don't re-litigate. Evidence: `ELIGIBILITY-DATA-AUDIT.md` (repo root, 2026-07-20).

This is the **fifth** field through the established companion-column pattern — the
recipe in `docs/trials-field-normalization.md` ("Adding a new normalized field") applies
verbatim. This plan supplies the vocabulary, the complete mapping table, and the
decisions the recipe can't make.

**Scope: sex eligibility only.** Age (`inclusion_agemin`/`agemax`) is deliberately a
separate follow-up — it carries unit-conversion and no-limit-vs-unknown decisions that
deserve their own review. Do not touch the age fields here.

> **PREREQUISITE: `WHO-HTML-CLEANUP-PLAN.md` must land and run first.** HTML is stripped
> at ingest there, not here — so this normalizer does **not** strip tags. Keep the HTML
> keys in the mapping table anyway (see §"HTML keys" below); they cost nothing and cover
> re-imports.

## Why this one first

Unlike `phase` or `study_type` (which merely *miss* variants), the current sex filter
returns **confidently wrong** results. Measured:

```
?inclusion_gender=Female  ->  1,772 trials
   of which genuinely female-only:                 324
   both-sexes trials that merely contain "female": 1,454   (82% false positives)
```

It conflates *"female-only"* with *"includes females"*, and **neither question can be
asked today**. That's a correctness bug, not a missing feature.

## The data (complete — dev DB = prod, 2026-07-20)

16,168 of 29,798 trials populated (54%), **25 distinct raw values**. After stripping HTML
tags, collapsing whitespace, and casefolding, they reduce to **18 keys** — the full set,
with counts:

| Key (after strip+collapse+casefold) | Rows | → |
|:---|---:|:---|
| `all` | 12,662 | `all` |
| `both` | 1,416 | `all` |
| `female: yes male: yes` | 795 | `all` |
| `both males and females` | 355 | `all` |
| `female, male` | 237 | `all` |
| `male and female` | 53 | `all` |
| `male/female` | 4 | `all` |
| `female` | 310 | `female` |
| `females` | 6 | `female` |
| `f` | 4 | `female` |
| `female: yes male: no` | 4 | `female` |
| `male` | 195 | `male` |
| `males` | 4 | `male` |
| `female: no male: yes` | 6 | `male` |
| `-` | 96 | **null** |
| `not specified` | 17 | **null** |
| `--` | 2 | **null** |
| `female: no male: no` | 2 | **null** (contradictory — see below) |

Raw-value provenance worth knowing: `ALL` is ClinicalTrials.gov (12,393), `Both` is
ChiCTR/IRCT/ISRCTN, `Both males and females` is ANZCTR, `Female, Male` is EU CTIS, and
**all 753 HTML variants come from the EU Clinical Trials Register** (plus 3 from NL-OMON).

## Vocabulary (decided)

`TrialSexEligibility` (`models.TextChoices`, in `gregory/utils/trial_field_normalizers.py`
next to the other enums):

```python
class TrialSexEligibility(models.TextChoices):
	ALL = "all", "All sexes"
	FEMALE = "female", "Female only"
	MALE = "male", "Male only"
```

Three decisions behind that shape — implement as written:

1. **There is deliberately NO `other` member**, breaking symmetry with
   `TrialPhase`/`TrialRecruitmentStatus`. Reason: a value literally rendered as
   `sex = other` would be read by any clinician as *intersex / non-binary participants*,
   which is emphatically **not** what it would mean (it would mean "we couldn't parse
   the string"). A misleading label is worse than a null. Unmapped values therefore map
   to **null**, and are found via the review query below.
2. **Placeholders (`-`, `--`, `Not Specified`) → null.** They carry no eligibility
   signal; storing them as data was the original mistake.
3. **`female: no male: no` (2 rows) → null, and logged.** Logically no one can enrol —
   it's a registry data-entry artifact, not a fourth state.

**Terminology note:** the raw column is `inclusion_gender`, but what registries actually
record for eligibility is sex (CTGov's own field is `eligibilityModule.sex`). The derived
field keeps the `<raw>_normalized` naming convention that `NORMALIZED_TRIAL_FIELDS`
depends on — `inclusion_gender_normalized` — while the enum is named for what it holds.
Say this in the field's `help_text`.

## Implementation

Follow `docs/trials-field-normalization.md`'s extension recipe. Specifics:

### 1. Normalizer (`gregory/utils/trial_field_normalizers.py`)

```python
def normalize_inclusion_gender(raw: str | None) -> str | None:
	"""Map a raw Trials.inclusion_gender value to a canonical TrialSexEligibility value.

	Returns None for missing/blank input, for explicit placeholders ("-", "Not
	Specified"), and for anything unrecognised (logged) — there is deliberately no
	"other" bucket, see INCLUSION-GENDER-NORMALIZATION-PLAN.md.

	The EU Clinical Trials Register stores this field as an HTML fragment
	("<br>Female: yes<br>Male: yes<br>"), so tags are stripped before matching.
	"""
```

Steps, in order: `None`/blank → `None`; collapse whitespace; strip; casefold;
exact-match against `_INCLUSION_GENDER_EXACT_MATCHES` (the 18 keys above, placeholders
mapped to `None`); unmapped → `None` **and log** (reuse the module's existing
`_resolve`/logging helper, passing a `field_label`, so unmapped values surface exactly
like they do for the other fields).

**No HTML stripping here** — `WHO-HTML-CLEANUP-PLAN.md` removes markup at ingest, which
is the right layer (one choke point in `importWHOXML.get_text`, and it fixes four other
columns at the same time). Duplicating a stripper in the normalizer would hide whether
the ingest fix is actually working.

**HTML keys** (`"<br>female: yes<br>male: yes<br>"` etc.): keep them in the exact-match
table regardless. After the cleanup they should never be hit, but they are free, they
make the table a complete record of what the registries have historically sent, and they
keep re-imports of stale cached XML safe. Note in a comment that they are expected-dead
post-cleanup.

**No generic token fallback** — same reasoning as `normalize_recruitment_status`: the
vocabulary is small and closed; guessing at an unseen spelling is riskier than surfacing
it. In particular, never substring-match `"female"`, since `"Female, Male"` contains it —
that is precisely the bug being fixed.

Register it: add `("inclusion_gender", "inclusion_gender_normalized",
normalize_inclusion_gender)` to `NORMALIZED_TRIAL_FIELDS`.

### 2. Model (`gregory/models.py`)

`inclusion_gender_normalized` next to `inclusion_gender`: `CharField(max_length=10,
null=True, blank=True, choices=TrialSexEligibility.choices, db_index=True,
editable=False)` + help_text noting it's sex-based eligibility derived from the raw
column. **`Trials.save()` needs no change** — it iterates the registry.

### 3. Migration + backfill

`makemigrations gregory` — one additive nullable indexed column, instant. **No new
backfill command**: `backfill_trial_normalized_fields --field inclusion_gender` is free
(the runner iterates `NORMALIZED_TRIAL_FIELDS`). The admin "Recompute normalized fields"
action likewise needs no change.

### 4. API surface — mirror `phase`/`phase_normalized` everywhere

- `TrialFilter`: `inclusion_gender_normalized = filters.ChoiceFilter(choices=TrialSexEligibility.choices)`.
- `TrialSerializer`: add the field.
- CSV (`api/direct_streaming.py`) and XLSX (`export_trials_xlsx`) column lists.
- Admin: `list_filter` + readonly/fieldset entry alongside the other normalized fields.
- **Remove the legacy `inclusion_gender` (icontains) filter entirely** (approved
  2026-07-20). It is not merely weak like `phase`/`study_type`/`countries` — it returns
  **confidently wrong** answers (`?inclusion_gender=Female` → 1,772 rows, only 324 of
  which are female-only), so leaving it available under a "legacy" label just preserves
  a trap. Delete the filter from `TrialFilter` and its `Meta.fields` entry, and remove
  it from the viewset docstring and `docs/03-api-and-rss-feeds.md`.
  - **Breaking-change handling:** a request with an unknown query param is *ignored* by
    django-filter, so an old client sending `?inclusion_gender=Female` will silently get
    **unfiltered** results rather than an error. Call this out explicitly in the PR
    description and in the API docs changelog entry, and grep the repo for internal
    callers (RSS views, management commands, the mockups' data snapshots) before
    deleting. If any internal caller exists, update it in the same PR.
  - Keep the **raw `inclusion_gender` field on the serializer** — removing the filter
    does not mean hiding the source-of-record value.

### 5. Facet — add `by_sex` to `/trials/stats/`

Closes audit item **A3b** (filter counts for eligibility). Append after the existing
facets in `build_stats_payload`, following the shipped idioms exactly (`.order_by()`
before GROUP BY, `Count("trial_id", distinct=True)`, every enum key present at 0):

```python
payload["by_sex"] = {v: counts.get(v, 0) for v in TrialSexEligibility.values}
payload["by_sex"]["no_sex_data"] = counts.get(None, 0)
```

One GROUP BY on an indexed column. **Honest denominator, document it:** `no_sex_data`
will be ~46% of trials globally (the legacy `source_register IS NULL` population that
also lacks sponsors and countries) — same caveat already documented for `no_modality`
and `no_sponsor`.

## Tests

Mirror `gregory/tests/test_trial_recruitment_status_normalization.py` (closest sibling —
exact matches, no fallback):

- **Parametrized over all 25 raw values** from the audit table, in their original casing
  and spacing, each asserting the expected canonical value. Copy the table into the test
  as the source of truth.
- HTML variants specifically: both `<br>Female: yes<br>Male: yes<br>` and the
  leading-space variant `<br> Female: yes<br> Male: yes<br>` → `all`;
  `<br>Female: yes<br>Male: no<br>` → `female`. (Expected-dead after the ingest cleanup,
  but asserted so a stale re-import can't regress.)
- **The removed legacy filter**: `?inclusion_gender=Female` no longer filters — assert
  it returns the unfiltered count, so the breaking change is deliberate and visible in
  the test suite rather than discovered in production.
- **The regression guard**: `"Female, Male"` → `all`, **not** `female` (this is the bug).
- Placeholders (`-`, `--`, `Not Specified`) and `female: no male: no` → `None`;
  `None`/empty/whitespace → `None`; an unseen spelling → `None` **and logged**.
- `Trials.save()` hook incl. `update_fields=["inclusion_gender"]` pulling the derived
  field into the update.
- Backfill: `--field inclusion_gender` populates; a `--field phase` run leaves it
  untouched; idempotent; `--dry-run` writes nothing.
- API: `?inclusion_gender_normalized=female` returns only female-only trials (fixture
  with one `Female` and one `Female, Male` — assert the latter is excluded); invalid
  choice → 400; serializer field present; `by_sex` facet shape (all keys at 0 when
  empty, `no_sex_data` counts nulls, respects filters).

## Runbook

1. `migrate`
2. `backfill_trial_normalized_fields --field inclusion_gender --dry-run`, review the
   unmapped report, then run for real.
3. Dev, then prod.
4. Sanity on prod: `?inclusion_gender_normalized=female` should return roughly **324**
   globally (vs 1,772 from the legacy substring filter) — that gap *is* the bug being
   fixed. `by_sex.all` should be ≈ 14,700.

## Out of scope

- Age normalization (`inclusion_agemin`/`agemax`) — separate follow-up per the audit.
- HTML stripping — done at ingest by `WHO-HTML-CLEANUP-PLAN.md` (the prerequisite).
- Any change to shipped facets beyond appending `by_sex`.

## Process checklist

- Branch off up-to-date `main` in `~/Labs/gregory`; never commit to `main` (verify with
  `git branch --show-current`).
- Full test suite before every commit; new migrations only.
- After review rounds: push fixes, resolve each addressed PR comment thread on GitHub.
- After merge + runbook: update `~/Desktop/trials-visualizations-data-audit.md` — A3b
  moves from Partial toward Delivered for the sex dimension (age still open).
