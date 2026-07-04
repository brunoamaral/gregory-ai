from django.db import connection


def recompute_article_relevance(article_ids=None, threshold=0.8):
	"""Sync articles.relevant with manual + ML-consensus relevance.
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
