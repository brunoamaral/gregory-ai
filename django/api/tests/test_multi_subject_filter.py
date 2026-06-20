from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.utils import timezone

from gregory.models import (
	Articles,
	Trials,
	Subject,
	Team,
	Organization,
	ArticleSubjectRelevance,
	OrganizationApiSettings,
)


class ArticleMultiSubjectFilterTests(TestCase):
	"""Tests for the ?subjects= AND-filter on /articles/"""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(name="Test Org", slug="ms-filter-org")
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Test Team", slug="test-team-ms", organization=self.org
		)

		self.subject_a = Subject.objects.create(
			subject_name="Subject A",
			subject_slug="subject-a",
			team=self.team,
		)
		self.subject_b = Subject.objects.create(
			subject_name="Subject B",
			subject_slug="subject-b",
			team=self.team,
		)
		self.subject_c = Subject.objects.create(
			subject_name="Subject C",
			subject_slug="subject-c",
			team=self.team,
		)

		# article_ab  → subjects A + B
		self.article_ab = Articles.objects.create(
			title="Article AB",
			link="https://example.com/ab",
		)
		self.article_ab.subjects.add(self.subject_a, self.subject_b)
		self.article_ab.teams.add(self.team)

		# article_a   → subject A only
		self.article_a = Articles.objects.create(
			title="Article A",
			link="https://example.com/a",
		)
		self.article_a.subjects.add(self.subject_a)
		self.article_a.teams.add(self.team)

		# article_b   → subject B only
		self.article_b = Articles.objects.create(
			title="Article B",
			link="https://example.com/b",
		)
		self.article_b.subjects.add(self.subject_b)
		self.article_b.teams.add(self.team)

		# article_abc → subjects A + B + C
		self.article_abc = Articles.objects.create(
			title="Article ABC",
			link="https://example.com/abc",
		)
		self.article_abc.subjects.add(self.subject_a, self.subject_b, self.subject_c)
		self.article_abc.teams.add(self.team)

	# ------------------------------------------------------------------
	# AND semantics – must include exactly the articles in both A and B
	# ------------------------------------------------------------------

	def test_and_match_returns_correct_articles(self):
		"""?subjects=A,B returns article_ab and article_abc (both in A and B)."""
		url = f"/articles/?subjects={self.subject_a.id},{self.subject_b.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["article_id"] for r in response.data["results"]}
		self.assertIn(self.article_ab.article_id, ids)
		self.assertIn(self.article_abc.article_id, ids)
		self.assertNotIn(self.article_a.article_id, ids)
		self.assertNotIn(self.article_b.article_id, ids)

	# ------------------------------------------------------------------
	# Single value behaves like subject_id
	# ------------------------------------------------------------------

	def test_single_subject_equivalent_to_subject_id(self):
		"""?subjects=A returns same set as ?subject_id=A."""
		url_subjects = f"/articles/?subjects={self.subject_a.id}"
		url_subject_id = f"/articles/?subject_id={self.subject_a.id}"
		r1 = self.client.get(url_subjects)
		r2 = self.client.get(url_subject_id)
		self.assertEqual(r1.status_code, status.HTTP_200_OK)
		self.assertEqual(r2.status_code, status.HTTP_200_OK)
		ids1 = {r["article_id"] for r in r1.data["results"]}
		ids2 = {r["article_id"] for r in r2.data["results"]}
		self.assertEqual(ids1, ids2)

	# ------------------------------------------------------------------
	# No match → 200 with empty results
	# ------------------------------------------------------------------

	def test_no_match_returns_empty(self):
		"""?subjects=A,B,C returns only article_abc; other IDs return empty."""
		url = f"/articles/?subjects={self.subject_a.id},{self.subject_b.id},{self.subject_c.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["article_id"] for r in response.data["results"]}
		self.assertEqual(ids, {self.article_abc.article_id})

	def test_impossible_combination_returns_empty(self):
		"""When no article belongs to both A and C alone, result is empty."""
		# Only article_abc belongs to C; but also belongs to A, so we need a subject
		# that nothing shares with C except article_abc. We'll create a subject_d that
		# no article has.
		subject_d = Subject.objects.create(
			subject_name="Subject D",
			subject_slug="subject-d",
			team=self.team,
		)
		url = f"/articles/?subjects={self.subject_a.id},{subject_d.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	# ------------------------------------------------------------------
	# Invalid values are ignored; all-invalid → empty result
	# ------------------------------------------------------------------

	def test_invalid_values_ignored(self):
		"""Non-numeric values are silently dropped; all-invalid → empty."""
		response = self.client.get("/articles/?subjects=foo,bar")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	def test_mixed_valid_invalid_uses_valid_only(self):
		"""?subjects=A,notanumber uses only the valid ID."""
		url = f"/articles/?subjects={self.subject_a.id},notanumber"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["article_id"] for r in response.data["results"]}
		# All articles tagged with A should appear
		self.assertIn(self.article_a.article_id, ids)
		self.assertIn(self.article_ab.article_id, ids)
		self.assertIn(self.article_abc.article_id, ids)
		self.assertNotIn(self.article_b.article_id, ids)

	# ------------------------------------------------------------------
	# Composition with team_id
	# ------------------------------------------------------------------

	def test_subjects_composed_with_team_id(self):
		"""?subjects=A,B&team_id=X only returns articles in both subjects AND the team."""
		# Create a second team with its own article in subjects A+B
		other_org = Organization.objects.create(name="Other Org")
		other_team = Team.objects.create(
			name="Other Team", slug="other-team-slug", organization=other_org
		)
		other_article = Articles.objects.create(
			title="Other Team Article",
			link="https://example.com/other",
		)
		other_article.subjects.add(self.subject_a, self.subject_b)
		other_article.teams.add(other_team)

		url = f"/articles/?subjects={self.subject_a.id},{self.subject_b.id}&team_id={self.team.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["article_id"] for r in response.data["results"]}
		self.assertNotIn(other_article.article_id, ids)
		self.assertIn(self.article_ab.article_id, ids)

	# ------------------------------------------------------------------
	# Composition with relevant=true
	# ------------------------------------------------------------------

	def test_subjects_composed_with_relevant(self):
		"""?subjects=A,B&relevant=true only returns articles that are also marked relevant."""
		# Mark article_ab relevant for subject_a
		ArticleSubjectRelevance.objects.create(
			article=self.article_ab,
			subject=self.subject_a,
			is_relevant=True,
		)
		# article_abc is NOT marked relevant

		url = (
			f"/articles/?subjects={self.subject_a.id},{self.subject_b.id}&relevant=true"
		)
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["article_id"] for r in response.data["results"]}
		self.assertIn(self.article_ab.article_id, ids)
		self.assertNotIn(self.article_abc.article_id, ids)

	# ------------------------------------------------------------------
	# De-duplication — each article appears at most once
	# ------------------------------------------------------------------

	def test_no_duplicate_articles(self):
		"""Results contain no duplicate article_ids despite chained joins."""
		url = f"/articles/?subjects={self.subject_a.id},{self.subject_b.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [r["article_id"] for r in response.data["results"]]
		self.assertEqual(len(ids), len(set(ids)))


class ArticleSubjectAnyFilterTests(TestCase):
	"""Tests for the ?subjects_any= OR-filter on /articles/"""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(name="Any Org", slug="any-filter-org")
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Any Team", slug="any-team-ms", organization=self.org
		)

		self.subject_a = Subject.objects.create(
			subject_name="Any Subject A",
			subject_slug="any-subject-a",
			team=self.team,
		)
		self.subject_b = Subject.objects.create(
			subject_name="Any Subject B",
			subject_slug="any-subject-b",
			team=self.team,
		)
		self.subject_c = Subject.objects.create(
			subject_name="Any Subject C",
			subject_slug="any-subject-c",
			team=self.team,
		)

		# article_a  → subject A only
		self.article_a = Articles.objects.create(
			title="Any Article A",
			link="https://example.com/any-a",
		)
		self.article_a.subjects.add(self.subject_a)
		self.article_a.teams.add(self.team)

		# article_b  → subject B only
		self.article_b = Articles.objects.create(
			title="Any Article B",
			link="https://example.com/any-b",
		)
		self.article_b.subjects.add(self.subject_b)
		self.article_b.teams.add(self.team)

		# article_c  → subject C only
		self.article_c = Articles.objects.create(
			title="Any Article C",
			link="https://example.com/any-c",
		)
		self.article_c.subjects.add(self.subject_c)
		self.article_c.teams.add(self.team)

		# article_ab → subjects A + B
		self.article_ab = Articles.objects.create(
			title="Any Article AB",
			link="https://example.com/any-ab",
		)
		self.article_ab.subjects.add(self.subject_a, self.subject_b)
		self.article_ab.teams.add(self.team)

	def test_or_match_returns_articles_in_either_subject(self):
		"""?subjects_any=A,B returns all articles tagged with A or B."""
		url = f"/articles/?subjects_any={self.subject_a.id},{self.subject_b.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["article_id"] for r in response.data["results"]}
		self.assertIn(self.article_a.article_id, ids)
		self.assertIn(self.article_b.article_id, ids)
		self.assertIn(self.article_ab.article_id, ids)
		self.assertNotIn(self.article_c.article_id, ids)

	def test_single_subject_or_same_as_subject_id(self):
		"""?subjects_any=A returns same set as ?subject_id=A."""
		url_any = f"/articles/?subjects_any={self.subject_a.id}"
		url_id = f"/articles/?subject_id={self.subject_a.id}"
		r1 = self.client.get(url_any)
		r2 = self.client.get(url_id)
		self.assertEqual(r1.status_code, status.HTTP_200_OK)
		self.assertEqual(r2.status_code, status.HTTP_200_OK)
		ids1 = {r["article_id"] for r in r1.data["results"]}
		ids2 = {r["article_id"] for r in r2.data["results"]}
		self.assertEqual(ids1, ids2)

	def test_or_broader_than_and(self):
		"""OR result is a superset of AND result for the same subjects."""
		url_or = f"/articles/?subjects_any={self.subject_a.id},{self.subject_b.id}"
		url_and = f"/articles/?subjects={self.subject_a.id},{self.subject_b.id}"
		r_or = self.client.get(url_or)
		r_and = self.client.get(url_and)
		ids_or = {r["article_id"] for r in r_or.data["results"]}
		ids_and = {r["article_id"] for r in r_and.data["results"]}
		self.assertTrue(ids_and.issubset(ids_or))
		self.assertGreater(len(ids_or), len(ids_and))

	def test_invalid_values_ignored(self):
		"""Non-numeric values are silently dropped; all-invalid → empty."""
		response = self.client.get("/articles/?subjects_any=foo,bar")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	def test_no_duplicate_articles(self):
		"""Articles tagged with multiple matched subjects appear only once."""
		url = f"/articles/?subjects_any={self.subject_a.id},{self.subject_b.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [r["article_id"] for r in response.data["results"]]
		self.assertEqual(len(ids), len(set(ids)))


class TrialMultiSubjectFilterTests(TestCase):
	"""Tests for the ?subjects= AND-filter on /trials/"""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(name="Trial Org", slug="ms-trial-org")
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Trial Team", slug="trial-team-slug", organization=self.org
		)

		self.subject_a = Subject.objects.create(
			subject_name="Trial Subject A",
			subject_slug="trial-subject-a",
			team=self.team,
		)
		self.subject_b = Subject.objects.create(
			subject_name="Trial Subject B",
			subject_slug="trial-subject-b",
			team=self.team,
		)

		self.trial_ab = Trials.objects.create(
			title="Trial AB",
			link="https://example.com/trial-ab",
			published_date=timezone.now(),
		)
		self.trial_ab.subjects.add(self.subject_a, self.subject_b)
		self.trial_ab.teams.add(self.team)

		self.trial_a = Trials.objects.create(
			title="Trial A",
			link="https://example.com/trial-a",
			published_date=timezone.now(),
		)
		self.trial_a.subjects.add(self.subject_a)
		self.trial_a.teams.add(self.team)

		self.trial_b = Trials.objects.create(
			title="Trial B",
			link="https://example.com/trial-b",
			published_date=timezone.now(),
		)
		self.trial_b.subjects.add(self.subject_b)
		self.trial_b.teams.add(self.team)

	def test_and_match_returns_correct_trials(self):
		"""?subjects=A,B returns only trial_ab."""
		url = f"/trials/?subjects={self.subject_a.id},{self.subject_b.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertIn(self.trial_ab.trial_id, ids)
		self.assertNotIn(self.trial_a.trial_id, ids)
		self.assertNotIn(self.trial_b.trial_id, ids)

	def test_single_subject_trial(self):
		"""?subjects=A returns same set as ?subject_id=A for trials."""
		url_subjects = f"/trials/?subjects={self.subject_a.id}"
		url_subject_id = f"/trials/?subject_id={self.subject_a.id}"
		r1 = self.client.get(url_subjects)
		r2 = self.client.get(url_subject_id)
		self.assertEqual(r1.status_code, status.HTTP_200_OK)
		self.assertEqual(r2.status_code, status.HTTP_200_OK)
		ids1 = {r["trial_id"] for r in r1.data["results"]}
		ids2 = {r["trial_id"] for r in r2.data["results"]}
		self.assertEqual(ids1, ids2)

	def test_no_match_returns_empty_trials(self):
		"""?subjects=A,B where no trial has both returns empty."""
		subject_c = Subject.objects.create(
			subject_name="Trial Subject C",
			subject_slug="trial-subject-c",
			team=self.team,
		)
		url = f"/trials/?subjects={self.subject_a.id},{subject_c.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	def test_no_duplicate_trials(self):
		"""Results contain no duplicate trial_ids despite chained joins."""
		url = f"/trials/?subjects={self.subject_a.id},{self.subject_b.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [r["trial_id"] for r in response.data["results"]]
		self.assertEqual(len(ids), len(set(ids)))


class TrialSubjectAnyFilterTests(TestCase):
	"""Tests for the ?subjects_any= OR-filter on /trials/"""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(name="Trial Any Org", slug="any-trial-org")
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Trial Any Team", slug="trial-any-team", organization=self.org
		)

		self.subject_a = Subject.objects.create(
			subject_name="Trial Any Subject A",
			subject_slug="trial-any-subject-a",
			team=self.team,
		)
		self.subject_b = Subject.objects.create(
			subject_name="Trial Any Subject B",
			subject_slug="trial-any-subject-b",
			team=self.team,
		)
		self.subject_c = Subject.objects.create(
			subject_name="Trial Any Subject C",
			subject_slug="trial-any-subject-c",
			team=self.team,
		)

		self.trial_a = Trials.objects.create(
			title="Trial Any A",
			link="https://example.com/trial-any-a",
			published_date=timezone.now(),
		)
		self.trial_a.subjects.add(self.subject_a)
		self.trial_a.teams.add(self.team)

		self.trial_b = Trials.objects.create(
			title="Trial Any B",
			link="https://example.com/trial-any-b",
			published_date=timezone.now(),
		)
		self.trial_b.subjects.add(self.subject_b)
		self.trial_b.teams.add(self.team)

		self.trial_c = Trials.objects.create(
			title="Trial Any C",
			link="https://example.com/trial-any-c",
			published_date=timezone.now(),
		)
		self.trial_c.subjects.add(self.subject_c)
		self.trial_c.teams.add(self.team)

		self.trial_ab = Trials.objects.create(
			title="Trial Any AB",
			link="https://example.com/trial-any-ab",
			published_date=timezone.now(),
		)
		self.trial_ab.subjects.add(self.subject_a, self.subject_b)
		self.trial_ab.teams.add(self.team)

	def test_or_match_returns_trials_in_either_subject(self):
		"""?subjects_any=A,B returns all trials tagged with A or B."""
		url = f"/trials/?subjects_any={self.subject_a.id},{self.subject_b.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertIn(self.trial_a.trial_id, ids)
		self.assertIn(self.trial_b.trial_id, ids)
		self.assertIn(self.trial_ab.trial_id, ids)
		self.assertNotIn(self.trial_c.trial_id, ids)

	def test_single_subject_or_same_as_subject_id(self):
		"""?subjects_any=A returns same set as ?subject_id=A for trials."""
		url_any = f"/trials/?subjects_any={self.subject_a.id}"
		url_id = f"/trials/?subject_id={self.subject_a.id}"
		r1 = self.client.get(url_any)
		r2 = self.client.get(url_id)
		ids1 = {r["trial_id"] for r in r1.data["results"]}
		ids2 = {r["trial_id"] for r in r2.data["results"]}
		self.assertEqual(ids1, ids2)

	def test_no_duplicate_trials(self):
		"""Trials tagged with multiple matched subjects appear only once."""
		url = f"/trials/?subjects_any={self.subject_a.id},{self.subject_b.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [r["trial_id"] for r in response.data["results"]]
		self.assertEqual(len(ids), len(set(ids)))

	def test_invalid_values_ignored(self):
		"""Non-numeric values are silently dropped; all-invalid → empty."""
		response = self.client.get("/trials/?subjects_any=foo,bar")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)
