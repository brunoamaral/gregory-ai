"""
Export labeled training datasets as CSV files for off-box model training.

Produces the exact same rows `train_models` would train on (collect_articles +
build_dataset), so a model trained elsewhere from the export matches one
trained in place. Feed the resulting file back into training with
`train_models --dataset-file`.
"""

import os
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from gregory.models import Team, Subject
from gregory.utils.dataset import collect_articles, build_dataset


class Command(BaseCommand):
	help = (
		"Export labeled training data (cleaned text + relevance labels) to CSV, "
		"one file per subject, for training models off-box with "
		"train_models --dataset-file"
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--team",
			type=str,
			required=True,
			help="Team slug to export training data for",
		)
		parser.add_argument(
			"--subject",
			type=str,
			help="Subject slug within the team (default: all subjects with auto_predict enabled)",
		)

		window_group = parser.add_mutually_exclusive_group()
		window_group.add_argument(
			"--all-articles",
			action="store_true",
			help="Use all labeled articles (ignores 90-day window)",
		)
		window_group.add_argument(
			"--lookback-days",
			type=int,
			help="Override the default 90-day window for article discovery",
		)

		parser.add_argument(
			"--output-dir",
			type=str,
			help="Directory to write CSV files to (default: <BASE_DIR>/datasets)",
		)

	def handle(self, *args, **options):
		try:
			team = Team.objects.get(slug=options["team"])
		except Team.DoesNotExist:
			raise CommandError(f"Team with slug '{options['team']}' does not exist")

		if options["subject"]:
			try:
				subjects = [
					Subject.objects.get(team=team, subject_slug=options["subject"])
				]
			except Subject.DoesNotExist:
				raise CommandError(
					f"Subject with slug '{options['subject']}' does not exist in team '{team.slug}'"
				)
		else:
			subjects = list(team.subjects.filter(auto_predict=True))
			if not subjects:
				raise CommandError(
					f"Team '{team.slug}' has no subjects with auto_predict enabled"
				)

		window_days = (
			None if options["all_articles"] else options.get("lookback_days") or 90
		)

		output_dir = Path(
			options.get("output_dir") or os.path.join(settings.BASE_DIR, "datasets")
		)
		output_dir.mkdir(parents=True, exist_ok=True)

		date_tag = datetime.now().strftime("%Y%m%d")
		exported = 0

		for subject in subjects:
			articles_qs = collect_articles(team.slug, subject.subject_slug, window_days)
			dataset_df = build_dataset(articles_qs, subject)

			if len(dataset_df) == 0:
				self.stdout.write(
					self.style.WARNING(
						f"Skipping {team.slug}/{subject.subject_slug}: no labeled articles"
					)
				)
				continue

			output_path = (
				output_dir / f"{team.slug}_{subject.subject_slug}_{date_tag}.csv"
			)
			dataset_df.to_csv(output_path, index=False)
			exported += 1

			class_counts = dataset_df["relevant"].value_counts()
			self.stdout.write(
				f"Exported {len(dataset_df)} articles "
				f"(relevant={class_counts.get(1, 0)}, not relevant={class_counts.get(0, 0)}) "
				f"for {team.slug}/{subject.subject_slug} to {output_path}"
			)

		if exported == 0:
			raise CommandError("No datasets were exported")

		self.stdout.write(self.style.SUCCESS(f"Exported {exported} dataset(s)"))
