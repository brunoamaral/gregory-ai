from django.db import connection


def recompute_article_relevance(article_ids=None, threshold=0.8):
	"""Sync articles.relevant with manual + ML-consensus relevance.

	ML consensus only counts the *latest* prediction per (article, subject,
	algorithm) pair — a retired model_version's stale score must not keep an
	article "relevant" forever after a retrain. The correlated MAX ranges over
	ALL predictions for the pair (not only qualifying ones), so a latest
	prediction that dropped below threshold correctly disqualifies the pair.
	Ties on created_date match multiple rows, which is harmless for the
	DISTINCT-algorithm count. The lookup is backed by mlpred_art_subj_date_idx
	on (article, subject, -created_date).

	Full pass when article_ids is None. Returns number of rows changed."""
	scope, params = "", [threshold]
	if article_ids is not None:
		if not article_ids:
			return 0
		scope = "WHERE a2.article_id = ANY(%s)"
		params.append(list(article_ids))
	sql = f"""
	UPDATE articles a
	SET relevant = computed.new_relevant
	FROM (
		SELECT a2.article_id,
			(
				EXISTS (
					SELECT 1 FROM gregory_articlesubjectrelevance r
					WHERE r.article_id = a2.article_id AND r.is_relevant IS TRUE
				)
				OR EXISTS (
					SELECT 1
					FROM articles_subjects xs
					JOIN subjects s ON s.id = xs.subject_id AND s.auto_predict IS TRUE
					JOIN gregory_mlpredictions mp
						ON mp.article_id = xs.articles_id
						AND mp.subject_id = xs.subject_id
						AND mp.predicted_relevant IS TRUE
						AND mp.probability_score >= %s
						AND mp.created_date = (
							SELECT MAX(mp2.created_date)
							FROM gregory_mlpredictions mp2
							WHERE mp2.article_id = mp.article_id
							  AND mp2.subject_id = mp.subject_id
							  AND mp2.algorithm = mp.algorithm
						)
					WHERE xs.articles_id = a2.article_id
					GROUP BY xs.subject_id, s.ml_consensus_type
					HAVING COUNT(DISTINCT mp.algorithm) >=
						CASE s.ml_consensus_type
							WHEN 'all' THEN 3 WHEN 'majority' THEN 2 ELSE 1 END
				)
			) AS new_relevant
		FROM articles a2
		{scope}
	) computed
	WHERE a.article_id = computed.article_id
	  AND a.relevant IS DISTINCT FROM computed.new_relevant
	"""
	with connection.cursor() as c:
		c.execute(sql, params)
		return c.rowcount


def compute_ml_drift(threshold=0.8):
	"""Live, read-only measurement of denormalized-field drift.

	Reports whether recompute_article_ml_scores / recompute_article_relevance
	would find anything to change, without writing anything — used by the
	admin summary email health line. See docs/ml-prediction-signal-bypass-plan.md
	for why this exists: bulk_create pipeline writes used to bypass the signals
	that keep these fields in sync.

	Returns a dict:
	  stale_ml_score: articles with predictions but a NULL ml_score
	  missing_relevant: articles a live consensus/manual check says should be
	    relevant, but Articles.relevant disagrees
	  unexpected_relevant: the reverse — should always be 0; non-zero here means
	    the stored flag is relevant for an article nothing currently justifies,
	    a different failure mode than staleness

	Each count is a single SQL COUNT — no article IDs are materialized into
	Python, so this stays cheap to call on every admin summary send even as
	the articles table grows.
	"""
	from django.db.models import Q

	from api.filters import ml_relevant_articles_q
	from gregory.models import Articles

	# .values("article_id") keeps the DISTINCT (needed because the reverse-FK
	# joins below can multiply rows) scoped to a single column instead of
	# every field on Articles — cheaper, and avoids a wide SELECT the count()
	# wrapper never needed in the first place.
	stale_ml_score = (
		Articles.objects.filter(
			ml_predictions_detail__isnull=False, ml_score__isnull=True
		)
		.values("article_id")
		.distinct()
		.count()
	)

	should_be_relevant_q = Q(article_subject_relevances__is_relevant=True) | (
		ml_relevant_articles_q(threshold)
	)

	missing_relevant = (
		Articles.objects.filter(should_be_relevant_q)
		.exclude(relevant=True)
		.values("article_id")
		.distinct()
		.count()
	)
	unexpected_relevant = (
		Articles.objects.filter(relevant=True)
		.exclude(should_be_relevant_q)
		.values("article_id")
		.distinct()
		.count()
	)

	return {
		"stale_ml_score": stale_ml_score,
		"missing_relevant": missing_relevant,
		"unexpected_relevant": unexpected_relevant,
	}


def recompute_article_ml_scores(article_ids=None):
	"""Sync articles.ml_score with the average of each article's latest
	prediction per (algorithm, subject) pair.

	Full pass when article_ids is None. Returns number of rows changed."""
	scope, params = "", []
	if article_ids is not None:
		if not article_ids:
			return 0
		scope = "WHERE a2.article_id = ANY(%s)"
		params.append(list(article_ids))
	sql = f"""
	UPDATE articles a
	SET ml_score = computed.new_ml_score
	FROM (
		SELECT a2.article_id,
			(
				SELECT AVG(p.probability_score)
				FROM (
					SELECT DISTINCT ON (algorithm, subject_id)
						probability_score
					FROM gregory_mlpredictions mp
					WHERE mp.article_id = a2.article_id
					  AND mp.probability_score IS NOT NULL
					ORDER BY algorithm, subject_id, created_date DESC
				) p
			) AS new_ml_score
		FROM articles a2
		{scope}
	) computed
	WHERE a.article_id = computed.article_id
	  AND a.ml_score IS DISTINCT FROM computed.new_ml_score
	"""
	with connection.cursor() as c:
		c.execute(sql, params)
		return c.rowcount
