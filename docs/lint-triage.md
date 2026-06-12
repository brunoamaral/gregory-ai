# Lint triage — Ruff gate worklist

The Ruff gate (see [`ruff.toml`](../ruff.toml) and the `lint` job in
[`.github/workflows/tests.yaml`](../.github/workflows/tests.yaml)) enforces, on
every push / PR:

| Rule | Catches |
| --- | --- |
| `E722` / `S110` / `S112` | bare `except:` and `try/except: pass` — the empty catch block |
| `F401` | unused imports (auto-removed; re-exports in `__init__.py` ignored) |
| `S113` | `requests` calls without a timeout — can hang the pipeline |
| `RUF100` | unused `# noqa` — flags a directive the moment its issue is fixed, so this worklist stays honest |

Pre-existing violations are grandfathered with inline `# noqa` so the gate is
green on arrival and **new code is enforced immediately**. When a site is fixed,
delete its `# noqa` and `RUF100` keeps it clean.

How to reproduce locally (same version as CI):

```bash
uvx ruff@0.15.17 check django/
```

> **`F401` / `S113` already cleared.** Enabling them removed 230 unused imports
> and added `timeout=30` to 8 `requests` calls. Seven `# noqa: F401` remain by
> design — ML **availability-probe** imports, where the import itself is the test
> (`try: import X` to see whether it succeeds). Removing these silently breaks the
> check, so they must stay:
> [`predict_articles.py:144`](../django/gregory/management/commands/predict_articles.py),
> [`bert_wrapper.py:23`](../django/gregory/ml/bert_wrapper.py),
> [`tests/test_bert_wrapper.py:17`](../django/gregory/tests/test_bert_wrapper.py),
> and the four trainer probes in
> [`train_models.py:_check_ml_imports`](../django/gregory/management/commands/train_models.py).
>
> ⚠️ Ruff's F401 autofix **does not** spare these when the guard is
> `except Exception` (only `except ImportError`). It wrongly stripped the four in
> `_check_ml_imports`; they were restored with `# noqa`. Re-check this pattern if
> you ever re-run `--fix`.

---

## Empty-catch worklist (E722 / S110)

The remaining 19 grandfathered sites, grouped by the action each needs.

## Group A — Legitimately suppress (keep the `# noqa`, it's correct)

The catch-all is intentional and the silent pass is the right behaviour. Action:
none required — optionally tighten the `# noqa` to carry the reason inline.

| Site | Why it's fine |
| --- | --- |
| [`gregory/signals.py:31`](../django/gregory/signals.py) | History-stamping signal; a failure here must never break the underlying model `save()`. Already comments the intent. |
| [`templates/emails/components/content_organizer.py:408,416`](../django/templates/emails/components/content_organizer.py) | `prefetch_related` / `select_related` on a possibly-sliced queryset; failing and continuing with the original queryset is the designed fallback. |

## Group B — Narrow the exception (keep silent; missing data is expected)

These extract *optional* data. Swallowing is acceptable, but the bare `except:`
also hides real bugs (e.g. a typo'd attribute). Replace with the specific
exception; no logging needed.

| Site | Currently | Suggested |
| --- | --- | --- |
| [`gregory/classes.py:82,87,123,127,131,135,140,145`](../django/gregory/classes.py) | bare `except:` around CrossRef dict/list reads (link, title, date parts, abstract, authors) | `except (KeyError, IndexError, TypeError):` |
| [`api/direct_streaming.py:223`](../django/api/direct_streaming.py) | bare `except:` falling back to `str(value)` when `json.dumps` fails | `except (TypeError, ValueError):` |
| [`gregory/management/commands/feedreader_articles.py:207,334`](../django/gregory/management/commands/feedreader_articles.py) | `except Exception: pass` around DOI parsing from a feed link | `except (IndexError, ValueError):` (optionally `logger.debug`) |
| [`gregory/ml/gpu_config.py:85`](../django/gregory/ml/gpu_config.py) | `except Exception: pass` when disabling the GPU | keep `except Exception:` but add `logger.debug("GPU disable failed: %s", e)` |

## Group C — Narrow **and log** (these can mask real bugs — highest priority)

A failure here is swallowed as a "normal" outcome, so a genuine bug looks like an
empty result or a no-op. Narrow the exception and log the rest.

| Site | Risk | Suggested |
| --- | --- | --- |
| ✅ [`api/views.py:1780`](../django/api/views.py) | Any exception in search filtering becomes "0 results" — a bug silently looks like an empty search. | **Done** — narrowed to `(AttributeError, TypeError, ValueError)` + `logger.warning`, rest propagates. `AuthorSearchView` aligned too. |
| ✅ [`gregory/admin.py:75,143`](../django/gregory/admin.py) | Errors in admin org-scoping / choice-filtering were swallowed; could hide an org-visibility bug. | **Done** — `field_choices` narrowed to `(ObjectDoesNotExist, AttributeError, ValueError, TypeError)` + log (fail-closed); `get_queryset` narrowed to `FieldDoesNotExist`, so a filter error now propagates instead of silently returning the **unfiltered** queryset. |
| [`indexers/sage.py:23`](../django/indexers/sage.py) | bare `except:` on `Articles.objects.create()` treats *every* error as a uniqueness collision; `print()` instead of logging. **Also verify this module is still used** — it reads a top-level `input` and looks like a standalone script. | If kept: `except IntegrityError:` + `logger.warning(...)`. If dead: delete. |

---

## Phase 2 (not yet enforced)

Turn these on in `ruff.toml` once the worklist above is clear. Counts are current
(`uvx ruff check django/ --statistics` for the live numbers):

| Rule | Count | Notes |
| --- | --- | --- |
| `BLE001` blind `except Exception:` | 82 | Mostly *logged* handlers — legitimate. Opinionated; review before enforcing. |
| `T201` `print()` | 154 | Use logging instead — thematically the whole point, but a real migration. Grandfather and burn down. |
| `F811` redefined-while-unused | 8 | Possible real duplicate-definition bugs — read each before suppressing. |
| `F841` unused-variable | 9 | "Computed then ignored" — a hardcoded-result tell. |
| `W605` invalid escape sequence | 1+ | e.g. `ml_train.py` regex strings that should be raw strings. |
