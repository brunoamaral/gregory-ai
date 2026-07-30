from django.core.management.base import BaseCommand

from gregory.relevance import recompute_article_ml_scores


class Command(BaseCommand):
	help = "Backfill ml_score on all articles from their latest ML predictions."

	def add_arguments(self, parser):
		parser.add_argument(
			"--article-ids",
			nargs="+",
			type=int,
			default=None,
			help="Limit the recompute to these article IDs (default: all articles).",
		)

	def handle(self, *args, **options):
		article_ids = options.get("article_ids")
		self.stdout.write("Recomputing Articles.ml_score...")
		changed = recompute_article_ml_scores(article_ids=article_ids)
		self.stdout.write(
			self.style.SUCCESS(f"Done. ml_score updated for {changed} articles.")
		)
