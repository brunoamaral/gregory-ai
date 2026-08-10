"""
Tests for the Articles admin's "Refresh from CrossRef" button/view
(gregory.admin.ArticleAdmin.crossref_refresh_view) and the
gregory.services.crossref_refresh service layer.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_article_crossref_refresh
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytz
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from organizations.models import Organization

from gregory.classes import SciencePaper
from gregory.models import Articles, Authors, Team
from gregory.services.crossref_refresh import build_change_reason

User = get_user_model()


def _mock_paper(doi="10.1000/xyz", **kwargs):
	"""Build a SciencePaper mock with sensible "nothing new" defaults, the way
	test_doi_import_service.py does for the sibling import service."""
	paper = MagicMock()
	paper.doi = doi
	paper.title = None
	paper.abstract = None
	paper.published_date = None
	paper.access = None
	paper.publisher = None
	paper.journal = None
	paper.pdf_link = None
	paper.authors = []
	paper.retracted = None
	paper._work = {"issued": {"date-parts": [[2024, 3, 5]]}}
	paper.clean_abstract.side_effect = lambda: paper.abstract
	paper.refresh.return_value = None
	for k, v in kwargs.items():
		setattr(paper, k, v)
	return paper


class ArticleCrossrefRefreshViewTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Org", slug="crossref-org")
		self.team = Team.objects.create(
			organization=self.org, name="Team", slug="crossref-team"
		)
		self.article = Articles.objects.create(
			title="Old title",
			link="https://example.com/a",
			doi="10.1000/xyz",
		)
		self.article.teams.add(self.team)

		self.superuser = User.objects.create_superuser(
			username="admin", email="admin@example.com", password="pw"
		)
		self.url = reverse(
			"admin:gregory_articles_crossref_refresh", args=[self.article.pk]
		)
		self.change_url = reverse(
			"admin:gregory_articles_change", args=[self.article.pk]
		)

	def _patch_paper(self, MockSciencePaper, paper):
		MockSciencePaper.return_value = paper
		# Use the real (static) failure-detection logic against whatever
		# refresh() "returned", instead of a mock that's truthy by default.
		MockSciencePaper.is_crossref_failed = SciencePaper.is_crossref_failed

	def _get_diff(self):
		response = self.client.get(self.url)
		return response, response.context["diff"], response.context.get("signed_payload")

	def _field_index(self, diff, field_name):
		return next(i for i, fd in enumerate(diff.fields) if fd.field == field_name)

	# ------------------------------------------------------------------
	# GET
	# ------------------------------------------------------------------

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_get_renders_diff_without_mutating(self, MockSciencePaper):
		self._patch_paper(
			MockSciencePaper, _mock_paper(title="New Title", publisher="Acme")
		)
		self.client.force_login(self.superuser)

		response, diff, payload = self._get_diff()

		self.assertEqual(response.status_code, 200)
		self.article.refresh_from_db()
		self.assertEqual(self.article.title, "Old title")
		self.assertIsNone(self.article.crossref_check)
		self.assertTrue(payload)
		self.assertEqual({fd.field for fd in diff.fields}, {"title", "publisher"})

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_get_no_changes_found_redirects(self, MockSciencePaper):
		self.article.title = "Same Title"
		self.article.save()
		self._patch_paper(MockSciencePaper, _mock_paper(title="Same Title"))
		self.client.force_login(self.superuser)

		response = self.client.get(self.url)

		self.assertRedirects(response, self.change_url)

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_get_crossref_failure_shows_error_and_does_not_write(
		self, MockSciencePaper
	):
		paper = _mock_paper()
		paper.refresh.return_value = "DOI not found"
		self._patch_paper(MockSciencePaper, paper)
		self.client.force_login(self.superuser)

		response = self.client.get(self.url)

		self.assertRedirects(response, self.change_url)
		self.article.refresh_from_db()
		self.assertIsNone(self.article.crossref_check)

	def test_article_without_doi_button_hidden_and_view_refuses(self):
		article = Articles.objects.create(title="No DOI", link="https://example.com/b")
		article.teams.add(self.team)
		self.client.force_login(self.superuser)

		change_response = self.client.get(
			reverse("admin:gregory_articles_change", args=[article.pk])
		)
		self.assertIn(b"nothing to look up", change_response.content)

		url = reverse("admin:gregory_articles_crossref_refresh", args=[article.pk])
		response = self.client.get(url)

		self.assertRedirects(
			response, reverse("admin:gregory_articles_change", args=[article.pk])
		)

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_title_diff_is_cleaned_like_feed_titles(self, MockSciencePaper):
		self._patch_paper(
			MockSciencePaper, _mock_paper(title="<scp>ABC</scp> Title")
		)
		self.client.force_login(self.superuser)

		_, diff, _ = self._get_diff()
		title_row = next(fd for fd in diff.fields if fd.field == "title")

		self.assertEqual(title_row.proposed, "ABC Title")

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_crossref_none_never_blanks_populated_field(self, MockSciencePaper):
		self.article.publisher = "Existing Publisher"
		self.article.save()
		self._patch_paper(MockSciencePaper, _mock_paper(title="New Title"))
		self.client.force_login(self.superuser)

		_, diff, _ = self._get_diff()

		self.assertNotIn("publisher", [fd.field for fd in diff.fields])

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_access_unknown_current_counts_as_empty_and_preselects(
		self, MockSciencePaper
	):
		self._patch_paper(MockSciencePaper, _mock_paper(access="open"))
		self.client.force_login(self.superuser)

		_, diff, _ = self._get_diff()
		access_row = next(fd for fd in diff.fields if fd.field == "access")

		self.assertTrue(access_row.preselect)

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_access_already_determined_does_not_preselect(self, MockSciencePaper):
		self.article.access = "open"
		self.article.save()
		self._patch_paper(MockSciencePaper, _mock_paper(access="restricted"))
		self.client.force_login(self.superuser)

		_, diff, _ = self._get_diff()
		access_row = next(fd for fd in diff.fields if fd.field == "access")

		self.assertFalse(access_row.preselect)

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_access_unknown_from_unpaywall_never_downgrades_determined_value(
		self, MockSciencePaper
	):
		# SciencePaper.refresh() sets access="unknown" when Unpaywall has
		# nothing — that must never be proposed as a downgrade over an
		# already-determined "open"/"restricted" value.
		self.article.access = "open"
		self.article.save()
		self._patch_paper(MockSciencePaper, _mock_paper(access="unknown"))
		self.client.force_login(self.superuser)

		# access="unknown" is the only thing CrossRef/Unpaywall returned, and
		# it must not be proposed at all — so there's nothing left to review.
		response = self.client.get(self.url)

		self.assertRedirects(response, self.change_url)
		self.article.refresh_from_db()
		self.assertEqual(self.article.access, "open")

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_year_only_issued_warns_and_is_not_preselected_over_existing_date(
		self, MockSciencePaper
	):
		self.article.published_date = datetime(2020, 5, 5, tzinfo=pytz.UTC)
		self.article.save()
		paper = _mock_paper(
			published_date=datetime(2024, 1, 1, tzinfo=pytz.UTC),
		)
		paper._work = {"issued": {"date-parts": [[2024]]}}
		self._patch_paper(MockSciencePaper, paper)
		self.client.force_login(self.superuser)

		_, diff, _ = self._get_diff()
		date_row = next(fd for fd in diff.fields if fd.field == "published_date")

		self.assertIn("only a year", date_row.warning)
		self.assertFalse(date_row.preselect)

	# ------------------------------------------------------------------
	# POST
	# ------------------------------------------------------------------

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_post_applies_only_ticked_fields(self, MockSciencePaper):
		self._patch_paper(
			MockSciencePaper, _mock_paper(title="New Title", publisher="Acme")
		)
		self.client.force_login(self.superuser)
		_, diff, payload = self._get_diff()
		title_index = self._field_index(diff, "title")

		response = self.client.post(
			self.url, {"payload": payload, f"field_{title_index}": "on"}
		)

		self.assertRedirects(response, self.change_url)
		self.article.refresh_from_db()
		self.assertEqual(self.article.title, "New Title")
		self.assertIsNone(self.article.publisher)
		self.assertIsNotNone(self.article.crossref_check)

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_post_writes_history_row_with_change_reason(self, MockSciencePaper):
		self._patch_paper(MockSciencePaper, _mock_paper(title="New Title"))
		self.client.force_login(self.superuser)
		_, diff, payload = self._get_diff()
		title_index = self._field_index(diff, "title")

		self.client.post(self.url, {"payload": payload, f"field_{title_index}": "on"})

		self.article.refresh_from_db()
		latest_history = self.article.history.first()
		self.assertEqual(latest_history.history_change_reason, "CrossRef: title")

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_post_writes_exactly_one_history_row_per_apply(self, MockSciencePaper):
		# Regression guard: clear_marker() used to do its own save() after the
		# main article.save(), and since simple_history writes a history row on
		# every save() regardless of update_fields, that produced a duplicate
		# row carrying the same (still-set) change reason.
		self._patch_paper(MockSciencePaper, _mock_paper(title="New Title"))
		self.client.force_login(self.superuser)
		history_count_before = self.article.history.count()
		_, diff, payload = self._get_diff()
		title_index = self._field_index(diff, "title")

		self.client.post(self.url, {"payload": payload, f"field_{title_index}": "on"})

		self.article.refresh_from_db()
		self.assertEqual(self.article.history.count(), history_count_before + 1)

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_authors_add_reuses_existing_orcid_match(self, MockSciencePaper):
		existing = Authors.objects.create(
			given_name="Jane", family_name="Doe", ORCID="0000-0001-2345-6789"
		)
		self._patch_paper(
			MockSciencePaper,
			_mock_paper(
				authors=[
					{"given": "Jane", "family": "Doe", "ORCID": "0000-0001-2345-6789"}
				]
			),
		)
		self.client.force_login(self.superuser)
		_, diff, payload = self._get_diff()
		self.assertEqual(len(diff.authors), 1)
		self.assertEqual(diff.authors[0].action, "add")
		self.assertTrue(diff.authors[0].preselect)  # article currently has no authors

		self.client.post(self.url, {"payload": payload, "author_0": "on"})

		self.article.refresh_from_db()
		self.assertEqual(list(self.article.authors.all()), [existing])
		self.assertEqual(Authors.objects.filter(family_name="Doe").count(), 1)

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_authors_only_apply_still_writes_history_naming_added_author(
		self, MockSciencePaper
	):
		self._patch_paper(
			MockSciencePaper,
			_mock_paper(authors=[{"given": "Jane", "family": "Doe"}]),
		)
		self.client.force_login(self.superuser)
		_, diff, payload = self._get_diff()

		self.client.post(self.url, {"payload": payload, "author_0": "on"})

		self.article.refresh_from_db()
		self.assertEqual(self.article.authors.count(), 1)
		latest_history = self.article.history.first()
		self.assertIn("Doe", latest_history.history_change_reason)

	@patch("gregory.services.crossref_refresh.SciencePaper")
	def test_unticked_author_removal_leaves_m2m_alone(self, MockSciencePaper):
		existing = Authors.objects.create(given_name="John", family_name="Smith")
		self.article.authors.add(existing)
		self._patch_paper(MockSciencePaper, _mock_paper(authors=[]))
		self.client.force_login(self.superuser)

		_, diff, payload = self._get_diff()
		self.assertEqual(len(diff.authors), 1)
		self.assertEqual(diff.authors[0].action, "remove")
		self.assertFalse(diff.authors[0].preselect)

		# Nothing ticked: the view should refuse and change nothing.
		response = self.client.post(self.url, {"payload": payload})

		self.assertRedirects(response, self.change_url)
		self.article.refresh_from_db()
		self.assertEqual(list(self.article.authors.all()), [existing])

	def test_tampered_payload_rejected(self):
		self.client.force_login(self.superuser)
		bad_payload = signing.dumps(
			{
				"article_id": self.article.pk,
				"fields": [{"field": "title", "raw": "Hacked"}],
				"authors": [],
			},
			salt="not-the-right-salt",
		)

		response = self.client.post(
			self.url, {"payload": bad_payload, "field_0": "on"}
		)

		self.assertRedirects(response, self.change_url)
		self.article.refresh_from_db()
		self.assertEqual(self.article.title, "Old title")

	def test_expired_payload_rejected(self):
		import time

		salt = "gregory.admin.crossref_refresh"
		data = {
			"article_id": self.article.pk,
			"fields": [{"field": "title", "raw": "Hacked"}],
			"authors": [],
		}
		serialized = signing.JSONSerializer().dumps(data)
		base64d = signing.b64_encode(serialized).decode()
		old_timestamp = signing.b62_encode(int(time.time()) - 1000)
		value_with_old_timestamp = f"{base64d}:{old_timestamp}"
		# A validly-signed payload whose embedded timestamp is already outside
		# the view's max_age=900 window — forged the same way TimestampSigner
		# itself would have signed it 1000 seconds ago.
		expired_payload = signing.Signer(salt=salt).sign(value_with_old_timestamp)

		self.client.force_login(self.superuser)
		response = self.client.post(
			self.url, {"payload": expired_payload, "field_0": "on"}
		)

		self.assertRedirects(response, self.change_url)
		self.article.refresh_from_db()
		self.assertEqual(self.article.title, "Old title")

	def test_anonymous_user_cannot_trigger_apply(self):
		response = self.client.post(self.url)

		self.assertNotEqual(response.status_code, 200)
		self.article.refresh_from_db()
		self.assertIsNone(self.article.crossref_check)

	def test_staff_without_change_permission_is_forbidden(self):
		from organizations.models import OrganizationUser

		# Belongs to the article's org (so OrganizationFilterMixin's scoped
		# get_object() finds it) but has no Django model permission — the
		# has_change_permission() check inside the view is what must fire.
		staff = User.objects.create_user(
			username="staff-no-perm", password="pw", is_staff=True
		)
		OrganizationUser.objects.create(user=staff, organization=self.org)
		self.client.force_login(staff)

		response = self.client.post(self.url)

		self.assertEqual(response.status_code, 403)
		self.article.refresh_from_db()
		self.assertIsNone(self.article.crossref_check)


class ChangeReasonBuilderTest(SimpleTestCase):
	def test_empty_when_nothing_changed(self):
		self.assertEqual(build_change_reason([], [], []), "")

	def test_simple_case_fits_verbatim(self):
		reason = build_change_reason(["title", "summary"], [], [])
		self.assertEqual(reason, "CrossRef: title, summary")

	def test_never_exceeds_100_chars_with_30_authors(self):
		added = [f"VeryLongFamilyName{i}" for i in range(30)]
		reason = build_change_reason([], added, [])
		self.assertLessEqual(len(reason), 100)
		self.assertTrue(reason.startswith("CrossRef: "))

	def test_never_exceeds_100_chars_with_very_long_family_names(self):
		added = ["A" * 60, "B" * 60, "C" * 60]
		reason = build_change_reason([], added, [])
		self.assertLessEqual(len(reason), 100)

	def test_never_exceeds_100_chars_with_every_scalar_field_and_authors(self):
		fields = [
			"title",
			"summary",
			"publisher",
			"container_title",
			"published_date",
			"retracted",
			"access",
			"pdf_link",
		]
		added = [f"AddedFamilyName{i}" for i in range(10)]
		removed = [f"RemovedFamilyName{i}" for i in range(10)]
		reason = build_change_reason(fields, added, removed)
		self.assertLessEqual(len(reason), 100)
