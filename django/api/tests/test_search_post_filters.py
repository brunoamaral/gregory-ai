"""
Regression tests for BodyParamsAsQueryParamsMixin: POST-body filters on the
article/trial/author search endpoints must behave identically to the same
filters sent on the query string, for every ArticleFilter/TrialFilter/
AuthorFilter field — not just the hand-plumbed title/summary/search/status
params. Before this fix, DjangoFilterBackend/OrderingFilter only ever read
request.query_params, so a filter sent in a POST body was silently dropped
and the response came back unfiltered (a wrong 200, not an error).
"""

from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from gregory.models import (
	ArticleSubjectRelevance,
	Articles,
	Authors,
	Organization,
	OrganizationApiSettings,
	Subject,
	Team,
	Trials,
)


class ArticleSearchPostFilterTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.org = Organization.objects.create(
			name="Search POST Filter Org", slug="search-post-filter-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Search POST Filter Team",
			slug="search-post-filter-team",
			organization=self.org,
		)
		self.subject = Subject.objects.create(
			subject_name="Search POST Filter Subject",
			subject_slug="search-post-filter-subject",
			team=self.team,
		)
		self.other_subject = Subject.objects.create(
			subject_name="Search POST Filter Other Subject",
			subject_slug="search-post-filter-other-subject",
			team=self.team,
		)

		def make_article(title, link, pub_date, subjects=(self.subject,)):
			a = Articles.objects.create(title=title, link=link)
			a.published_date = pub_date
			a.save(update_fields=["published_date"])
			a.teams.add(self.team)
			for s in subjects:
				a.subjects.add(s)
			return a

		self.a_2022 = make_article(
			"Article 2022", "https://example.com/pf-1", timezone.make_aware(
				timezone.datetime(2022, 6, 1, 9, 0)
			)
		)
		self.a_2023 = make_article(
			"Article 2023", "https://example.com/pf-2", timezone.make_aware(
				timezone.datetime(2023, 6, 1, 9, 0)
			)
		)
		self.a_2024 = make_article(
			"Article 2024", "https://example.com/pf-3", timezone.make_aware(
				timezone.datetime(2024, 6, 1, 9, 0)
			)
		)
		self.a_both_subjects = make_article(
			"Article Both Subjects",
			"https://example.com/pf-4",
			timezone.make_aware(timezone.datetime(2023, 7, 1, 9, 0)),
			subjects=(self.subject, self.other_subject),
		)

		ArticleSubjectRelevance.objects.create(
			article=self.a_2023, subject=self.subject, is_relevant=True
		)

		self.base_params = {"team_id": self.team.id, "subject_id": self.subject.id}

	def _get(self, extra):
		url = reverse("article-search")
		return self.client.get(url, {**self.base_params, **extra})

	def _post(self, extra):
		url = reverse("article-search")
		return self.client.post(url, {**self.base_params, **extra}, format="json")

	def test_published_date_range_post_matches_get(self):
		params = {
			"published_date_after": "2023-01-01",
			"published_date_before": "2023-12-31",
		}
		get_resp = self._get(params)
		post_resp = self._post(params)

		self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
		self.assertEqual(post_resp.status_code, status.HTTP_200_OK)

		get_ids = {r["article_id"] for r in get_resp.data["results"]}
		post_ids = {r["article_id"] for r in post_resp.data["results"]}

		self.assertEqual(get_ids, post_ids)
		self.assertIn(self.a_2023.article_id, post_ids)
		self.assertIn(self.a_both_subjects.article_id, post_ids)
		self.assertNotIn(self.a_2022.article_id, post_ids)
		self.assertNotIn(self.a_2024.article_id, post_ids)

	def test_published_date_after_ignored_pre_fix_regression(self):
		"""Without the fix this POST would return every article for the team/subject."""
		post_resp = self._post({"published_date_after": "2024-01-01"})
		self.assertEqual(post_resp.status_code, status.HTTP_200_OK)
		post_ids = {r["article_id"] for r in post_resp.data["results"]}
		self.assertEqual(post_ids, {self.a_2024.article_id})

	def test_relevant_boolean_post_matches_get(self):
		params = {"relevant": "true"}
		get_resp = self._get(params)
		post_resp = self._post(params)

		self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
		self.assertEqual(post_resp.status_code, status.HTTP_200_OK)

		get_ids = {r["article_id"] for r in get_resp.data["results"]}
		post_ids = {r["article_id"] for r in post_resp.data["results"]}

		self.assertEqual(get_ids, post_ids)
		self.assertEqual(post_ids, {self.a_2023.article_id})

	def test_subjects_list_body_value_matches_csv_query_string(self):
		"""A JSON list body value must be equivalent to a comma-joined query string."""
		subject_ids = [self.subject.id, self.other_subject.id]
		get_resp = self._get({"subjects": ",".join(str(i) for i in subject_ids)})
		post_resp = self._post({"subjects": subject_ids})

		self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
		self.assertEqual(post_resp.status_code, status.HTTP_200_OK)

		get_ids = {r["article_id"] for r in get_resp.data["results"]}
		post_ids = {r["article_id"] for r in post_resp.data["results"]}

		self.assertEqual(get_ids, post_ids)
		self.assertEqual(post_ids, {self.a_both_subjects.article_id})

	def test_query_string_wins_over_body(self):
		"""Same key in both places: the query-string value takes precedence."""
		url = reverse("article-search")
		response = self.client.post(
			url + "?published_date_after=2024-01-01",
			{**self.base_params, "published_date_after": "2022-01-01"},
			format="json",
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["article_id"] for r in response.data["results"]}
		self.assertEqual(ids, {self.a_2024.article_id})

	def test_mismatched_query_string_team_id_has_no_effect_on_post(self):
		"""team_id/subject_id are ArticleFilter fields too. Without
		identity_params forcing them from the body, a disagreeing query-string
		team_id would leak into the filterset and silently intersect against
		the body's team_id, emptying the results — not just being ignored."""
		other_team = Team.objects.create(
			name="Other POST Filter Team",
			slug="other-post-filter-team",
			organization=self.org,
		)
		url = reverse("article-search")
		baseline = self.client.post(url, self.base_params, format="json")
		response = self.client.post(
			f"{url}?team_id={other_team.id}&subject_id={self.subject.id}",
			self.base_params,
			format="json",
		)
		self.assertEqual(baseline.status_code, status.HTTP_200_OK)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		baseline_ids = {r["article_id"] for r in baseline.data["results"]}
		ids = {r["article_id"] for r in response.data["results"]}
		self.assertEqual(ids, baseline_ids)
		self.assertGreater(len(ids), 0)

	def test_title_search_still_works_on_post(self):
		"""Regression: title/summary/search still work now that the manual
		get_queryset handling was removed in favor of ArticleFilter."""
		response = self._post({"title": "2024"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["article_id"] for r in response.data["results"]}
		self.assertEqual(ids, {self.a_2024.article_id})

	def test_ordering_still_validated_on_post(self):
		"""Regression: garbage ordering values are ignored, not applied blindly."""
		response = self._post({"ordering": "nonexistent_field"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)

	def test_missing_required_parameters_contract_unchanged(self):
		url = reverse("article-search")
		response = self.client.post(url, {}, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

		response = self.client.post(
			url, {"team_id": 999999, "subject_id": self.subject.id}, format="json"
		)
		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class TrialSearchPostFilterTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.org = Organization.objects.create(
			name="Trial Search POST Filter Org", slug="trial-search-post-filter-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Trial Search POST Filter Team",
			slug="trial-search-post-filter-team",
			organization=self.org,
		)
		self.subject = Subject.objects.create(
			subject_name="Trial Search POST Filter Subject",
			subject_slug="trial-search-post-filter-subject",
			team=self.team,
		)

		def make_trial(title, link, date_registration, phase=None, has_results=False):
			t = Trials.objects.create(
				title=title,
				link=link,
				date_registration=date_registration,
				phase=phase,
				results_posted=has_results,
			)
			t.teams.add(self.team)
			t.subjects.add(self.subject)
			return t

		self.t_2023 = make_trial(
			"Trial 2023", "https://example.com/tpf-1", date(2023, 6, 1)
		)
		self.t_2024 = make_trial(
			"Trial 2024",
			"https://example.com/tpf-2",
			date(2024, 6, 1),
			phase="Phase 2",
			has_results=True,
		)

		self.base_params = {"team_id": self.team.id, "subject_id": self.subject.id}

	def _get(self, extra):
		url = reverse("trial-search")
		return self.client.get(url, {**self.base_params, **extra})

	def _post(self, extra):
		url = reverse("trial-search")
		return self.client.post(url, {**self.base_params, **extra}, format="json")

	def test_date_registration_range_post_matches_get(self):
		params = {
			"date_registration_after": "2024-01-01",
			"date_registration_before": "2024-12-31",
		}
		get_resp = self._get(params)
		post_resp = self._post(params)

		self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
		self.assertEqual(post_resp.status_code, status.HTTP_200_OK)

		get_ids = {r["trial_id"] for r in get_resp.data["results"]}
		post_ids = {r["trial_id"] for r in post_resp.data["results"]}

		self.assertEqual(get_ids, post_ids)
		self.assertEqual(post_ids, {self.t_2024.trial_id})

	def test_has_results_boolean_post_matches_get(self):
		params = {"has_results": "true"}
		get_resp = self._get(params)
		post_resp = self._post(params)

		self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
		self.assertEqual(post_resp.status_code, status.HTTP_200_OK)

		get_ids = {r["trial_id"] for r in get_resp.data["results"]}
		post_ids = {r["trial_id"] for r in post_resp.data["results"]}

		self.assertEqual(get_ids, post_ids)
		self.assertEqual(post_ids, {self.t_2024.trial_id})

	def test_phase_normalized_post_matches_get(self):
		params = {"phase_normalized": "phase_2"}
		get_resp = self._get(params)
		post_resp = self._post(params)

		self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
		self.assertEqual(post_resp.status_code, status.HTTP_200_OK)

		get_ids = {r["trial_id"] for r in get_resp.data["results"]}
		post_ids = {r["trial_id"] for r in post_resp.data["results"]}

		self.assertEqual(get_ids, post_ids)
		self.assertEqual(post_ids, {self.t_2024.trial_id})

	def test_date_registration_after_ignored_pre_fix_regression(self):
		"""Without the fix this POST would return every trial for the team/subject."""
		post_resp = self._post({"date_registration_after": "2024-01-01"})
		self.assertEqual(post_resp.status_code, status.HTTP_200_OK)
		post_ids = {r["trial_id"] for r in post_resp.data["results"]}
		self.assertEqual(post_ids, {self.t_2024.trial_id})

	def test_mismatched_query_string_subject_id_has_no_effect_on_post(self):
		"""team_id/subject_id are TrialFilter fields too. Without
		identity_params forcing them from the body, a disagreeing query-string
		subject_id would leak into the filterset and silently intersect against
		the body's subject_id, emptying the results — not just being ignored."""
		other_subject = Subject.objects.create(
			subject_name="Other Trial POST Filter Subject",
			subject_slug="other-trial-post-filter-subject",
			team=self.team,
		)
		url = reverse("trial-search")
		baseline = self.client.post(url, self.base_params, format="json")
		response = self.client.post(
			f"{url}?team_id={self.team.id}&subject_id={other_subject.id}",
			self.base_params,
			format="json",
		)
		self.assertEqual(baseline.status_code, status.HTTP_200_OK)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		baseline_ids = {r["trial_id"] for r in baseline.data["results"]}
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertEqual(ids, baseline_ids)
		self.assertGreater(len(ids), 0)

	def test_status_search_still_works_on_post(self):
		"""Regression: status is a legacy CharFilter alias for recruitment_status;
		still works now the manual get_queryset handling was removed."""
		Trials.objects.filter(pk=self.t_2023.pk).update(
			recruitment_status="Recruiting"
		)
		response = self._post({"status": "Recruiting"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertEqual(ids, {self.t_2023.trial_id})

	def test_missing_required_parameters_contract_unchanged(self):
		url = reverse("trial-search")
		response = self.client.post(url, {}, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

		response = self.client.post(
			url, {"team_id": 999999, "subject_id": self.subject.id}, format="json"
		)
		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AuthorSearchPostFilterTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.org = Organization.objects.create(
			name="Author Search POST Filter Org",
			slug="author-search-post-filter-org",
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Author Search POST Filter Team",
			slug="author-search-post-filter-team",
			organization=self.org,
		)
		self.subject = Subject.objects.create(
			subject_name="Author Search POST Filter Subject",
			subject_slug="author-search-post-filter-subject",
			team=self.team,
		)

		self.author_pt = Authors.objects.create(
			given_name="Ines", family_name="Silva", country="PT"
		)
		self.author_us = Authors.objects.create(
			given_name="John", family_name="Doe", country="US"
		)

		article = Articles.objects.create(
			title="Author POST Filter Article",
			link="https://example.com/apf-1",
			published_date=timezone.now(),
		)
		article.authors.add(self.author_pt, self.author_us)
		article.teams.add(self.team)
		article.subjects.add(self.subject)

		self.base_params = {"team_id": self.team.id, "subject_id": self.subject.id}

	def _get(self, extra):
		url = reverse("author-search")
		return self.client.get(url, {**self.base_params, **extra})

	def _post(self, extra):
		url = reverse("author-search")
		return self.client.post(url, {**self.base_params, **extra}, format="json")

	def test_country_post_matches_get(self):
		"""country is only reachable via AuthorFilter (no manual get_queryset
		handling ever existed for it), so this is the clearest proof the mixin
		is what makes POST-body filters work at all."""
		params = {"country": "PT"}
		get_resp = self._get(params)
		post_resp = self._post(params)

		self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
		self.assertEqual(post_resp.status_code, status.HTTP_200_OK)

		get_ids = {r["author_id"] for r in get_resp.data["results"]}
		post_ids = {r["author_id"] for r in post_resp.data["results"]}

		self.assertEqual(get_ids, post_ids)
		self.assertEqual(post_ids, {self.author_pt.author_id})

	def test_country_ignored_pre_fix_regression(self):
		"""Without the fix this POST would return every author for the team/subject."""
		post_resp = self._post({"country": "PT"})
		self.assertEqual(post_resp.status_code, status.HTTP_200_OK)
		post_ids = {r["author_id"] for r in post_resp.data["results"]}
		self.assertEqual(post_ids, {self.author_pt.author_id})

	def test_full_name_still_works_on_post(self):
		"""Regression: full_name is handled directly in get_queryset (unchanged
		by this fix); confirm it still narrows results on POST."""
		response = self._post({"full_name": "ines"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["author_id"] for r in response.data["results"]}
		self.assertEqual(ids, {self.author_pt.author_id})

	def test_mismatched_query_string_full_name_has_no_effect_on_post(self):
		"""full_name is an AuthorFilter field too. Without identity_params
		forcing it from the body, a disagreeing query-string full_name would
		leak into the filterset and silently intersect against the body's
		full_name, emptying the results — not just being ignored."""
		url = reverse("author-search")
		response = self.client.post(
			f"{url}?full_name=nonexistent-name-xyz",
			{**self.base_params, "full_name": "ines"},
			format="json",
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["author_id"] for r in response.data["results"]}
		self.assertEqual(ids, {self.author_pt.author_id})

	def test_missing_required_parameters_contract_unchanged(self):
		url = reverse("author-search")
		response = self.client.post(url, {}, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
