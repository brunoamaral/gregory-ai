# Glossary

Key terms used across GregoryAI documentation.

---

**Article** — a published piece of content ingested by GregoryAI. The term covers science papers, news articles, and trial summaries. Internally they share the same `Articles` model with a `kind` field distinguishing them. See [02-sources-and-articles.md](02-sources-and-articles.md).

**Author** — a person associated with one or more articles. Authors are identified by ORCID when available. See [authors-api.md](authors-api.md) and [merge-authors-command.md](merge-authors-command.md).

**Category** — a keyword-based filter. Any article whose title contains at least one of the category's terms is tagged with that category. Categories work across subjects. See [02-sources-and-articles.md](02-sources-and-articles.md).

**Consensus type** — the rule controlling how many ML models must agree before an article is counted as ML-relevant. Options are `any` (1 of 3), `majority` (2 of 3), and `all` (3 of 3). Set per subject. See [ml-consensus.md](ml-consensus.md).

**Discovery date** — the date GregoryAI first added an article or trial to its database, distinct from the publication date.

**DOI** — Digital Object Identifier. Used by GregoryAI for de-duplication and for enriching article metadata from CrossRef.

**List** — a named email list that subscribers opt into. A subscriber can belong to multiple lists. Lists are linked to a team and optionally scoped to specific subjects. See [subscriptions.md](subscriptions.md).

**ML threshold** — a minimum probability score (0.0–1.0) that a single model's prediction must meet before that prediction counts towards the consensus. Default is 0.8. See [ml-consensus.md](ml-consensus.md).

**ORCID** — Open Researcher and Contributor ID. A persistent digital identifier for researchers. GregoryAI uses ORCID URLs in the form `https://orcid.org/0000-0000-0000-0000`.

**Organisation** — the top-level grouping in GregoryAI's multi-tenant model. Owns teams, credentials, and sites. Provided by `django-organizations`. See [06-organisations-teams-and-sites.md](06-organisations-teams-and-sites.md).

**OrganisationSite** — a link between an Organisation and a Django Site. One can be marked as `is_default`. See [06-organisations-teams-and-sites.md](06-organisations-teams-and-sites.md).

**Pipeline** — the `manage.py pipeline` command that fetches new articles and trials from all configured sources and applies ML predictions. Run every 12 hours in production.

**Pseudo-labelling** — a semi-supervised technique used during training where the model's own high-confidence predictions on unlabelled data are used as additional training labels. Enabled with `--pseudo-label` on `train_models`.

**Relevant** — an article is "relevant" if it has been manually marked so by an admin reviewer, or if it meets the ML consensus criteria for at least one of its subjects.

**Source** — a configured feed or endpoint from which GregoryAI pulls articles. Sources use an RSS method by default. Each source is linked to a subject and specifies what kind of content it provides (`science paper`, `news`, or `trials`). See [02-sources-and-articles.md](02-sources-and-articles.md).

**Subject** — a named research area (e.g., "Multiple Sclerosis"). Groups sources and their articles. ML models are trained and consensus rules are configured per subject. See [02-sources-and-articles.md](02-sources-and-articles.md).

**Subscriber** — a person who receives email digests. One row per unique email address. Subscribers can opt into multiple lists and have per-site profile overrides. See [subscriptions.md](subscriptions.md).

**SubscriberSiteProfile** — a per-site profile override for a subscriber (e.g., "researcher" on one site, "patient" on another). See [subscriptions.md](subscriptions.md).

**Team** — a logical group that owns subjects, sources, subscriber lists, and optionally its own site and Postmark credentials. Multiple teams can share one GregoryAI instance. See [06-organisations-teams-and-sites.md](06-organisations-teams-and-sites.md).

**Trial** — a clinical trial record. Trials share the same ingestion pipeline as articles but are stored in their own `Trials` model with WHO ICTRP fields. See [02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md).
