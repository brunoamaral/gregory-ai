# Cookbook — common tasks

Task-shaped recipes for the most frequent GregoryAI operations. Each recipe is a concise walkthrough; follow the links for full reference documentation.

---

## How do I add a new RSS source?

1. Log into the Django admin at `/admin/`.
2. Go to **Gregory > Sources** and click **Add source**.
3. Fill in:
   - **Name** — a human-readable label
   - **Link** — the RSS feed URL
   - **Subject** — the research area this source belongs to
   - **Source for** — `science paper`, `news`, or `trials`
   - **Method** — `rss`
4. Save. The next `pipeline` run will start ingesting from this source.

```bash
# Trigger the pipeline manually
docker exec gregory python manage.py pipeline
```

For BASE (Bielefeld Academic Search Engine) feeds, GregoryAI has a dedicated processor that handles DOI extraction from `dc:relation` fields automatically. No extra configuration is needed — just add the BASE feed URL as a standard RSS source.

Reference: [02-sources-and-articles.md](02-sources-and-articles.md), [feed-processors.md](feed-processors.md)

---

## How do I export articles to CSV?

```bash
# Export all articles for a team
curl "https://api.example.com/articles/?team_id=1&format=csv&all_results=true"

# Export articles matching a search
curl "https://api.example.com/articles/?team_id=1&subject_id=2&search=stem+cells&format=csv&all_results=true"

# Export via the search endpoint
curl "https://api.example.com/api/articles/search/?team_id=1&subject_id=2&search=COVID&format=csv&all_results=true"
```

Add `all_results=true` to bypass pagination. Without it you get only the first page.

Reference: [csv-export.md](csv-export.md), [article-search-api.md](article-search-api.md)

---

## How do I subscribe a user from my website?

Embed an HTML form that POSTs to `/subscriptions/new/`:

```html
<form method="POST" action="https://api.example.com/subscriptions/new/">
  <input type="text" name="first_name" required>
  <input type="text" name="last_name">
  <input type="email" name="email" required>
  <select name="profile">
    <option value="patient">Patient</option>
    <option value="researcher">Researcher</option>
    <option value="doctor">Doctor</option>
  </select>
  <input type="hidden" name="list" value="3">
  <button type="submit">Subscribe</button>
</form>
```

On success the user is redirected to `/thank-you/` on your domain. For this to work, add your domain to the **Allowed Domains** field of the target list in the Django admin (**Subscriptions → Lists**).

Reference: [subscriptions.md](subscriptions.md), [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md)

---

## How do I retrain ML models after labelling new articles?

1. Label articles as relevant or not relevant in the admin (see [04-machine-learning.md](04-machine-learning.md)).
2. Run the training command:

```bash
# Train all algorithms for a team and subject
docker exec gregory python manage.py train_models --team ms-research --subject ms

# Train only the fast algorithm first to check results
docker exec gregory python manage.py train_models --team ms-research --subject ms --algo lgbm_tfidf

# Retrain all teams at once
docker exec gregory python manage.py train_models --all-teams
```

Reference: [05-training-models.md](05-training-models.md), [ml-consensus.md](ml-consensus.md)

---

## How do I merge duplicate authors?

```bash
# Preview what would happen (always run this first)
docker exec gregory python manage.py merge_authors 0000-0000-0000-1234 --dry-run

# Run the actual merge
docker exec gregory python manage.py merge_authors 0000-0000-0000-1234

# Keep a specific author record
docker exec gregory python manage.py merge_authors 0000-0000-0000-1234 --keep-author 42
```

Reference: [merge-authors-command.md](merge-authors-command.md)

---

## How do I get articles by author via the API?

```bash
# Get all articles by author ID
GET /articles/?author_id=123

# Get authors for a team with article counts
GET /authors/?team_id=1&sort_by=article_count&order=desc

# Get authors for a specific subject this year
GET /authors/?team_id=1&subject_id=2&timeframe=year&sort_by=article_count

# Get author RSS feed by ORCID
GET /feed/author/0000-0000-0000-1234/
```

Reference: [authors-api.md](authors-api.md), [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md)

---

## How do I send the digests manually?

```bash
# Admin digest (articles from last 48 hours)
docker exec gregory python manage.py send_admin_summary

# Weekly subscriber digest
docker exec gregory python manage.py send_weekly_summary

# Dry run to preview without sending
docker exec gregory python manage.py send_weekly_summary --dry-run --debug
```

Reference: [ml-consensus.md](ml-consensus.md)

---

## How do I configure ML consensus per subject?

1. Go to the Django admin → **Gregory > Subjects**.
2. Open the subject you want to configure.
3. Set **ML consensus type** to `any`, `majority`, or `all`.
4. Save.

The change takes effect immediately on the next digest send and on `/articles/?relevant=true` API calls.

Reference: [ml-consensus.md](ml-consensus.md)

---

## How do I choose a sort order for a weekly digest list?

Each digest list has an **Article Sort Order** setting with two options:

| Mode | Behaviour |
|---|---|
| `relevancy` (default) | Only articles that pass ML consensus or are manually marked relevant are included. Ranked by confidence score. |
| `date` | All articles matching the list's subjects are included (no ML filtering), ordered by discovery date newest first. Manually excluded articles (marked not-relevant for all their subjects) are still suppressed. |

**To change the sort order:**

1. Go to Django admin → **Subscriptions > Lists**.
2. Open the list you want to configure.
3. Under **Content Settings**, change **Article Sort Order**.
4. Save.

> When **Date** mode is selected, the **ML Threshold** field is greyed out in the admin — it has no effect in this mode.

**To preview the effect before the next send:**

```bash
# Show what would be sent in date mode (no emails sent)
docker exec gregory python manage.py send_weekly_summary --dry-run --debug --days 30
```

The `--days` flag controls the lookback window; articles older than N days are excluded regardless of sort order.
