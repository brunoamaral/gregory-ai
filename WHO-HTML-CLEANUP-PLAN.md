# Plan: strip HTML at ingest for WHO ICTRP trial fields

Executor plan (written for Sonnet). Small, self-contained. **Prerequisite for
`INCLUSION-GENDER-NORMALIZATION-PLAN.md`** — do this one first, so the sex normalizer
never has to strip markup itself.

Evidence: dev DB (= prod) audit, 2026-07-20.

## The problem

`importWHOXML` stores WHO ICTRP field text verbatim. Several source registries embed
HTML in their XML, so raw markup lands in the database and renders as literal `<br>` /
`<b>` anywhere these fields are displayed.

Affected columns and row counts (tag-whitelist match, dev DB):

| Column | Rows with HTML |
|:---|---:|
| `inclusion_criteria` | 3,128 |
| `exclusion_criteria` | 2,944 |
| `condition` | 2,446 |
| `intervention` | 2,016 |
| `primary_outcome` | 1,456 |
| `secondary_outcome` | ~1,368 |
| `inclusion_gender` | 756 |
| `study_design` | ~237 |
| `title` / `scientific_title` | 11 each |
| `source_support` | 2 |

It is **not** one registry — six ICTRP source registers contribute
(`inclusion_criteria` breakdown: EU Clinical Trials Register 753, IRCT 516, ChiCTR 500,
NL-OMON 468, ANZCTR 255, ISRCTN 206). They all arrive through the same importer, so
there is exactly **one** place to fix.

Tags observed: `br` (dominant), `b`, `p`, `a`, `li`, `ul`, `tr`, `td`. HTML entities also
appear but are rare (`&amp;` 23, `&lt;`/`&gt;` 27, `&nbsp;` 15, numeric `&#…` 135).

## Two traps — read before writing code

**1. `summary` must NOT be cleaned.** 13,162 trials have HTML in `summary`, and it is
**intentional**: the CTIS feedreader composes a labeled block
(`<b>Trial number</b>: …<br/><b>Overall trial status</b>: …`) and the EUCTR RSS summary
is HTML including `<a href>` links. That field is rendered as HTML by consumers.
Stripping it would destroy the display summary. **Exclude `summary` from every part of
this change.**

**2. A naive `<[a-zA-Z/][^>]*>` regex corrupts real text.** Some registries use angle
brackets as quotation marks or placeholders — e.g. a Chinese trial's criteria reading
`…with reference to diagnostic criteria <the guide of diagnosis and treatment>…`, and
literal `\<TAB\>` markers. Measured naive-only (i.e. false-positive) matches: 35 in
`inclusion_criteria`, 4 in `primary_outcome`, 1 in `exclusion_criteria`. A naive strip
would silently delete those words from the criteria text.

**Therefore: parse HTML, don't regex it.** The repo already has the right tool —
`gregory.utils.text_utils.cleanHTML` (BeautifulSoup `.get_text()`), used elsewhere.
BeautifulSoup leaves non-tag angle-bracket text alone, which is exactly the behaviour
needed.

## Implementation

### 1. A shared cleaner (`gregory/utils/text_utils.py`)

`cleanHTML` alone is not sufficient: `get_text()` on `"A<br>B"` yields `"AB"` — words
run together. Add a sibling that is safe for these fields:

```python
def clean_field_html(value: str | None) -> str | None:
	"""Plain text from a registry field that may contain HTML.

	Block/line-break tags become whitespace before extraction so "A<br>B" yields
	"A B", not "AB". Entities are unescaped by the parser. Whitespace is collapsed
	and the result stripped; returns None for empty/blank input so callers keep the
	"absent field" semantics.

	Uses a real parser (not a regex): several ICTRP registries use angle brackets as
	quotation marks (e.g. "criteria <the guide of diagnosis and treatment>"), which a
	tag-shaped regex would delete. See WHO-HTML-CLEANUP-PLAN.md.
	"""
```

Steps: return `None` for falsy/blank; `BeautifulSoup(value, "html.parser")`; replace
`br`, `p`, `li`, `tr`, `div` (and their closers) with a space via
`soup.find_all(...)` + `replace_with(" ")` or by inserting separators; `get_text(" ")`;
`re.sub(r"\s+", " ", …).strip()`; return `None` if the result is empty.

Do **not** modify `cleanHTML` — other callers depend on its current behaviour.

### 2. Apply at ingest (`importWHOXML.py`)

`get_text` (~line 45) is the single choke point — every one of these fields is read
through it:

```python
def get_text(self, trial, tag_name):
	element = trial.find(tag_name)
	if element is not None and element.text is not None:
		return clean_field_html(element.text)   # was: " ".join(element.text.split()).strip()
	return None
```

`clean_field_html` already collapses whitespace, so it subsumes the existing behaviour.
This covers every field the importer reads — including ones not in the table above, which
is fine and desirable.

**Check the other importers before finishing**: grep `feedreader_trials.py` (EU CTIS RSS)
and `feedreader_trials_ctgov.py` for the same fields. CTIS's `EUTrialParser.parse_summary`
already regex-extracts values out of HTML, and `summary` is deliberately HTML — so the
expectation is *no change needed there*. Confirm and note it; don't refactor them.

### 3. One-time backfill: `clean_trial_html`

New management command, idempotent, `--dry-run`, modeled on
`backfill_clean_titles` (the article-title precedent).

- Columns to clean (**explicit list — `summary` is absent by design**):
  `title`, `scientific_title`, `inclusion_criteria`, `exclusion_criteria`, `condition`,
  `intervention`, `primary_outcome`, `secondary_outcome`, `study_design`,
  `inclusion_gender`, `source_support`.
- Selection: rows where any of those columns matches a **whitelisted-tag** regex
  (`</?(br|b|p|a|i|u|em|strong|li|ul|ol|div|span|table|tr|td|th|hr|sub|sup|font)( [^>]*)?/?>`,
  case-insensitive) — narrow selection avoids rewriting ~thousands of untouched rows.
- For each row, run `clean_field_html` per column; write only columns that actually
  changed.
- **Use `bulk_update` in batches (1000), never `trial.save()`** — `save()` fans out to
  `sync_trial_countries()` and every normalizer. **Exception:** `inclusion_gender` feeds
  `inclusion_gender_normalized` in the *next* plan; since that field doesn't exist yet
  when this runs, `bulk_update` is safe here. Add a comment saying so, and note that
  after the gender plan lands, re-running this command would need
  `backfill_trial_normalized_fields --field inclusion_gender` afterwards.
- `.only(...)` + `.iterator(chunk_size=2000)` to bound memory.
- Report per column: rows scanned, rows changed. `--dry-run` prints a few before/after
  samples and writes nothing.

## Tests

- `clean_field_html`: `"A<br>B"` → `"A B"` (**not** `"AB"`); nested/attributed tags
  (`<a href="…">x</a>`) → `"x"`; entities (`&amp;`, `&nbsp;`, `&#39;`) unescaped;
  whitespace collapsed; `None`/`""`/whitespace-only → `None`;
  **`"criteria <the guide of diagnosis and treatment> apply"` is preserved verbatim**
  (the false-positive guard — this is the test that justifies not using a regex);
  `"\<TAB\>"` preserved.
- The real fixtures: `"<br>Female: yes<br>Male: yes<br>"` → `"Female: yes Male: yes"`.
- `importWHOXML`: a trial whose XML contains `<br>` in criteria is stored clean;
  existing importer tests still pass.
- Command: cleans the listed columns; **leaves `summary` untouched** (explicit test with
  an HTML-bearing summary); idempotent (second run changes 0 rows); `--dry-run` writes
  nothing; uses `bulk_update` (assert no `Trials.save()` — query-count or patched save).

## Runbook

1. `clean_trial_html --dry-run` — review the before/after samples and per-column counts.
2. Run for real. Dev, then prod. No migration.
3. Spot-check: `SELECT count(*) FROM trials WHERE inclusion_criteria ~* '</?br…'` → 0,
   and `SELECT count(*) FROM trials WHERE summary ~ '<'` → still ~13k (proof `summary`
   was left alone).
4. Then start `INCLUSION-GENDER-NORMALIZATION-PLAN.md`, whose mapping table gets simpler
   (the HTML variants collapse into the plain ones once this has run — but keep them in
   the table anyway, see that plan's note).

## Out of scope

- Cleaning `summary` (intentional HTML).
- Article-side HTML (separate corpus, separate precedent in `backfill_clean_titles`).
- Rewriting CTIS/CTGov importers (they don't leak — verify, then leave alone).
- Any normalization work; this change only removes markup.

## Process checklist

- Branch off up-to-date `main` in `~/Labs/gregory`; never commit to `main`.
- Full test suite before committing.
- After review: push fixes, resolve each addressed PR comment thread on GitHub.
