# Editor attribution and manual trial links

> This is a historical implementation note. For current behaviour, see [Edit history](../02.1-database-tables-and-fields.md#edit-history) and [ArticleTrialReference](../02.1-database-tables-and-fields.md#articletrialreference).

**Context**: Edits made by named editors need to be traceable to a person, and links an editor adds or removes between an article and a trial must survive `detect_trial_references`. This is the groundwork; no endpoint writes these yet.

---

## History rows name a person

`ArticleSiteContent`, `ArticleSubjectRelevance` and `ArticleTrialReference` keep a history table. Each history row now records `editor_user`, `editor_label` (name and email at save time) and `via` (`mcp`, `api_key`, `admin`). Changes made outside a request, such as the pipeline, leave all three blank. `ArticleSubjectRelevance` and `ArticleTrialReference` had no history before, so their history starts at the upgrade.

## Trial links

`ArticleTrialReference` gains three columns:

| Column | Meaning |
|:-------|:--------|
| `source` | `auto` (default) or `manual` |
| `created_by` | The editor who added a manual link |
| `suppressed` | An auto-detected link an editor removed. Hidden from reads, kept so detection doesn't recreate it |

## Changes to `detect_trial_references`

| Before | After |
|:-------|:------|
| `--reset` deleted every reference | `--reset` deletes only `source="auto"` rows that aren't suppressed |
| A trial matched by a second identifier type got another row | A pair an editor unlinked, or linked by hand, is skipped under every identifier |

## Changes to the API

A suppressed link no longer appears in an article's `clinical_trials`, a trial's `articles`, the `has_clinical_trials` filter, or the trials XLSX export. Nothing else in the API changes.

## Migration guide

Run `python manage.py migrate`. Existing links become `source="auto"`, not suppressed, so reads are unchanged. No action for clients.
