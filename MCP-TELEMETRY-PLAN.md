# MCP server telemetry — plan

Status: Phases 1 and 2 implemented on `fix/mcp-cache-tool-catalog` (not yet merged/deployed — see
the deployment boundary below).

Phase 1: `gregory_mcp/telemetry.py` (`TelemetryMiddleware`), instrumented `client.py`/`cache.py`,
extended `logging_config.py`'s field allowlist. Verified against the real server stack, not just
unit tests — driving actual `tools/call` requests through `build_server()` surfaced that our tools
return plain `dict` with no declared output schema, so `CallToolResult.structured_content` is unset
on the wire; the shape fields (`result_count`/`total_count`/`has_next`) fall back to parsing the
same payload out of `content[0].text` instead.

Phase 2: `gregory_mcp/query_shape.py` (tokenizer, guardrails, taxonomy index/match), wired into
`TelemetryMiddleware` for `search_articles`/`search_trials`/`list_categories` only, best-effort
(a taxonomy-fetch failure never breaks the request it's describing). Started against Phase 1 with
zero real traffic — deliberately, at the user's call, ahead of the plan's own "only after Phase 1
is deployed" gate; thresholds (stopword list, length buckets, the 400-char/7-digit guardrails) are
untuned defaults and worth revisiting once real query volume exists. Also surfaced and fixed a
second real bug the same way: `telemetry.py` importing `query_shape` at module scope created a
cycle (`telemetry -> query_shape -> cache -> client -> telemetry`, since client.py/cache.py already
import telemetry.py's `record_*` functions) — fixed with a lazy import inside
`_annotate_query_shape`, confirmed via a fresh-process import of `gregory_mcp.server`.

`tests/test_telemetry.py` (19 tests) + `tests/test_query_shape.py` (14 tests).

Phase 3: `gregory_mcp/zero_result.py` (`guidance_for`) — search_articles/search_trials attach a
`guidance` key (`applied_filters` + ranked suggestions) to a zero-hit response instead of the bare
`{"count": 0, "articles": []}` dead end. Rule-based, not ML: relevance/threshold, date range,
taxonomy ID, registry ID, boolean search, in that priority order, falling back to a generic
suggestion. Filter *names* only, same boundary telemetry.py holds for `params_used` — verified no
filter value ever appears in the guidance output. Confirmed end-to-end that it composes cleanly
with Phase 1/2 telemetry (the new `guidance` dict doesn't perturb `_result_shape`'s list-scan for
`result_count`, since it isn't a list). Docstrings updated for both tools.

`tests/test_zero_result.py` (11 tests) + new cases in `test_tools_articles.py`/`test_tools_trials.py`.

Phase 4: optional `intent: str | None` param on search_articles/search_trials only (not
search_authors — the carve-out holds, asserted in tests), disclosed verbatim in the tool's `Args:`
docstring. `gregory_mcp/intent.py` (`record`, `scan_for_pii`) logs full text to a genuinely separate
OS stream — stderr, not stdout, `propagate=False`, its own `IntentJsonFormatter` with a 3-field
allowlist (`tool`/`intent`/`pii_flags`) that structurally cannot emit anything from the telemetry
stream's field set. Heuristic PII scan flags without blocking: email, long digit run, first-person
medical, age specificity, and specificity co-occurrence (age/geography token + a taxonomy category
match, reusing `query_shape.analyze`). Verified end-to-end with a real request carrying an email +
age-specific intent: stdout showed `intent` only as a name in `params_used`, never its value; stderr
carried the full text with `pii_flags: ["email", "age_specificity"]`. `specificity_co_occurrence`
correctly didn't fire there because the email had already tripped `query_shape`'s own guardrail —
not a bug, the `email` flag already dominates that case.

90-day retention/hard-delete and actual downstream stream routing are deploy-side (Phase 6), not
implemented here — same boundary as the rest of the plan's retention story.

`tests/test_intent.py` (23 tests) + wiring cases in `test_tools_articles.py`/`test_tools_trials.py`
+ a `test_schema_contract.py` fix (`intent` added to the non-filter-args allowlist for both search
tools, since it's never forwarded to Django).

Full suite: 156 passed. Phases 5-6 not started — Phase 5 (the scheduled review) can't run for real
until there's deployed traffic to review.

Goal: learn enough about how LLM clients actually use `mcp-server/` to improve it — which tools
earn their place, which parameters are dead weight in a 17 KB schema payload, where searches come
back empty, which Gregory API endpoints are the latency floor, and **what people are asking for
that we do not yet provide** — without building a record of what any individual person researched.

---

## Summary

Three layers of instrumentation exist. Two are dark:

| Layer | State |
|:---|:---|
| nginx `mcp-access.log` (`mcp_combined`) | Working. Only place that sees 429s. Tool name comes from the client-controlled `Mcp-Name` header. |
| `JsonFormatter` (`gregory_mcp/logging_config.py`) | Built with `tool`/`duration_ms`/`status_code` fields. **Nothing emits them.** Three log lines exist in the whole package: startup, transport error, retry. |
| `OpenTelemetryMiddleware` | Ships **on by default** in `mcp==2.0.0` (`lowlevel/server.py:439`), already emits per-method spans with `gen_ai.tool.name`. `opentelemetry-api` is already in the image. But no SDK and no exporter are installed, so **every span is a no-op**. |

No tool call has ever been logged. That is the gap this plan closes.

The hook point exists: `MCPServer(..., middleware=[...])` takes a `ServerMiddleware`, and each
request's `ctx` carries `method`, `params` (`name` + `arguments` for `tools/call`), and
`meta[io.modelcontextprotocol/clientInfo]` — client name and version, on every request under the
stateless 2026-07-28 core.

---

## Deployment boundary

**Nothing in this plan is applied to House directly.** House is production — the live GregoryAI
instance — and every phase here delivers code, example config, and docs into the repo. Copying
config across, reloading nginx, and deploying containers are Bruno's steps, taken deliberately.

This matches how `mcp-server/` already shipped: `nginx-example-configuration/nginx.conf` is
explicitly "an example, not the deployed file" (`docs/07-mcp-server.md:169`), and the CI deploy
pulls images without touching the checkout.

Practical consequence for every phase below: a change is not live when it lands on a branch, so
"deployed" and "merged" are separate states in this document, and any phase that depends on real
traffic depends on Bruno having deployed the phase before it.

---

## Decisions taken

**2026-08-09 — `intent` is logged as full text, not classified.** An earlier draft proposed mapping
intent to a fixed taxonomy of question types and logging only the class. Rejected: the class list
cannot be written before the data has been seen, and at this server's volume a rising `other`
bucket is an alarm with no diagnosis. Full text from day one, with bounded retention and a
scheduled review that can act retroactively (Phase 4 / Phase 5). The assumption under test is that
model-authored intent strings do not contain personally identifying information; the review exists
to falsify it with evidence rather than confirm it by impression.

---

## The anonymity design

Two fields carry user-derived text, and they are treated differently on purpose.

### `search` — bagged

The thing that makes a search query identifying is rarely the individual terms. `encephalitis` on
encefalites.pt identifies nobody. What identifies is **co-occurrence** (`encephalitis` ∧
`rituximab` ∧ `paediatric`) and **linkage** (that tuple tied to the same caller as yesterday's
query). Both are destroyed at write time, cheaply:

**1. Destroy co-occurrence.** Never log the query string or the term tuple. Split into terms and
emit each as its own record with no key joining them. Both terms get counted; that one person
asked for both is never written down.

**2. Destroy linkage.** No IP, no session id, no stable client hash, timestamps truncated to the
hour. `stateless_http=True` means there is no session id to leak in the first place. Without a
correlation key the output is a term-frequency table, not a set of profiles.

**3. Prefer a closed vocabulary.** Gregory already has one: `category_terms` on `/categories/`,
which this server fetches and caches. Match terms against it and log the matched category slugs
plus a count of terms matching nothing. The output alphabet is the site's own public category
list, non-personal by construction.

**4. k-anonymity on the remainder** (Phase 5, may never ship).

### `intent` — full text, bounded window

Bagging destroys exactly what makes intent useful: the shape of the ask is the signal. So `intent`
is stored whole. The protection is **exposure duration and review**, not content reduction:

- rolling 90-day window, hard delete; derived conclusions persist, raw rows do not
- automated PII flagging on write (flag, do not block) into a separate review queue
- a scheduled review with a defined pass/fail that can retire the field (Phase 5)

**This asymmetry is deliberate, not an inconsistency.** `search` gets bagged because bagging is
free there — the vocabulary signal survives it. `intent` is not bagged because bagging would leave
nothing to analyse. Where content reduction costs nothing it is applied; where it would destroy
the signal, exposure is bounded instead.

### Not logged at any phase

`search_authors(search=…)`, `full_name`, `given_name`, `family_name`, `orcid`, and `doi` are
personal data about **third parties** — researchers who never interacted with us. "Someone looked
up Dr. X" is a different record from "Dr. X's publication list is public." Shape only: term count,
whether it looked like an ORCID. **`intent` is not collected on `search_authors` either**, since an
intent phrased around a named person reintroduces exactly what the carve-out removes.

### What does not work

**Salted hashing.** The biomedical query space is small and enumerable; a dictionary attack over a
MeSH vocabulary recovers plaintext in seconds. Not a protection.

**Differential privacy.** Right at Google scale. At this volume Laplace noise swamps the signal.

**Scrubbing after the fact.** Any design where the raw value lands somewhere first and is cleaned
later has already lost — it is on disk, in a rotation, in a backup.

### Separation is protection

nginx keeps IPs. The app log keeps terms and intent. They must never be joinable: different files,
different retention, no shared correlation key, and no request id appearing in both. Keeping them
apart is free and does more than any amount of filtering.

Note for the docs: pseudonymous data is still personal data under GDPR; only genuinely anonymous
data falls outside it. Phase 6 adds the disclosure. That is a design note, not legal advice —
worth a second opinion before Phase 4 ships.

---

## Phase 0 — nginx log format — **done**

No app change, no privacy surface.

`$http_mcp_method` added to the `mcp_combined` format in
`nginx-example-configuration/nginx.conf`. This separates `tools/call` from `tools/list` at the
edge, which directly validates the cache-hint work on `fix/mcp-cache-tool-catalog` — if
`tools/list` per client per hour stays high, clients are ignoring `ttlMs` and that branch did not
achieve what it set out to.

Caveat for any analysis: both `Mcp-Name` and `Mcp-Method` are client-controlled. Treat an empty
value as "unknown", not as "no tool" / "no method".

**Not yet live.** Per the deployment boundary above, the example config is the deliverable; copying
it to House and reloading nginx is Bruno's step, and until then this changes nothing about what is
logged.

---

## Phase 1 — `TelemetryMiddleware`, no query content

The bulk of the value, none of the privacy argument. Ship first and independently.

**New file `gregory_mcp/telemetry.py`:**

- `TelemetryMiddleware(ServerMiddleware)`, passed via `build_server()`'s `MCPServer(middleware=[…])`.
  Runs inside the SDK's built-in OTel and request-state middleware, so it sees the sealed wire form.
- Times `call_next`. Emits exactly one JSON line per request through the existing `JsonFormatter`
  — the `tool` / `duration_ms` / `status_code` fields it already declares and never populates.
- A `ContextVar` holding an upstream accumulator, set in the middleware and incremented in
  `GregoryClient.get()`, gives `upstream_ms` and `upstream_calls` without threading state through
  every tool signature.

**Fields per event:**

| Field | Why |
|:---|:---|
| `method`, `tool` | From server-side dispatch, **not** the `Mcp-Name` header |
| `duration_ms`, `upstream_ms`, `upstream_calls` | Splits our overhead from Django's; doubles as an API profiler |
| `outcome`, `error_kind` | ok / tool_error / validation_error / upstream_error; `GregoryAPIError` status, `ValueError`, `GregoryPaginationTruncatedError` |
| `result_count`, `total_count`, `has_next` | Selectivity ("showed 10 of 4,200") and truncation pressure |
| `params_used` | Sorted list of parameter **names** that were non-None. Never values. |
| `page`, `page_size` | Is `DEFAULT_PAGE_SIZE = 10` too small? |
| `subject_id`, `team_id`, `category_slug`, `category_modality` | Public taxonomy IDs, low cardinality, safe |
| `client_name`, `client_version`, `protocol_version` | From `_meta`; tells us which clients honour cache hints |
| `cache` | hit / miss / single-flight-wait, for the catalog tools |

No IP. No session id. No free text of any kind at this phase.

**Answers on its own:** zero-result rate per tool (highest-value single metric, broken down by
`params_used`); parameter coverage — which of `search_articles`' 20 parameters are never used and
can be cut from the schema; whether the 3 prompts and 2 resources are ever exercised; `tools/list`
refetch rate; per-endpoint upstream latency; catalog cache hit rate; client mix.

**Tests:** one record per request; `params_used` never contains a value; `search` / `full_name` /
`orcid` / `doi` never appear in any emitted field (assert explicitly — it is the regression that
matters); upstream accounting survives retries; a raising tool still emits an event.

**Analysis:** `docker logs gregory-mcp | jq`, same ad-hoc style `docs/07-mcp-server.md` already
uses for `mcp-access.log`. At these volumes, no sampling and no aggregation infrastructure.

---

## Phase 2 — query shape and taxonomy match

Only after Phase 1 is deployed and the volume is known.

**Tokenizer** (`gregory_mcp/query_shape.py`): lowercase, strip boolean operators and punctuation,
split, drop stopwords and tokens under 3 characters.

**Taxonomy match:** build the allowlist from `category_terms` + `category_name` on `/categories/`,
reusing `get_all_pages_cached` — already held and refreshed every 10 minutes.

**Emitted, for `search_articles` / `search_trials` / `list_categories` only:**

- `matched_category_slugs` — closed vocabulary, public, non-personal by construction
- `unmatched_term_count` — integer only at this phase
- `term_count`, `has_boolean_ops`, `has_quoted_phrase`, `length_bucket`

**Guardrail:** drop the query from shape analysis entirely if it contains `@`, a digit run of 7+,
or exceeds a length cap. Cheap insurance against a pasted email or phone number.

Tells us "models search for covered categories and get zero results N% of the time" — directly
actionable against the corpus and the docstrings.

---

## Phase 3 — zero-result responses become useful

A product change that ships independently of everything else, and the vehicle Phase 4 rides on.

Today `search_articles` with no hits returns `{"count": 0, "articles": []}` — a dead end for the
model and a wasted signal for us. Replace with a structured response carrying: which filters were
applied, which are most likely over-constraining, and what to try instead (drop `relevant`, widen
the date range, call `list_subjects` first).

Worth doing on its own merits — it makes the failure case useful to the model — and it creates the
natural moment to ask what was actually being looked for.

---

## Phase 4 — `intent` capture, full text

Gated on Phase 1 volume data. If the server is seeing tens of calls a day, this reports nothing
that could not be learned by reading the log by hand.

### Why this field and not the user's prompt

There is no protocol channel for the user's prompt. The reserved `_meta` keys under 2026-07-28 are
exactly five — `protocolVersion`, `clientInfo`, `clientCapabilities`, `logLevel`, `serverInfo` —
and none carries conversation context. `sampling`'s `include_context` came closest and is
deprecated in the draft spec (`resolve.py:128`). So intent has to be *asked for*, not read.

**And the largest blind spot is structural:** if the model decides we cannot help and never calls
us, we see nothing. The gaps most worth finding — "user asked about survival curves, we have no
such data, so the model answered from its own knowledge" — never touch this server. No amount of
logging fixes that; it bounds what this phase can deliver.

### Mechanism

An optional `intent` parameter on `search_articles` / `search_trials` — model-authored, one short
phrase describing the information need. Established pattern; the `qmd` MCP server takes exactly
this on every search call. Works with every client, no capability negotiation.

Parameter description carries the disclosure, so it is visible in the tool schema to anyone who
inspects the server: *"One short phrase describing the information need. Recorded to identify gaps
in the corpus. Do not include personal or identifying details."*

**Note on the redaction argument:** the model paraphrasing the user is a *soft* control, weaker
than it sounds. A helpful model turns "my 7-year-old was just diagnosed with X" into
`intent="treatment options for paediatric X"` — it drops the first person and keeps the
identifying triple. The flagging below therefore looks for **specificity patterns**, not just
first-person phrasing, or it misses the case that matters most on encefalites.pt.

Not collected on `search_authors` (see carve-out above).

### Storage and flagging

- Written to its own stream, separate from the Phase 1 telemetry and from `mcp-access.log`. No
  shared correlation key.
- **Rolling 90-day retention, hard delete.** Derived conclusions persist as conclusions; raw rows
  do not.
- A heuristic scan on write **flags without blocking**, into a review queue:

| Pattern | Example |
|:---|:---|
| Email | `@` between word characters |
| Phone / long identifiers | digit runs of 7+ |
| First-person medical | `my/our son\|daughter\|child\|wife\|husband\|mother\|father`, `I was/am diagnosed\|treated` |
| Age specificity | `N-year-old`, `aged N` |
| Specificity co-occurrence | an age or geography token alongside a category term |

Flag rate is itself the metric — a near-zero rate is a measured answer to "does this contain PII",
not an impression.

---

## Phase 5 — the scheduled review

**Trigger:** 1,000 collected intents **or** 6 months from first meaningful traffic, whichever comes
first. The clock starts at real traffic, not at merge — a quiet first quarter would otherwise mean
reviewing an empty window and re-confirming nothing.

**Input:** every flagged record, plus a random sample of 100 unflagged ones.

**Outcome, decided against this test rather than by impression:**

| Verdict | Condition | Action |
|:---|:---|:---|
| **Pass** | Flag rate < 1% **and** no record in flagged-or-sampled set where an individual could plausibly be identified | Continue. Next review in 12 months. |
| **Fail** | Any confirmed identifiable record, **or** flag rate ≥ 1% | Stop collecting `intent` text. Delete the current window. Fall back to classification-only. |
| **Inconclusive** | Fewer than 1,000 intents | Extend; review again at the threshold. |

The 90-day window is what makes **Fail** actionable rather than merely regrettable — the exposure
drains rather than being permanent.

### Phase 5b — k-anonymised unmatched search terms

Separate, lower priority, **may never ship**. Unmatched terms from Phase 2, logged individually
with no key joining terms from the same query, timestamps truncated to the hour, 7-day buffer. A
rollup emits a term only when seen from **≥ k distinct request-hours** over 30 days; everything
below k is discarded. Purpose: surface vocabulary worth adding categories for, at the point where
enough distinct people use it that it is no longer identifying.

Honest caveat: at low traffic this suppresses nearly everything. Phase 1 volume data decides
whether it is worth building at all. `intent` (Phase 4) likely covers the same ground better.

---

## Phase 6 — retention, rotation, docs

- Rotation and explicit retention on all three streams: Phase 1 telemetry, `intent` (90 days), and
  the Phase 5b buffer (7 days). Aggregates and conclusions kept indefinitely.
- Confirm nginx and app logs stay unjoinable — no shared request id.
- New section in `docs/07-mcp-server.md`: what is collected, what is deliberately not, retention
  windows, the review schedule and its pass/fail. Per `CLAUDE.md`, docs land in the same PR.
- Revisit the nginx rate limits (30 r/m per client-tool, 120 r/m per client) against real data —
  `docs/07-mcp-server.md:139` already flags those numbers as guesses.

---

## Explicitly out of scope

**Writing telemetry to Postgres via the Django API.** The stateless, GET-only property is what this
server's design rests on, and `tests/test_server.py:80` asserts the client exposes no write verbs.
`APIAccessSchemeLog` exists on the Django side but reaching it means giving this server a write
path.

**Sampling the client's model to summarise intent.** Technically available. It spends the user's
tokens and their model's time on our analytics without their agreement, and the conversation-context
part is deprecated regardless.

**Elicitation as a default.** `ctx.elicit()` is supported and has the best consent story of any
option here — the person is asked directly and can decline. But it interrupts a conversation and
needs the client's elicitation capability. Reasonable later as an opt-in "help us improve" mode;
wrong as always-on.

**Standing up an OTel collector.** The spans are already emitted and already free — enabling them
needs only `opentelemetry-sdk` plus an exporter behind an env var. But that needs a backend
running somewhere, and House is already load-sensitive (see `HOUSE-LOAD-SPIKE-PLAN.md`). The
Phase 1 middleware is written so its attributes can be attached to the existing spans later without
rework; flipping the exporter on is then a dependency change, not a redesign.

---

## Decisions needed

1. **`intent` retention window.** 90 days is a starting suggestion, not a defended number. Shorter
   is safer, longer gives the review more to work with.
2. **Does Phase 5b ship at all?** Defer until Phase 1 reports two weeks of volume. At tens of
   requests a day, k-anonymity yields nothing and the risk buys no information.
3. **Second opinion on the GDPR framing** before Phase 4 ships, given the health-research context
   and the rare-disease instance.
