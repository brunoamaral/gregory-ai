"""
Tests for the export_training_data command and train_models --dataset-file,
the two halves of the off-box training workflow.
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from gregory.models import Team, Subject, Articles, ArticleSubjectRelevance
from organizations.models import Organization


SUMMARY = (
	"This study evaluates treatment outcomes in multiple sclerosis patients "
	"using magnetic resonance imaging biomarkers alongside clinical disability "
	"scores collected during a twenty four month follow up period."
)


def create_labeled_articles(team, subject, count=10):
	"""Create `count` labeled articles, alternating relevant/not relevant."""
	articles = []
	for i in range(count):
		article = Articles.objects.create(
			title=f"Test Article {subject.subject_slug} {i}",
			summary=SUMMARY,
			link=f"https://example.com/{subject.subject_slug}/{i}",
		)
		article.teams.add(team)
		article.subjects.add(subject)
		ArticleSubjectRelevance.objects.create(
			article=article,
			subject=subject,
			is_relevant=(i % 2 == 0),
		)
		articles.append(article)
	return articles


class ExportTrainingDataTest(TestCase):
	"""Test case for the export_training_data management command."""

	def setUp(self):
		self.organization = Organization.objects.create(name="Test Organization")
		self.team = Team.objects.create(
			slug="test-team", name="Test Team", organization=self.organization
		)
		self.subject = Subject.objects.create(
			subject_name="Test Subject",
			subject_slug="test-subject",
			team=self.team,
			auto_predict=True,
		)
		create_labeled_articles(self.team, self.subject)
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)

	def export(self, *args):
		out = StringIO()
		call_command(
			"export_training_data",
			"--team",
			self.team.slug,
			"--output-dir",
			self.temp_dir.name,
			*args,
			stdout=out,
		)
		return out.getvalue()

	def exported_files(self):
		return sorted(Path(self.temp_dir.name).glob("*.csv"))

	def test_exports_csv_matching_build_dataset(self):
		self.export("--subject", "test-subject", "--all-articles")

		files = self.exported_files()
		self.assertEqual(len(files), 1)
		self.assertIn("test-team_test-subject_", files[0].name)

		df = pd.read_csv(files[0])
		self.assertEqual(list(df.columns), ["article_id", "text", "relevant"])
		self.assertEqual(len(df), 10)
		self.assertEqual(df["relevant"].sum(), 5)
		# Text must be cleaned like prediction time (lowercased, no HTML)
		self.assertTrue((df["text"] == df["text"].str.lower()).all())

	def test_exports_all_auto_predict_subjects_by_default(self):
		other = Subject.objects.create(
			subject_name="Other Subject",
			subject_slug="other-subject",
			team=self.team,
			auto_predict=True,
		)
		create_labeled_articles(self.team, other, count=4)
		# Subjects without auto_predict are not exported
		Subject.objects.create(
			subject_name="Manual Subject",
			subject_slug="manual-subject",
			team=self.team,
			auto_predict=False,
		)

		self.export("--all-articles")

		names = [f.name for f in self.exported_files()]
		self.assertEqual(len(names), 2)
		self.assertTrue(any("test-subject" in n for n in names))
		self.assertTrue(any("other-subject" in n for n in names))

	def test_skips_subject_without_labeled_articles(self):
		empty = Subject.objects.create(
			subject_name="Empty Subject",
			subject_slug="empty-subject",
			team=self.team,
			auto_predict=True,
		)

		output = self.export("--all-articles")

		self.assertIn("Skipping test-team/empty-subject", output)
		self.assertEqual(len(self.exported_files()), 1)

	def test_unknown_team_raises(self):
		with self.assertRaises(CommandError):
			call_command(
				"export_training_data",
				"--team",
				"nope",
				"--output-dir",
				self.temp_dir.name,
			)

	def test_unknown_subject_raises(self):
		with self.assertRaises(CommandError):
			self.export("--subject", "nope")

	def test_no_exportable_datasets_raises(self):
		ArticleSubjectRelevance.objects.all().update(is_relevant=None)
		with self.assertRaises(CommandError):
			self.export("--all-articles")


class TrainModelsDatasetFileTest(TestCase):
	"""Test case for train_models --dataset-file (the off-box training half)."""

	def setUp(self):
		self.organization = Organization.objects.create(name="Test Organization")
		self.team = Team.objects.create(
			slug="test-team", name="Test Team", organization=self.organization
		)
		self.subject = Subject.objects.create(
			subject_name="Test Subject",
			subject_slug="test-subject",
			team=self.team,
			auto_predict=True,
		)
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)

	def write_dataset(self, rows=20, columns=("article_id", "text", "relevant")):
		"""Write a dataset CSV like export_training_data produces."""
		data = [
			{
				"article_id": i,
				"text": f"cleaned text for article number {i} about sclerosis research",
				"relevant": i % 2,
			}
			for i in range(rows)
		]
		df = pd.DataFrame(data)[list(columns)]
		path = Path(self.temp_dir.name) / "dataset.csv"
		df.to_csv(path, index=False)
		return str(path)

	def call_train(self, dataset_path, **extra):
		out = StringIO()
		call_command(
			"train_models",
			"--team",
			"test-team",
			"--subject",
			"test-subject",
			"--algo",
			"lgbm_tfidf",
			"--dataset-file",
			dataset_path,
			stdout=out,
			**extra,
		)
		return out.getvalue()

	def test_trains_from_dataset_file_without_db_articles(self):
		"""Training from a file must not depend on Articles rows in the DB."""
		dataset_path = self.write_dataset()

		mock_trainer = MagicMock()
		mock_trainer.evaluate.return_value = {"accuracy": 0.9, "f1": 0.8}

		with (
			patch(
				"gregory.management.commands.train_models.get_trainer",
				return_value=mock_trainer,
			),
			patch(
				"gregory.management.commands.train_models.collect_articles"
			) as mock_collect,
			patch("django.conf.settings.BASE_DIR", self.temp_dir.name),
		):
			self.call_train(dataset_path)

		# The DB collection path must not run
		mock_collect.assert_not_called()

		# The trainer received the file's rows, split 70/15/15
		mock_trainer.train.assert_called_once()
		train_kwargs = mock_trainer.train.call_args.kwargs
		self.assertEqual(len(train_kwargs["train_texts"]), 14)
		self.assertEqual(len(train_kwargs["val_texts"]), 3)
		mock_trainer.save.assert_called_once()

	def test_missing_columns_fails_run(self):
		"""A dataset file missing required columns must fail fast during
		argument validation, before any training run is attempted."""
		dataset_path = self.write_dataset(columns=("article_id", "text"))

		mock_trainer = MagicMock()
		with (
			patch(
				"gregory.management.commands.train_models.get_trainer",
				return_value=mock_trainer,
			),
			patch("django.conf.settings.BASE_DIR", self.temp_dir.name),
			self.assertRaises(CommandError),
		):
			self.call_train(dataset_path)

		mock_trainer.train.assert_not_called()

	def test_dataset_file_requires_subject(self):
		dataset_path = self.write_dataset()
		with self.assertRaises(CommandError):
			call_command(
				"train_models",
				"--team",
				"test-team",
				"--dataset-file",
				dataset_path,
			)

	def test_dataset_file_conflicts_with_all_articles(self):
		dataset_path = self.write_dataset()
		with self.assertRaises(CommandError):
			self.call_train(dataset_path, all_articles=True)

	def test_dataset_file_conflicts_with_pseudo_label(self):
		dataset_path = self.write_dataset()
		with self.assertRaises(CommandError):
			self.call_train(dataset_path, pseudo_label=True)

	def test_missing_dataset_file_raises(self):
		with self.assertRaises(CommandError):
			self.call_train(str(Path(self.temp_dir.name) / "does-not-exist.csv"))

	def test_metrics_json_merges_trainer_architecture_keys(self):
		"""train_models must not clobber the metrics.json written by
		trainer.save(). BertTrainer.save() writes architecture keys like
		max_len/dense_units that BertTrainer.load() later restores (#734);
		the final metrics.json must keep those keys alongside the fresh
		val_/test_ metrics computed here, rather than overwriting the file."""
		dataset_path = self.write_dataset()

		def fake_save(model_dir):
			model_dir = Path(model_dir)
			model_dir.mkdir(parents=True, exist_ok=True)
			metrics_path = model_dir / "metrics.json"
			with open(metrics_path, "w") as f:
				json.dump({"max_len": 128, "dense_units": 48}, f)
			return {"weights_path": str(model_dir / "weights"), "metrics_info": {}}

		mock_trainer = MagicMock()
		mock_trainer.evaluate.return_value = {"accuracy": 0.9, "f1": 0.8}
		mock_trainer.save.side_effect = fake_save

		with (
			patch(
				"gregory.management.commands.train_models.get_trainer",
				return_value=mock_trainer,
			),
			patch(
				"gregory.management.commands.train_models.collect_articles"
			),
			patch("django.conf.settings.BASE_DIR", self.temp_dir.name),
		):
			self.call_train(dataset_path)

		mock_trainer.save.assert_called_once()
		model_dir = Path(mock_trainer.save.call_args.args[0])
		metrics_path = model_dir / "metrics.json"

		with open(metrics_path) as f:
			metrics = json.load(f)

		# Architecture keys written by trainer.save() must survive.
		self.assertEqual(metrics["max_len"], 128)
		self.assertEqual(metrics["dense_units"], 48)
		# The freshly computed val_/test_ metrics must also be present.
		self.assertIn("val_accuracy", metrics)
		self.assertIn("test_accuracy", metrics)
		self.assertIn("val_f1", metrics)
		self.assertIn("test_f1", metrics)

	def test_unreadable_dataset_file_raises(self):
		"""An empty/corrupt CSV must fail argument validation, not silently
		train on zero rows."""
		bad_path = Path(self.temp_dir.name) / "empty.csv"
		bad_path.write_text("")
		with self.assertRaises(CommandError):
			self.call_train(str(bad_path))


if __name__ == "__main__":
	import unittest

	unittest.main()
