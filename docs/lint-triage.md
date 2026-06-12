# Lint triage — empty-catch gate (E722 / S110 / S112)

The Ruff gate (see [`ruff.toml`](../ruff.toml) and the `lint` job in
[`.github/workflows/tests.yaml`](../.github/workflows/tests.yaml)) fails CI on
**bare `except:`** and **`try/except: pass`** — the "empty catch block" that
swallows failures silently.

19 pre-existing sites were grandfathered with inline `# noqa` so the gate is
green on arrival and **new code is enforced immediately**. This file is the
worklist for retiring those `# noqa`s. When a site is fixed, delete its `# noqa`
and the gate keeps it clean.

How to reproduce locally (same version as CI):

```bash
uvx ruff@0.15.17 check django/
```

---

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
| [`api/views.py:1780`](../django/api/views.py) | Any exception in search filtering becomes "0 results" — a bug silently looks like an empty search. | Catch the expected query/value errors, `logger.warning(...)` anything else (or let it 500). |
| [`gregory/admin.py:76,144`](../django/gregory/admin.py) | Errors in admin org-scoping / choice-filtering are swallowed; could hide an org-visibility bug. | Narrow to the expected lookup error, log the rest. |
| [`indexers/sage.py:23`](../django/indexers/sage.py) | bare `except:` on `Articles.objects.create()` treats *every* error as a uniqueness collision; `print()` instead of logging. **Also verify this module is still used** — it reads a top-level `input` and looks like a standalone script. | If kept: `except IntegrityError:` + `logger.warning(...)`. If dead: delete. |

---

## Phase 2 (not yet enforced)

Turn these on in `ruff.toml` once the worklist above is clear. Counts are current
(`uvx ruff check django/ --statistics` for the live numbers):

| Rule | Count | Notes |
| --- | --- | --- |
| `BLE001` blind `except Exception:` | 82 | Mostly *logged* handlers — legitimate. Opinionated; review before enforcing. |
| `F401` unused-import | 234 | Run `ruff check django/ --fix` once (auto-removes), let the test suite vet it, then enforce. |
| `F811` redefined-while-unused | 8 | Possible real duplicate-definition bugs — read each before suppressing. |
| `F841` unused-variable | 9 | "Computed then ignored" — a hardcoded-result tell. |
