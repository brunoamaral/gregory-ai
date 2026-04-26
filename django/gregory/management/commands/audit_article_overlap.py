from django.core.management.base import BaseCommand, CommandError
from django.db import connection


def _md_table(cursor):
	cols = [d[0] for d in cursor.description]
	rows = cursor.fetchall()
	if not rows:
		return "_No results._\n"
	widths = [max(len(str(c)), max(len(str(r[i])) for r in rows)) for i, c in enumerate(cols)]
	sep = "| " + " | ".join("-" * w for w in widths) + " |"
	header = "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cols)) + " |"
	lines = [header, sep]
	for row in rows:
		lines.append("| " + " | ".join(str(v).ljust(widths[i]) for i, v in enumerate(row)) + " |")
	return "\n".join(lines) + "\n"


BASE_CTE = """
	WITH base AS (
		SELECT DISTINCT at.articles_id
		FROM articles_teams at
		JOIN articles_subjects s ON s.articles_id = at.articles_id
		WHERE at.team_id = %s AND s.subject_id = %s
	)
"""

Q1 = """
	SELECT t.id AS team_id, t.name AS team_name,
	       s.id AS subject_id, s.subject_name
	FROM gregory_team t, subjects s
	WHERE t.id = %s AND s.id = %s
"""

Q2 = """
	SELECT COUNT(DISTINCT at.articles_id) AS articles_in_base
	FROM articles_teams at
	JOIN articles_subjects s ON s.articles_id = at.articles_id
	WHERE at.team_id = %s AND s.subject_id = %s
"""

Q3 = BASE_CTE + """
	SELECT
		(SELECT COUNT(*) FROM base) AS base_total,
		(SELECT COUNT(DISTINCT b.articles_id)
		   FROM base b JOIN articles_teams at2 ON at2.articles_id = b.articles_id
		  WHERE at2.team_id <> %s)       AS also_in_other_teams,
		(SELECT COUNT(DISTINCT b.articles_id)
		   FROM base b JOIN articles_subjects s2 ON s2.articles_id = b.articles_id
		  WHERE s2.subject_id <> %s)     AS also_in_other_subjects
"""

Q4 = BASE_CTE + """
	SELECT t.id AS team_id, t.name AS team_name, t.slug AS team_slug,
	       COUNT(DISTINCT at2.articles_id) AS shared_articles
	FROM base b
	JOIN articles_teams at2 ON at2.articles_id = b.articles_id
	JOIN gregory_team t     ON t.id = at2.team_id
	WHERE at2.team_id <> %s
	GROUP BY t.id, t.name, t.slug
	ORDER BY shared_articles DESC
"""

Q5 = BASE_CTE + """
	SELECT sub.id AS subject_id, sub.subject_name, sub.subject_slug,
	       sub.team_id AS owner_team_id, owner.name AS owner_team_name,
	       COUNT(DISTINCT s2.articles_id) AS shared_articles
	FROM base b
	JOIN articles_subjects s2   ON s2.articles_id = b.articles_id
	JOIN subjects sub            ON sub.id = s2.subject_id
	LEFT JOIN gregory_team owner ON owner.id = sub.team_id
	WHERE s2.subject_id <> %s
	GROUP BY sub.id, sub.subject_name, sub.subject_slug, sub.team_id, owner.name
	ORDER BY shared_articles DESC
"""

Q6 = BASE_CTE + """
	SELECT a.article_id,
	       LEFT(a.title, 80) AS title_preview,
	       (SELECT string_agg(t.name, ', ' ORDER BY t.name)
	          FROM articles_teams at JOIN gregory_team t ON t.id = at.team_id
	         WHERE at.articles_id = a.article_id) AS teams,
	       (SELECT string_agg(sub.subject_name, ', ' ORDER BY sub.subject_name)
	          FROM articles_subjects s JOIN subjects sub ON sub.id = s.subject_id
	         WHERE s.articles_id = a.article_id) AS subjects
	FROM base b
	JOIN articles a ON a.article_id = b.article_id
	ORDER BY a.article_id
	LIMIT 50
"""

Q7 = BASE_CTE + """
	SELECT at2.team_id, t.name AS team_name,
	       s2.subject_id, sub.subject_name,
	       COUNT(DISTINCT at2.articles_id) AS shared_articles
	FROM base b
	JOIN articles_teams    at2 ON at2.articles_id = b.articles_id
	JOIN articles_subjects s2  ON s2.articles_id  = b.articles_id
	JOIN gregory_team      t   ON t.id   = at2.team_id
	JOIN subjects          sub ON sub.id = s2.subject_id
	WHERE at2.team_id <> %s OR s2.subject_id <> %s
	GROUP BY at2.team_id, t.name, s2.subject_id, sub.subject_name
	ORDER BY shared_articles DESC
	LIMIT 50
"""


class Command(BaseCommand):
	help = "Audit which articles tagged for a given team+subject also appear in other teams/subjects."

	def add_arguments(self, parser):
		parser.add_argument("--team", type=int, default=1, metavar="ID", help="team_id to audit (default: 1)")
		parser.add_argument("--subject", type=int, default=1, metavar="ID", help="subject_id to audit (default: 1)")

	def handle(self, *args, **options):
		tid = options["team"]
		sid = options["subject"]

		out = []

		with connection.cursor() as cur:
			cur.execute(Q1, [tid, sid])
			row = cur.fetchone()
			if not row:
				raise CommandError(f"team_id={tid} or subject_id={sid} does not exist.")
			team_id, team_name, subject_id, subject_name = row

			cur.execute(Q2, [tid, sid])
			base_total = cur.fetchone()[0]

			cur.execute(Q3, [tid, sid, tid, sid])
			r = cur.fetchone()
			base_total_check, other_teams_count, other_subjects_count = r

			cur.execute(Q4, [tid, sid, tid])
			q4_table = _md_table(cur)

			cur.execute(Q5, [tid, sid, sid])
			q5_table = _md_table(cur)

			cur.execute(Q6, [tid, sid])
			q6_table = _md_table(cur)

			cur.execute(Q7, [tid, sid, tid, sid])
			q7_table = _md_table(cur)

		out.append(f"# Article overlap audit — team_id={tid} / subject_id={sid}\n")
		out.append(f"## Pair under audit\n- Team: {team_id} — {team_name}\n- Subject: {subject_id} — {subject_name}\n")
		out.append("## Summary")
		out.append(f"- Articles tagged with team_id={tid} AND subject_id={sid}: **{base_total}**")
		out.append(f"- Of those, also in at least one other team: **{other_teams_count}**")
		out.append(f"- Of those, also in at least one other subject: **{other_subjects_count}**\n")
		out.append("## Other teams these articles appear under (Q4)\n" + q4_table)
		out.append("## Other subjects these articles appear under (Q5)\n" + q5_table)
		out.append("## Per-article breakdown — first 50 (Q6)\n" + q6_table)
		out.append("## Team × subject co-occurrence — top 50 (Q7)\n" + q7_table)

		self.stdout.write("\n".join(out))
