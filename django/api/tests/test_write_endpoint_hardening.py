"""
Regression tests for the write-endpoint hardening pass (post_article,
edit_article, edit_trial).

Covers:
  - Malformed JSON body -> 400 with the JSON error envelope, not a raw 500
    (all three endpoints).
  - An error path with an oversized payload / error message does not crash
    generateAccessSchemeLog with a DataError; the normal response is
    returned and the log row is written with truncated fields.
  - Case-variant DOI is treated as a match against the DB's
    Lower(doi)-based unique constraint: post_article routes it through the
    ARTICLE_EXISTS flow (not a raw IntegrityError -> 500), and edit_article
    finds the article instead of 404ing.
  - Trials created via post_article get a timezone-aware discovery_date.

Run with:
    docker exec gregory python manage.py test api.tests.test_write_endpoint_hardening
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase, Client
from django.utils.timezone import now, is_aware
from organizations.models import Organization

from api.models import APIAccessScheme, APIAccessSchemeLog
from api.utils.responses import ARTICLE_EXISTS, INVALID_JSON
from gregory.models import (
	Articles,
	Sources,
	Team,
	Subject,
	Trials,
	OrganizationApiSettings,
)

# ---------------------------------------------------------------------------
# Shared helpers (duplicated from test_edit_article.py / test_post_article_org_scoping.py pattern)
# ---------------------------------------------------------------------------


def _make_org(name, slug=""):
	slug = slug or name.lower().replace(" ", "-")
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=False
	)
	return org


def _make_team(org, name):
	return Team.objects.create(
		organization=org, name=name, slug=name.lower().replace(" ", "-")
	)


def _make_subject(team, name):
	from django.utils.text import slugify

	return Subject.objects.create(
		team=team, subject_name=name, subject_slug=slugify(name)
	)


def _make_source(team, subject, name, source_for="science paper"):
	return Sources.objects.create(
		name=name,
		link=f"https://src.example.com/{name}",
		team=team,
		subject=subject,
		source_for=source_for,
	)


def _make_scheme(org, name, ip_addresses=""):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f"{name}@example.com",
		organization=org,
		ip_addresses=ip_addresses,
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


def _make_article(team, doi, title="Test Article"):
	article = Articles.objects.create(
		title=title,
		link=f"https://example.com/{doi}",
		doi=doi,
	)
	article.teams.add(team)
	return article


def _post(client, api_key, payload, path="/articles/post/"):
	body = json.dumps(payload).encode()
	headers = {"HTTP_AUTHORIZATION": api_key} if api_key else {}
	return client.post(
		path,
		data=body,
		content_type="application/json",
		**headers,
	)


def _post_raw(client, api_key, raw_body, path="/articles/post/"):
	headers = {"HTTP_AUTHORIZATION": api_key} if api_key else {}
	return client.post(
		path,
		data=raw_body,
		content_type="application/json",
		**headers,
	)


def _edit_article(client, api_key, payload):
	return _post(client, api_key, payload, path="/articles/edit/")


def _edit_article_raw(client, api_key, raw_body):
	return _post_raw(client, api_key, raw_body, path="/articles/edit/")


def _edit_trial(client, api_key, payload):
	return _post(client, api_key, payload, path="/trials/edit/")


def _edit_trial_raw(client, api_key, raw_body):
	return _post_raw(client, api_key, raw_body, path="/trials/edit/")


# ---------------------------------------------------------------------------
# Fix 1 — malformed JSON body -> 400, not a raw 500
# ---------------------------------------------------------------------------


class MalformedJsonTest(TestCase):
	def setUp(self):
		self.client = Client()
		self.org = _make_org("JSON Org", "hardening-json-org")
		self.scheme = _make_scheme(self.org, "hardening-json-key")

	def test_post_article_malformed_json_returns_400(self):
		resp = _post_raw(self.client, self.scheme.api_key, b"{not valid json")
		self.assertEqual(resp.status_code, 400)
		data = resp.json()
		self.assertEqual(data["code"], INVALID_JSON)

	def test_edit_article_malformed_json_returns_400(self):
		resp = _edit_article_raw(self.client, self.scheme.api_key, b"{not valid json")
		self.assertEqual(resp.status_code, 400)
		self.assertEqual(resp.json()["code"], INVALID_JSON)

	def test_edit_trial_malformed_json_returns_400(self):
		resp = _edit_trial_raw(self.client, self.scheme.api_key, b"{not valid json")
		self.assertEqual(resp.status_code, 400)
		self.assertEqual(resp.json()["code"], INVALID_JSON)

	def test_malformed_json_without_api_key_still_returns_400_not_401(self):
		"""JSON parsing happens before auth resolution, so a bad body fails
		fast with 400 regardless of whether an API key was supplied."""
		resp = _post_raw(self.client, None, b"not json at all")
		self.assertEqual(resp.status_code, 400)

	def test_malformed_json_creates_log_with_truncated_body(self):
		oversized_garbage = b"{" + b"x" * 3000  # invalid JSON, > 1700 chars
		resp = _post_raw(self.client, self.scheme.api_key, oversized_garbage)
		self.assertEqual(resp.status_code, 400)
		log = APIAccessSchemeLog.objects.filter(
			call_type="POST /articles/post/"
		).latest("access_date")
		self.assertEqual(log.http_code, 400)
		self.assertLessEqual(len(log.payload_received), 1700)


# ---------------------------------------------------------------------------
# Fix 2 — generateAccessSchemeLog must not crash on oversized fields
# ---------------------------------------------------------------------------


class OversizedLogFieldsTest(TestCase):
	def setUp(self):
		self.client = Client()
		self.org = _make_org("Oversized Org", "hardening-oversized-org")
		self.team = _make_team(self.org, "Oversized Team")
		self.subject = _make_subject(self.team, "Oversized Subject")
		self.scheme = _make_scheme(self.org, "hardening-oversized-key")
		self.source = _make_source(
			self.team, self.subject, "hardening-oversized-source"
		)

	def test_duplicate_doi_with_oversized_payload_does_not_500(self):
		"""A duplicate-DOI POST whose payload (str(post_data)) exceeds the
		payload_received varchar(1700) column must still return the normal
		ARTICLE_EXISTS response, not an unhandled 500 from the log write."""
		existing = _make_article(self.team, doi="10.1234/oversized-payload")
		huge_summary = "A" * 3000
		payload = {
			"kind": "science paper",
			"source_id": self.source.pk,
			"title": "Oversized payload duplicate test",
			"doi": existing.doi,
			"summary": huge_summary,
			"link": "https://example.com/oversized-payload-post",
		}
		with patch("gregory.classes.SciencePaper.refresh", return_value=None):
			resp = _post(self.client, self.scheme.api_key, payload)
		self.assertEqual(resp.status_code, 200)
		data = resp.json()
		self.assertEqual(data["code"], ARTICLE_EXISTS)

		log = APIAccessSchemeLog.objects.filter(
			api_access_scheme=self.scheme
		).latest("access_date")
		self.assertEqual(log.http_code, 200)
		self.assertLessEqual(len(log.payload_received), 1700)

	def test_long_error_message_is_truncated_not_500(self):
		"""A FieldNotFoundError whose message exceeds the error_message
		varchar(500) column (because it echoes back an oversized `kind`
		value from the payload) must still return 400, not an unhandled
		500 from the log write. `doi` is capped at 280 chars by the model,
		so an oversized `kind` mismatch is the simplest way to construct a
		long error message without fighting other column limits."""
		huge_kind = "science paper" + ("z" * 600)
		payload = {
			"kind": huge_kind,  # mismatches source.source_for ("science paper")
			"source_id": self.source.pk,
			"title": "Long kind mismatch test",
			"doi": "10.1234/long-kind-mismatch",
		}
		resp = _post(self.client, self.scheme.api_key, payload)
		self.assertEqual(resp.status_code, 400)

		log = APIAccessSchemeLog.objects.filter(
			api_access_scheme=self.scheme
		).latest("access_date")
		self.assertEqual(log.http_code, 400)
		self.assertIsNotNone(log.error_message)
		self.assertLessEqual(len(log.error_message), 499)


# ---------------------------------------------------------------------------
# Fix 3 — DOI case-sensitivity must match the Lower(doi) unique constraint
# ---------------------------------------------------------------------------


class CaseVariantDoiTest(TestCase):
	def setUp(self):
		self.client = Client()
		self.org = _make_org("Case Org", "hardening-case-org")
		self.team = _make_team(self.org, "Case Team")
		self.subject = _make_subject(self.team, "Case Subject")
		self.scheme = _make_scheme(self.org, "hardening-case-key")
		self.existing_source = _make_source(
			self.team, self.subject, "hardening-case-existing-source"
		)
		self.new_source = _make_source(
			self.team, self.subject, "hardening-case-new-source"
		)
		self.article = _make_article(self.team, doi="10.1234/CaseTest")
		self.article.sources.add(self.existing_source)

	def test_post_article_case_variant_doi_returns_article_exists(self):
		"""A case-variant DOI must be caught by the pre-check (doi__iexact)
		and routed through ARTICLE_EXISTS, not fall through to an
		IntegrityError -> 500 on create()."""
		payload = {
			"kind": "science paper",
			"source_id": self.new_source.pk,
			"title": "Different title, same DOI different case",
			"doi": "10.1234/casetest",  # lower-case variant of the stored DOI
			"link": "https://example.com/case-variant-post",
		}
		with patch("gregory.classes.SciencePaper.refresh", return_value=None):
			resp = _post(self.client, self.scheme.api_key, payload)
		self.assertEqual(resp.status_code, 200)
		data = resp.json()
		self.assertEqual(data["code"], ARTICLE_EXISTS)

		# No second article was created for the case-variant DOI.
		self.assertEqual(
			Articles.objects.filter(doi__iexact="10.1234/casetest").count(), 1
		)
		# Source/team/subject associations were added to the existing article.
		self.article.refresh_from_db()
		self.assertIn(self.new_source, self.article.sources.all())
		self.assertIn(self.team, self.article.teams.all())
		self.assertIn(self.subject, self.article.subjects.all())

	def test_post_article_integrity_error_race_falls_back_to_article_exists(self):
		"""Backstop: if a concurrent request wins the race between the
		pre-check and create(), the resulting IntegrityError must still be
		converted to the ARTICLE_EXISTS flow rather than surfacing a 500."""
		race_doi = "10.5555/RaceCondition"
		real_create = Articles.objects.create

		def _simulate_concurrent_insert(**kwargs):
			# Emulate another request having just inserted the same DOI
			# (case-variant) between our pre-check and this create() call.
			real_create(
				title="Concurrent Insert",
				link="https://example.com/race-condition-concurrent",
				doi=race_doi,
			)
			raise IntegrityError(
				'duplicate key value violates unique constraint "unique_article_doi"'
			)

		payload = {
			"kind": "science paper",
			"source_id": self.new_source.pk,
			"title": "Race condition article",
			"doi": "10.5555/racecondition",  # case-variant of race_doi
			"link": "https://example.com/race-condition-post",
		}
		with patch("gregory.classes.SciencePaper.refresh", return_value=None), patch.object(
			Articles.objects, "create", side_effect=_simulate_concurrent_insert
		):
			resp = _post(self.client, self.scheme.api_key, payload)

		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.json()["code"], ARTICLE_EXISTS)
		self.assertEqual(
			Articles.objects.filter(doi__iexact=race_doi).count(), 1
		)

	def test_post_article_title_link_race_with_doi_present_reports_title_conflict(self):
		"""A title+link race can fire even when the payload carries a DOI
		(concurrent insert with the same title+link but a different DOI).
		The backstop must branch on the violated constraint: this must NOT
		be handled as a DOI conflict — the DOI lookup would find nothing
		and the client would get a misleading DOI-exists message."""

		def _simulate_title_link_race(**kwargs):
			raise IntegrityError(
				'duplicate key value violates unique constraint "unique_article_title_link"'
			)

		payload = {
			"kind": "science paper",
			"source_id": self.new_source.pk,
			"title": "Race condition article with unrelated DOI",
			"doi": "10.9999/unrelated-doi",
			"link": "https://example.com/title-link-race-post",
		}
		with patch("gregory.classes.SciencePaper.refresh", return_value=None), patch.object(
			Articles.objects, "create", side_effect=_simulate_title_link_race
		):
			resp = _post(self.client, self.scheme.api_key, payload)

		self.assertEqual(resp.status_code, 200)
		data = resp.json()
		self.assertEqual(data["code"], ARTICLE_EXISTS)
		self.assertIn("Title", data["extra_data"])
		self.assertNotIn("DOI", data["extra_data"])

	def test_post_article_integrity_error_with_null_doi_does_not_mass_attach(self):
		"""If create() hits an IntegrityError while the payload has no DOI
		(a title+link race), the backstop must NOT run doi__iexact=None —
		that matches every DOI-less article and would attach the source to
		all of them. It must surface the title ARTICLE_EXISTS flow instead."""
		bystander = _make_article(
			self.team, doi=None, title="Innocent bystander without DOI"
		)

		def _simulate_title_link_race(**kwargs):
			raise IntegrityError(
				'duplicate key value violates unique constraint "unique_article_title_link"'
			)

		payload = {
			"kind": "science paper",
			"source_id": self.new_source.pk,
			"title": "Race condition article without DOI",
			"link": "https://example.com/null-doi-race-post",
		}
		with patch(
			"gregory.classes.SciencePaper.find_doi", return_value=None
		), patch("gregory.classes.SciencePaper.refresh", return_value=None), patch.object(
			Articles.objects, "create", side_effect=_simulate_title_link_race
		):
			resp = _post(self.client, self.scheme.api_key, payload)

		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.json()["code"], ARTICLE_EXISTS)
		# The DOI-less bystander was left untouched.
		self.assertNotIn(self.new_source, bystander.sources.all())

	def test_edit_article_case_variant_doi_returns_200(self):
		resp = _edit_article(
			self.client,
			self.scheme.api_key,
			{"doi": "10.1234/CASETEST", "takeaways": "case-insensitive edit works"},
		)
		self.assertEqual(resp.status_code, 200)
		data = resp.json()
		self.assertEqual(data["article_id"], self.article.article_id)


# ---------------------------------------------------------------------------
# Fix 5 — discovery_date on Trials created via post_article is tz-aware
# ---------------------------------------------------------------------------


class TrialDiscoveryDateTimezoneTest(TestCase):
	def setUp(self):
		self.client = Client()
		self.org = _make_org("TZ Org", "hardening-tz-org")
		self.team = _make_team(self.org, "TZ Team")
		self.subject = _make_subject(self.team, "TZ Subject")
		self.scheme = _make_scheme(self.org, "hardening-tz-key")
		self.source = _make_source(
			self.team, self.subject, "hardening-tz-source", source_for="trials"
		)

	def test_trial_discovery_date_is_timezone_aware(self):
		payload = {
			"kind": "trials",
			"source_id": self.source.pk,
			"title": "TZ Aware Trial",
			"link": "https://example.com/tz-aware-trial",
			"identifiers": {"nct": "NCT9990001"},
		}
		resp = _post(self.client, self.scheme.api_key, payload)
		# post_article replies 200 with a JSON envelope carrying trial_id.
		self.assertEqual(resp.status_code, 200, resp.content)
		self.assertIn("trial_id", resp.json())

		trial = Trials.objects.get(identifiers__nct="NCT9990001")
		self.assertIsNotNone(trial.discovery_date)
		self.assertTrue(is_aware(trial.discovery_date))
