from django.core.management.base import BaseCommand

from gregory.relevance import recompute_article_relevance


class Command(BaseCommand):
	help = "Refresh the denormalized Articles.relevant flag from manual and ML-consensus relevance."

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
		self.stdout.write("Recomputing Articles.relevant...")
		changed = recompute_article_relevance(article_ids=article_ids)
		self.stdout.write(
			self.style.SUCCESS(f"Done. relevant changed for {changed} articles.")
		)
