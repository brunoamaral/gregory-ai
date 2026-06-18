from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
	help = "Backfill ml_score on all articles from their latest ML predictions."

	def handle(self, *args, **options):
		self.stdout.write("Computing ml_score from predictions (single-pass update)...")
		with connection.cursor() as cursor:
			# Single statement: sets ml_score to the average of the most recent
			# prediction per (algorithm, subject) pair, or NULL when there are none.
			cursor.execute(
				"""
				UPDATE articles
				SET ml_score = (
					SELECT AVG(p.probability_score)
					FROM (
						SELECT DISTINCT ON (algorithm, subject_id)
							probability_score
						FROM gregory_mlpredictions mp
						WHERE mp.article_id = articles.article_id
						  AND mp.probability_score IS NOT NULL
						ORDER BY algorithm, subject_id, created_date DESC
					) p
				)
				"""
			)
			updated = cursor.rowcount

		self.stdout.write(
			self.style.SUCCESS(f"Done. ml_score updated for {updated} articles.")
		)
