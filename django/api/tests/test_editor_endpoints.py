"""
The ``/editor/`` routes (api/editor_views.py): what the MCP server calls for a
verified editor. See MCP-AUTH-PLAN.md, "Django editor endpoints".

Setup: one organisation with a public site A (subject A) and a private,
MCP-enabled site B (subject B). Editors hold grants on one site each.
"""

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils.text import slugify
from rest_framework.test import APIClient

from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from gregory.models import (
	ArticleSiteContent,
	ArticleSubjectRelevance,
	ArticleTrialReference,
	Articles,
	Subject,
	Team,
	Trials,
)
from mcpauth.models import SiteEditor
from mcpauth.tests.helpers import SERVICE_KEY, grant, make_org, make_user
from sitesettings.models import CustomSetting


def _subject(org, name):
	team = Team.objects.create(organization=org, name=f"{name} team", slug=slugify(f"{name} team"))
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


def headers(user, site, key=SERVICE_KEY):
	"""What the MCP server sends for a verified editor."""
	out = {
		"HTTP_X_GREGORY_EDITOR_USER": str(user.pk),
		"HTTP_X_GREGORY_EDITOR_SITE": str(site.pk),
	}
	if key is not None:
		out["HTTP_AUTHORIZATION"] = f"Bearer {key}"
	return out


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class EditorFixture(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = make_org("Editor Org")
		cls.other_org = make_org("Editor Other Org")
		cls.subject_a = _subject(cls.org, "Subject A")
		cls.subject_b = _subject(cls.org, "Subject B")
		cls.subject_other = _subject(cls.other_org, "Subject Other")
		cls.site_a = publish_subjects(cls.subject_a, organization=cls.org, domain="ed-a.example.com")
		cls.site_b = private_site_publishing(cls.subject_b, organization=cls.org, domain="ed-b.example.com")
		cls.site_other = publish_subjects(cls.subject_other, organization=cls.other_org, domain="ed-o.example.com")
		CustomSetting.objects.filter(site__in=[cls.site_a, cls.site_b, cls.site_other]).update(mcp_enabled=True)

		cls.ana = make_user("ana", cls.org)
		cls.ben = make_user("ben", cls.org)
		grant(cls.ana, cls.site_a)
		grant(cls.ana, cls.site_b)
		grant(cls.ben, cls.site_a, can_edit=False)

		cls.art_a = Articles.objects.create(title="Article A", link="https://a.test/a", doi="10.1/aaa")
		cls.art_a.subjects.add(cls.subject_a)
		cls.art_b = Articles.objects.create(title="Article B", link="https://a.test/b", doi="10.1/bbb")
		cls.art_b.subjects.add(cls.subject_b)
		cls.art_other = Articles.objects.create(title="Article Other", link="https://a.test/o", doi="10.1/ooo")
		cls.art_other.subjects.add(cls.subject_other)

		cls.trial_a = Trials.objects.create(
			title="Trial A", link="https://t.test/a", identifiers={"nct": "NCT00000001"}
		)
		cls.trial_a.subjects.add(cls.subject_a)
		cls.trial_b = Trials.objects.create(title="Trial B", link="https://t.test/b", identifiers={"euct": "2020-000001-11"})
		cls.trial_b.subjects.add(cls.subject_b)
		cls.trial_other = Trials.objects.create(title="Trial Other", link="https://t.test/o")
		cls.trial_other.subjects.add(cls.subject_other)

	def setUp(self):
		cache.clear()
		self.client = APIClient()

	def get(self, path, user=None, site=None, **extra):
		return self.client.get(path, **headers(user or self.ana, site or self.site_a), **extra)

	def send(self, method, path, data=None, user=None, site=None):
		return getattr(self.client, method)(
			path, data, format="json", **headers(user or self.ana, site or self.site_a)
		)


class AuthenticationTest(EditorFixture):
	def test_valid_editor_is_accepted(self):
		self.assertEqual(self.get("/editor/articles/resolve/?doi=10.1/aaa").status_code, 200)

	def test_no_credential_is_401(self):
		response = self.client.get("/editor/articles/resolve/?doi=10.1/aaa")
		self.assertEqual(response.status_code, 401)

	def test_wrong_credential_is_401(self):
		response = self.client.get(
			"/editor/articles/resolve/?doi=10.1/aaa", **headers(self.ana, self.site_a, key="not-the-key")
		)
		self.assertEqual(response.status_code, 401)

	@override_settings(GREGORY_MCP_SERVICE_KEY="")
	def test_unconfigured_credential_refuses_everything(self):
		response = self.client.get("/editor/articles/resolve/?doi=10.1/aaa", **headers(self.ana, self.site_a, key=""))
		self.assertEqual(response.status_code, 401)

	def test_credential_without_editor_headers_is_401(self):
		response = self.client.get(
			"/editor/articles/resolve/?doi=10.1/aaa", HTTP_AUTHORIZATION=f"Bearer {SERVICE_KEY}"
		)
		self.assertEqual(response.status_code, 401)

	def test_editor_headers_without_the_credential_are_refused_on_any_route(self):
		for path in ("/articles/", "/editor/articles/resolve/?doi=10.1/aaa", "/tenants/", "/stats/"):
			with self.subTest(path=path):
				response = self.client.get(path, **headers(self.ana, self.site_a, key=None))
				self.assertEqual(response.status_code, 401)

	def test_one_editor_header_alone_is_refused(self):
		response = self.client.get("/articles/", HTTP_X_GREGORY_EDITOR_USER=str(self.ana.pk))
		self.assertEqual(response.status_code, 401)
		response = self.client.get("/articles/", HTTP_X_GREGORY_EDITOR_SITE=str(self.site_a.pk))
		self.assertEqual(response.status_code, 401)

	def test_editor_headers_are_only_accepted_on_editor_routes(self):
		for path in ("/articles/", "/trials/", "/articles/stats/"):
			with self.subTest(path=path):
				response = self.client.get(path, **headers(self.ana, self.site_a))
				self.assertEqual(response.status_code, 401)

	def test_user_without_a_grant_is_403(self):
		stranger = make_user("stranger", self.org)
		response = self.get("/editor/articles/resolve/?doi=10.1/aaa", user=stranger)
		self.assertEqual(response.status_code, 403)

	def test_revoked_grant_is_403_on_the_next_request(self):
		self.assertEqual(self.get("/editor/articles/resolve/?doi=10.1/aaa").status_code, 200)
		SiteEditor.objects.get(user=self.ana, site=self.site_a).revoke()
		self.assertEqual(self.get("/editor/articles/resolve/?doi=10.1/aaa").status_code, 403)

	def test_grant_revoked_by_a_raw_update_is_still_caught(self):
		SiteEditor.objects.filter(user=self.ana, site=self.site_a).update(revoked_at="2026-01-01T00:00:00Z")
		self.assertEqual(self.get("/editor/articles/resolve/?doi=10.1/aaa").status_code, 403)

	def test_grant_on_another_site_does_not_carry_over(self):
		# Ben holds site A only; naming site B is a 403, not a quiet switch.
		response = self.get("/editor/articles/resolve/?doi=10.1/bbb", user=self.ben, site=self.site_b)
		self.assertEqual(response.status_code, 403)

	def test_superuser_status_alone_is_not_a_grant(self):
		root = make_user("root", is_superuser=True, is_staff=True)
		self.assertEqual(self.get("/editor/articles/resolve/?doi=10.1/aaa", user=root).status_code, 403)

	def test_inactive_user_and_unknown_ids_are_401(self):
		self.ana.is_active = False
		self.ana.save()
		self.assertEqual(self.get("/editor/articles/resolve/?doi=10.1/aaa").status_code, 401)
		response = self.client.get(
			"/editor/articles/resolve/?doi=10.1/aaa",
			HTTP_AUTHORIZATION=f"Bearer {SERVICE_KEY}",
			HTTP_X_GREGORY_EDITOR_USER="999999",
			HTTP_X_GREGORY_EDITOR_SITE=str(self.site_a.pk),
		)
		self.assertEqual(response.status_code, 401)

	def test_non_numeric_headers_are_401(self):
		response = self.client.get(
			"/editor/articles/resolve/?doi=10.1/aaa",
			HTTP_AUTHORIZATION=f"Bearer {SERVICE_KEY}",
			HTTP_X_GREGORY_EDITOR_USER="ana",
			HTTP_X_GREGORY_EDITOR_SITE="1; drop",
		)
		self.assertEqual(response.status_code, 401)

	def test_site_with_the_assistant_off_is_403(self):
		CustomSetting.objects.filter(site=self.site_a).update(mcp_enabled=False)
		self.assertEqual(self.get("/editor/articles/resolve/?doi=10.1/aaa").status_code, 403)

	def test_401_carries_a_bearer_challenge(self):
		response = self.client.get("/editor/articles/resolve/?doi=10.1/aaa")
		self.assertEqual(response["WWW-Authenticate"], "Bearer")

	def test_session_login_does_not_open_editor_routes(self):
		self.client.force_login(self.ana)
		response = self.client.get("/editor/articles/resolve/?doi=10.1/aaa")
		self.assertEqual(response.status_code, 401)

	def test_read_only_grant_reads_but_cannot_write(self):
		self.assertEqual(self.get("/editor/articles/resolve/?doi=10.1/aaa", user=self.ben).status_code, 200)
		for method, path, data in (
			("patch", f"/editor/articles/{self.art_a.pk}/editorial/", {"takeaways": "x"}),
			("put", f"/editor/articles/{self.art_a.pk}/relevance/{self.subject_a.pk}/", {"is_relevant": True}),
			("post", f"/editor/articles/{self.art_a.pk}/trials/", {"trial_id": self.trial_a.pk}),
			("delete", f"/editor/articles/{self.art_a.pk}/trials/{self.trial_a.pk}/", None),
		):
			with self.subTest(path=path):
				self.assertEqual(self.send(method, path, data, user=self.ben).status_code, 403)
		self.assertEqual(ArticleSiteContent.objects.count(), 0)


class ResolveDoiTest(EditorFixture):
	def test_one_match(self):
		response = self.get("/editor/articles/resolve/?doi=10.1/AAA")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json(), {"article_id": self.art_a.pk, "doi": "10.1/aaa", "title": "Article A"})

	def test_no_match_is_404(self):
		self.assertEqual(self.get("/editor/articles/resolve/?doi=10.9/none").status_code, 404)

	def test_article_outside_the_sites_scope_is_the_same_404(self):
		missing = self.get("/editor/articles/resolve/?doi=10.9/none")
		outside = self.get("/editor/articles/resolve/?doi=10.1/bbb")  # site A editor, article of site B
		foreign = self.get("/editor/articles/resolve/?doi=10.1/ooo")
		self.assertEqual(outside.status_code, 404)
		self.assertEqual(foreign.status_code, 404)
		self.assertEqual(outside.json(), missing.json())

	def test_private_sites_article_resolves_for_its_editor(self):
		response = self.get("/editor/articles/resolve/?doi=10.1/bbb", site=self.site_b)
		self.assertEqual(response.status_code, 200)

	def test_several_matches_return_the_conflicting_ids(self):
		from django.db import connection

		with connection.cursor() as cursor:
			cursor.execute("DROP INDEX IF EXISTS unique_article_doi")
		twin = Articles.objects.create(title="Twin", link="https://a.test/twin", doi="10.1/AAA")
		twin.subjects.add(self.subject_a)

		response = self.get("/editor/articles/resolve/?doi=10.1/aaa")

		self.assertEqual(response.status_code, 409)
		self.assertCountEqual(response.json()["article_ids"], [self.art_a.pk, twin.pk])

	def test_duplicate_outside_the_scope_is_not_revealed(self):
		from django.db import connection

		with connection.cursor() as cursor:
			cursor.execute("DROP INDEX IF EXISTS unique_article_doi")
		hidden = Articles.objects.create(title="Hidden twin", link="https://a.test/hidden", doi="10.1/aaa")
		hidden.subjects.add(self.subject_other)

		response = self.get("/editor/articles/resolve/?doi=10.1/aaa")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["article_id"], self.art_a.pk)

	def test_doi_is_required(self):
		self.assertEqual(self.get("/editor/articles/resolve/").status_code, 400)


class EditorialWriteTest(EditorFixture):
	def url(self, article=None):
		return f"/editor/articles/{(article or self.art_a).pk}/editorial/"

	def test_creates_the_sites_row_and_returns_stored_values(self):
		response = self.send("patch", self.url(), {"takeaways": "Key finding", "summary_plain_english": "Plain"})

		self.assertEqual(response.status_code, 200, response.content)
		body = response.json()
		self.assertEqual(body["takeaways"], "Key finding")
		self.assertEqual(body["summary_plain_english"], "Plain")
		self.assertEqual(body["site_id"], self.site_a.pk)
		self.assertEqual(body["updated_by"], "ana <ana@example.com>")
		self.assertIsNotNone(body["updated_at"])
		row = ArticleSiteContent.objects.get()
		self.assertEqual((row.article, row.site), (self.art_a, self.site_a))

	def test_omitted_field_is_left_alone_and_empty_string_clears(self):
		ArticleSiteContent.objects.create(article=self.art_a, site=self.site_a, takeaways="Keep", summary_plain_english="Drop")

		response = self.send("patch", self.url(), {"summary_plain_english": ""})

		body = response.json()
		self.assertEqual(body["takeaways"], "Keep")
		self.assertIsNone(body["summary_plain_english"])

	def test_empty_body_is_400(self):
		self.assertEqual(self.send("patch", self.url(), {}).status_code, 400)

	def test_other_sites_content_is_never_touched(self):
		ArticleSiteContent.objects.create(article=self.art_a, site=self.site_b, takeaways="Site B text")

		self.send("patch", self.url(), {"takeaways": "Site A text"})

		self.assertEqual(ArticleSiteContent.objects.get(site=self.site_b).takeaways, "Site B text")
		self.assertEqual(ArticleSiteContent.objects.get(site=self.site_a).takeaways, "Site A text")

	def test_article_outside_the_scope_is_404_and_writes_nothing(self):
		for article in (self.art_b, self.art_other):
			with self.subTest(article=article.title):
				self.assertEqual(self.send("patch", self.url(article), {"takeaways": "x"}).status_code, 404)
		self.assertEqual(ArticleSiteContent.objects.count(), 0)

	def test_unknown_article_is_404(self):
		self.assertEqual(self.send("patch", "/editor/articles/99999/editorial/", {"takeaways": "x"}).status_code, 404)

	def test_history_names_the_editor_and_the_mcp_door(self):
		self.send("patch", self.url(), {"takeaways": "By Ana"})

		row = ArticleSiteContent.history.get()
		self.assertEqual(row.editor_user, self.ana)
		self.assertEqual(row.editor_label, "ana <ana@example.com>")
		self.assertEqual(row.via, "mcp")

	def test_unchanged_values_write_no_new_history(self):
		self.send("patch", self.url(), {"takeaways": "Same"})
		self.send("patch", self.url(), {"takeaways": "Same"})
		self.assertEqual(ArticleSiteContent.history.count(), 1)

	def test_second_editor_is_attributed_separately(self):
		colleague = make_user("cora", self.org)
		grant(colleague, self.site_a)
		self.send("patch", self.url(), {"takeaways": "One"})
		response = self.send("patch", self.url(), {"takeaways": "Two"}, user=colleague)
		self.assertEqual(response.json()["updated_by"], "cora <cora@example.com>")

	def test_get_is_not_allowed(self):
		self.assertEqual(self.get(self.url()).status_code, 405)


	def test_a_patch_writes_only_the_fields_it_sends(self):
		"""Writing every loaded column would let two overlapping PATCHes of
		different fields each overwrite the other's new value."""
		from unittest import mock

		ArticleSiteContent.objects.create(
			article=self.art_a, site=self.site_a, takeaways="Old", summary_plain_english="Kept"
		)
		original_save = ArticleSiteContent.save
		calls = []

		def recording_save(instance, *args, **kwargs):
			calls.append(kwargs.get("update_fields"))
			return original_save(instance, *args, **kwargs)

		with mock.patch.object(ArticleSiteContent, "save", recording_save):
			response = self.send("patch", self.url(), {"takeaways": "New"})

		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(calls, [["takeaways", "updated_at"]])
		self.assertEqual(response.json()["summary_plain_english"], "Kept")


class RelevanceWriteTest(EditorFixture):
	def url(self, article=None, subject=None):
		return f"/editor/articles/{(article or self.art_a).pk}/relevance/{(subject or self.subject_a).pk}/"

	def test_sets_true_false_and_null(self):
		for value in (True, False, None):
			with self.subTest(value=value):
				response = self.send("put", self.url(), {"is_relevant": value})
				self.assertEqual(response.status_code, 200, response.content)
				self.assertIs(response.json()["is_relevant"], value)
				self.assertIs(ArticleSubjectRelevance.objects.get().is_relevant, value)

	def test_response_says_who_and_when(self):
		body = self.send("put", self.url(), {"is_relevant": True}).json()
		self.assertEqual(body["updated_by"], "ana <ana@example.com>")
		self.assertIsNotNone(body["updated_at"])
		self.assertEqual((body["article_id"], body["subject_id"]), (self.art_a.pk, self.subject_a.pk))

	def test_subject_outside_the_sites_scope_is_404(self):
		for subject in (self.subject_b, self.subject_other):
			with self.subTest(subject=subject.subject_name):
				self.assertEqual(self.send("put", self.url(subject=subject), {"is_relevant": True}).status_code, 404)
		self.assertEqual(ArticleSubjectRelevance.objects.count(), 0)

	def test_article_outside_the_scope_is_404(self):
		self.assertEqual(self.send("put", self.url(article=self.art_other), {"is_relevant": True}).status_code, 404)

	def test_missing_or_invalid_value_is_400(self):
		self.assertEqual(self.send("put", self.url(), {}).status_code, 400)
		self.assertEqual(self.send("put", self.url(), {"is_relevant": "maybe"}).status_code, 400)

	def test_history_names_the_editor(self):
		self.send("put", self.url(), {"is_relevant": False})
		row = ArticleSubjectRelevance.history.get()
		self.assertEqual((row.editor_user, row.via), (self.ana, "mcp"))

	def test_relevance_is_shared_by_every_site_listing_the_subject(self):
		# Subject A is also published by a second site; a change there is a change here.
		second = publish_subjects(self.subject_a, organization=self.org, domain="ed-a2.example.com")
		CustomSetting.objects.filter(site=second).update(mcp_enabled=True)
		grant(self.ana, second)

		self.send("put", self.url(), {"is_relevant": True}, site=second)

		self.assertIs(ArticleSubjectRelevance.objects.get(article=self.art_a, subject=self.subject_a).is_relevant, True)


class TrialLinkTest(EditorFixture):
	def url(self, article=None, trial=None):
		base = f"/editor/articles/{(article or self.art_a).pk}/trials/"
		return base + (f"{trial.pk}/" if trial else "")

	def test_link_creates_a_manual_row_with_the_trials_primary_identifier(self):
		response = self.send("post", self.url(), {"trial_id": self.trial_a.pk})

		self.assertEqual(response.status_code, 201, response.content)
		ref = ArticleTrialReference.objects.get()
		self.assertEqual(
			(ref.source, ref.identifier_type, ref.identifier_value, ref.created_by),
			("manual", "manual", "NCT00000001", self.ana),
		)
		body = response.json()
		self.assertEqual((body["source"], body["updated_by"]), ("manual", "ana <ana@example.com>"))

	def test_linking_twice_is_idempotent(self):
		self.send("post", self.url(), {"trial_id": self.trial_a.pk})
		response = self.send("post", self.url(), {"trial_id": self.trial_a.pk})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(ArticleTrialReference.objects.count(), 1)

	def test_linking_an_already_detected_pair_creates_nothing(self):
		ArticleTrialReference.objects.create(
			article=self.art_a, trial=self.trial_a, identifier_type="nct", identifier_value="NCT00000001"
		)
		response = self.send("post", self.url(), {"trial_id": self.trial_a.pk})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["source"], "auto")
		self.assertEqual(ArticleTrialReference.objects.count(), 1)

	def test_an_existing_manual_link_is_reported_over_an_auto_one(self):
		ArticleTrialReference.objects.create(
			article=self.art_a, trial=self.trial_a, identifier_type="nct", identifier_value="NCT00000001"
		)
		ArticleTrialReference.objects.create(
			article=self.art_a,
			trial=self.trial_a,
			identifier_type="manual",
			identifier_value="NCT00000001",
			source=ArticleTrialReference.SOURCE_MANUAL,
			created_by=self.ana,
		)
		response = self.send("post", self.url(), {"trial_id": self.trial_a.pk})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["source"], "manual")
		self.assertEqual(ArticleTrialReference.objects.count(), 2)

	def test_relinking_after_an_unlink_shows_the_link_again(self):
		ArticleTrialReference.objects.create(
			article=self.art_a, trial=self.trial_a, identifier_type="nct", identifier_value="N", suppressed=True
		)

		response = self.send("post", self.url(), {"trial_id": self.trial_a.pk})

		self.assertEqual(response.status_code, 201)
		visible = ArticleTrialReference.objects.filter(article=self.art_a, suppressed=False)
		self.assertEqual([r.source for r in visible], ["manual"])

	def test_trial_or_article_outside_the_scope_is_404(self):
		self.assertEqual(self.send("post", self.url(), {"trial_id": self.trial_b.pk}).status_code, 404)
		self.assertEqual(self.send("post", self.url(), {"trial_id": self.trial_other.pk}).status_code, 404)
		self.assertEqual(self.send("post", self.url(article=self.art_other), {"trial_id": self.trial_a.pk}).status_code, 404)
		self.assertEqual(ArticleTrialReference.objects.count(), 0)

	def test_invalid_trial_id_is_400(self):
		self.assertEqual(self.send("post", self.url(), {}).status_code, 400)
		self.assertEqual(self.send("post", self.url(), {"trial_id": "x"}).status_code, 400)

	def test_trial_without_identifiers_falls_back_to_its_id(self):
		bare = Trials.objects.create(title="Bare trial", link="https://t.test/bare")
		bare.subjects.add(self.subject_a)
		self.send("post", self.url(), {"trial_id": bare.pk})
		self.assertEqual(ArticleTrialReference.objects.get().identifier_value, f"trial:{bare.pk}")

	def test_unlinking_a_manual_link_deletes_it(self):
		self.send("post", self.url(), {"trial_id": self.trial_a.pk})

		response = self.send("delete", self.url(trial=self.trial_a))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["removed"], ["manual"])
		self.assertEqual(ArticleTrialReference.objects.count(), 0)
		self.assertEqual(ArticleTrialReference.history.filter(history_type="-").count(), 1)

	def test_unlinking_an_auto_link_hides_it_instead(self):
		ref = ArticleTrialReference.objects.create(
			article=self.art_a, trial=self.trial_a, identifier_type="nct", identifier_value="NCT00000001"
		)

		response = self.send("delete", self.url(trial=self.trial_a))

		self.assertEqual(response.json()["removed"], ["auto"])
		ref.refresh_from_db()
		self.assertTrue(ref.suppressed)
		self.assertEqual(ArticleTrialReference.history.latest("history_date").via, "mcp")

	def test_unlinking_removes_every_kind_of_link_for_the_pair(self):
		ArticleTrialReference.objects.create(
			article=self.art_a, trial=self.trial_a, identifier_type="nct", identifier_value="NCT00000001"
		)
		ArticleTrialReference.objects.create(
			article=self.art_a, trial=self.trial_a, identifier_type="manual", identifier_value="x", source="manual"
		)

		response = self.send("delete", self.url(trial=self.trial_a))

		self.assertEqual(response.json()["removed"], ["auto", "manual"])

	def test_unlinking_a_pair_that_is_not_linked_is_404(self):
		self.assertEqual(self.send("delete", self.url(trial=self.trial_a)).status_code, 404)

	def test_unlinking_twice_is_404_the_second_time(self):
		ArticleTrialReference.objects.create(
			article=self.art_a, trial=self.trial_a, identifier_type="nct", identifier_value="N"
		)
		self.send("delete", self.url(trial=self.trial_a))
		self.assertEqual(self.send("delete", self.url(trial=self.trial_a)).status_code, 404)

	def test_unlinking_outside_the_scope_is_404(self):
		self.assertEqual(self.send("delete", self.url(trial=self.trial_b)).status_code, 404)
		self.assertEqual(self.send("delete", self.url(article=self.art_other, trial=self.trial_a)).status_code, 404)

	def test_a_suppressed_link_is_not_visible_in_the_api(self):
		ArticleTrialReference.objects.create(
			article=self.art_a, trial=self.trial_a, identifier_type="nct", identifier_value="N"
		)
		self.send("delete", self.url(trial=self.trial_a))

		article = self.get(f"/editor/articles/{self.art_a.pk}/").json()

		self.assertEqual(article["clinical_trials"], [])


class HistoryTest(EditorFixture):
	def url(self, article=None, query=""):
		return f"/editor/articles/{(article or self.art_a).pk}/history/{query}"

	def test_lists_editorial_relevance_and_link_changes_newest_first(self):
		self.send("patch", f"/editor/articles/{self.art_a.pk}/editorial/", {"takeaways": "First"})
		self.send("put", f"/editor/articles/{self.art_a.pk}/relevance/{self.subject_a.pk}/", {"is_relevant": True})
		self.send("post", f"/editor/articles/{self.art_a.pk}/trials/", {"trial_id": self.trial_a.pk})

		body = self.get(self.url()).json()

		self.assertEqual([e["kind"] for e in body["entries"]], ["trial_link", "relevance", "editorial"])
		first = body["entries"][-1]
		self.assertEqual(first["change"], "created")
		self.assertEqual(first["changed_by"], "ana <ana@example.com>")
		self.assertEqual(first["via"], "mcp")
		self.assertEqual(first["details"]["takeaways"], "First")

	def test_the_limit_counts_only_in_scope_link_changes(self):
		"""Out-of-scope link changes newer than an in-scope one must not push it
		off the page before scope filtering."""
		ArticleTrialReference.objects.create(
			article=self.art_a, trial=self.trial_a, identifier_type="nct", identifier_value="NCT00000001"
		)
		for index in range(3):
			ArticleTrialReference.objects.create(
				article=self.art_a, trial=self.trial_other, identifier_type=f"other{index}", identifier_value="X"
			)

		entries = self.get(self.url(query="?limit=1")).json()["entries"]

		self.assertEqual([(e["kind"], e["details"]["trial_id"]) for e in entries], [("trial_link", self.trial_a.pk)])

	def test_only_this_sites_editorial_history_is_shown(self):
		ArticleSiteContent.objects.create(article=self.art_a, site=self.site_b, takeaways="Site B text")
		self.send("patch", f"/editor/articles/{self.art_a.pk}/editorial/", {"takeaways": "Site A text"})

		entries = self.get(self.url()).json()["entries"]

		texts = [e["details"]["takeaways"] for e in entries if e["kind"] == "editorial"]
		self.assertEqual(texts, ["Site A text"])

	def test_relevance_and_links_outside_the_scope_are_left_out(self):
		ArticleSubjectRelevance.objects.create(article=self.art_a, subject=self.subject_other, is_relevant=True)
		ArticleTrialReference.objects.create(
			article=self.art_a, trial=self.trial_other, identifier_type="nct", identifier_value="N"
		)

		self.assertEqual(self.get(self.url()).json()["entries"], [])

	def test_changes_by_an_api_key_name_the_key(self):
		from gregory.models import ArticleSiteContent as Content

		row = Content.objects.create(article=self.art_a, site=self.site_a, takeaways="Key text")
		Content.history.filter(id=row.pk).update(api_access_scheme_label="Frontend key", via="api_key")

		entries = self.get(self.url()).json()["entries"]

		self.assertEqual((entries[0]["changed_by"], entries[0]["via"]), ("Frontend key", "api_key"))

	def test_a_pipeline_change_has_no_name(self):
		ArticleSubjectRelevance.objects.create(article=self.art_a, subject=self.subject_a, is_relevant=None)
		entry = self.get(self.url()).json()["entries"][0]
		self.assertIsNone(entry["changed_by"])
		self.assertIsNone(entry["via"])

	def test_limit(self):
		for index in range(5):
			ArticleSubjectRelevance.objects.update_or_create(
				article=self.art_a, subject=self.subject_a, defaults={"is_relevant": bool(index % 2)}
			)
		self.assertEqual(len(self.get(self.url("", "?limit=2")).json()["entries"]), 2)
		self.assertEqual(self.get(self.url("", "?limit=0")).status_code, 400)
		self.assertEqual(self.get(self.url("", "?limit=9999")).status_code, 400)
		self.assertEqual(self.get(self.url("", "?limit=x")).status_code, 400)

	def test_article_outside_the_scope_is_404(self):
		self.assertEqual(self.get(self.url(self.art_b)).status_code, 404)


class ThrottleTest(EditorFixture):
	def patch(self, user=None, site=None, article=None, text="x"):
		article = article or self.art_a
		return self.send("patch", f"/editor/articles/{article.pk}/editorial/", {"takeaways": text}, user=user, site=site)

	@override_settings(MCP_EDITOR_RATE_LIMITS={"hour": 3, "day": 500})
	def test_writes_past_the_hourly_limit_are_429_with_retry_after(self):
		statuses = [self.patch(text=str(i)).status_code for i in range(5)]
		self.assertEqual(statuses, [200, 200, 200, 429, 429])
		self.assertIn("Retry-After", self.patch())

	@override_settings(MCP_EDITOR_RATE_LIMITS={"hour": 60, "day": 2})
	def test_daily_limit_applies_too(self):
		self.assertEqual([self.patch(text=str(i)).status_code for i in range(3)], [200, 200, 429])

	@override_settings(MCP_EDITOR_RATE_LIMITS={"hour": 2, "day": 500})
	def test_limit_is_per_user_and_site(self):
		self.patch()
		self.patch()
		self.assertEqual(self.patch().status_code, 429)
		# Another site, same person; and another person, same site.
		self.assertEqual(self.patch(site=self.site_b, article=self.art_b).status_code, 200)
		colleague = make_user("dana", self.org)
		grant(colleague, self.site_a)
		self.assertEqual(self.patch(user=colleague).status_code, 200)

	@override_settings(MCP_EDITOR_RATE_LIMITS={"hour": 1, "day": 500})
	def test_reads_are_never_throttled(self):
		self.patch()
		for _ in range(5):
			self.assertEqual(self.get("/editor/articles/resolve/?doi=10.1/aaa").status_code, 200)

	@override_settings(MCP_EDITOR_RATE_LIMITS={"hour": 1, "day": 500})
	def test_rejected_write_changes_nothing(self):
		self.patch(text="first")
		self.patch(text="second")
		self.assertEqual(ArticleSiteContent.objects.get().takeaways, "first")


class EditorReadTest(EditorFixture):
	def titles(self, response):
		self.assertEqual(response.status_code, 200, response.content)
		return sorted(r["title"] for r in response.json()["results"])

	def test_private_site_editor_reads_the_private_scope(self):
		self.assertEqual(self.titles(self.get("/editor/articles/", site=self.site_b)), ["Article B"])
		self.assertEqual(self.titles(self.get("/editor/trials/", site=self.site_b)), ["Trial B"])

	def test_the_same_content_is_hidden_from_anonymous_callers(self):
		# Two public sites exist, so the anonymous caller is told to name one; either
		# way, private site B's article is not in the answer.
		anonymous = APIClient().get(f"/articles/?site_id={self.site_b.pk}")
		self.assertNotIn("Article B", anonymous.content.decode())
		named = APIClient().get(f"/articles/?site_id={self.site_a.pk}")
		self.assertNotIn("Article B", named.content.decode())

	def test_editor_sees_only_their_sites_scope_even_with_grants_elsewhere(self):
		# Ana holds A and B; connected to A she must not see B's articles.
		self.assertEqual(self.titles(self.get("/editor/articles/", site=self.site_a)), ["Article A"])

	def test_include_public_does_not_widen_the_scope(self):
		response = self.get("/editor/articles/?include_public=true", site=self.site_a)
		self.assertEqual(self.titles(response), ["Article A"])

	def test_team_and_site_parameters_do_not_widen_the_scope(self):
		response = self.get(f"/editor/articles/?site_id={self.site_b.pk}&team_id={self.subject_b.team_id}", site=self.site_a)
		self.assertEqual(self.titles(response), [])

	def test_detail_of_an_article_outside_the_scope_is_404(self):
		self.assertEqual(self.get(f"/editor/articles/{self.art_b.pk}/", site=self.site_a).status_code, 404)
		self.assertEqual(self.get(f"/editor/articles/{self.art_a.pk}/", site=self.site_a).status_code, 200)

	def test_other_read_endpoints_are_scoped(self):
		subjects = self.get("/editor/subjects/", site=self.site_a).json()
		names = sorted(s["subject_name"] for s in subjects["results"])
		self.assertEqual(names, ["Subject A"])
		for path in ("/editor/authors/", "/editor/categories/", "/editor/sponsors/", "/editor/articles/stats/", "/editor/trials/stats/", "/editor/stats/"):
			with self.subTest(path=path):
				self.assertEqual(self.get(path).status_code, 200)

	def test_editorial_include_shows_only_this_site_with_updated_by_and_at(self):
		ArticleSiteContent.objects.create(article=self.art_a, site=self.site_b, takeaways="B text")
		self.send("patch", f"/editor/articles/{self.art_a.pk}/editorial/", {"takeaways": "A text"})

		row = self.get("/editor/articles/?include=editorial").json()["results"][0]

		self.assertEqual(len(row["editorial"]), 1)
		entry = row["editorial"][0]
		self.assertEqual(entry["site"]["id"], self.site_a.pk)
		self.assertEqual(entry["takeaways"], "A text")
		self.assertEqual(entry["updated_by"], "ana <ana@example.com>")
		self.assertIsNotNone(entry["updated_at"])

	def test_editorial_for_a_site_with_no_row_has_null_audit_fields(self):
		entry = self.get("/editor/articles/?include=editorial").json()["results"][0]["editorial"][0]
		self.assertIsNone(entry["takeaways"])
		self.assertIsNone(entry["updated_by"])
		self.assertIsNone(entry["updated_at"])

	def test_editorial_updated_by_names_an_api_key_when_a_key_made_the_change(self):
		row = ArticleSiteContent.objects.create(article=self.art_a, site=self.site_a, takeaways="Key text")
		ArticleSiteContent.history.filter(id=row.id).update(api_access_scheme_label="Frontend key")

		entry = self.get("/editor/articles/?include=editorial").json()["results"][0]["editorial"][0]

		self.assertEqual(entry["updated_by"], "Frontend key")

	def test_ordinary_callers_get_no_audit_fields(self):
		ArticleSiteContent.objects.create(article=self.art_a, site=self.site_a, takeaways="x")
		row = APIClient().get(f"/articles/?site_id={self.site_a.pk}&include=editorial").json()["results"][0]
		self.assertNotIn("updated_by", row["editorial"][0])
		self.assertNotIn("updated_at", row["editorial"][0])

	def test_editorial_query_count_does_not_grow_with_the_page(self):
		from django.db import connection
		from django.test.utils import CaptureQueriesContext

		for i in range(6):
			article = Articles.objects.create(title=f"Bulk {i}", link=f"https://a.test/bulk{i}")
			article.subjects.add(self.subject_a)
			ArticleSiteContent.objects.create(article=article, site=self.site_a, takeaways=f"t{i}")
		self.get("/editor/articles/?include=editorial&page_size=2")  # warm
		with CaptureQueriesContext(connection) as small:
			self.get("/editor/articles/?include=editorial&page_size=2")
		with CaptureQueriesContext(connection) as large:
			self.get("/editor/articles/?include=editorial&page_size=7")
		self.assertEqual(len(small), len(large))

	def test_stats_cover_a_subject_curated_from_another_organisations_team(self):
		"""A site's scope_subjects may come from any team. /editor/stats/ counts
		what /editor/articles/ shows, not only the owning organisation's teams."""
		mixed = publish_subjects(
			self.subject_a, self.subject_other, organization=self.org, domain="ed-mixed.example.com"
		)
		CustomSetting.objects.filter(site=mixed).update(mcp_enabled=True)
		grant(self.ana, mixed)

		listed = self.get("/editor/articles/", site=mixed).json()["count"]
		stats = self.get("/editor/stats/", site=mixed).json()

		self.assertEqual(listed, 2)
		self.assertEqual(stats["articles"], 2)

	def test_read_routes_need_the_credential_too(self):
		for path in ("/editor/articles/", "/editor/trials/", "/editor/subjects/", "/editor/stats/"):
			with self.subTest(path=path):
				self.assertEqual(self.client.get(path).status_code, 401)

	def test_editor_routes_cannot_be_written_through_the_read_viewsets(self):
		self.assertEqual(self.send("post", "/editor/articles/", {"title": "x"}).status_code, 405)
		self.assertEqual(self.send("delete", f"/editor/articles/{self.art_a.pk}/").status_code, 405)


class EditorTenantsTest(EditorFixture):
	def test_requires_the_service_credential_only(self):
		self.assertEqual(self.client.get("/editor/tenants/").status_code, 401)
		ok = self.client.get("/editor/tenants/", HTTP_AUTHORIZATION=f"Bearer {SERVICE_KEY}")
		self.assertEqual(ok.status_code, 200)

	def test_lists_private_mcp_sites_too_with_their_full_scope(self):
		response = self.client.get("/editor/tenants/", HTTP_AUTHORIZATION=f"Bearer {SERVICE_KEY}")
		by_domain = {t["domain"]: t for t in response.json()}
		self.assertEqual(set(by_domain), {"ed-a.example.com", "ed-b.example.com", "ed-o.example.com"})
		self.assertFalse(by_domain["ed-b.example.com"]["api_public"])
		self.assertTrue(by_domain["ed-a.example.com"]["api_public"])
		self.assertEqual([s["subject_name"] for s in by_domain["ed-b.example.com"]["subjects"]], ["Subject B"])

	def test_the_public_tenants_endpoint_still_hides_the_private_site(self):
		domains = {t["domain"] for t in APIClient().get("/tenants/").json()}
		self.assertEqual(domains, {"ed-a.example.com", "ed-o.example.com"})

	def test_a_site_without_the_assistant_is_not_listed(self):
		CustomSetting.objects.filter(site=self.site_b).update(mcp_enabled=False)
		response = self.client.get("/editor/tenants/", HTTP_AUTHORIZATION=f"Bearer {SERVICE_KEY}")
		self.assertNotIn("ed-b.example.com", {t["domain"] for t in response.json()})

	def test_an_editor_header_pair_is_not_needed_nor_accepted_as_a_substitute(self):
		response = self.client.get("/editor/tenants/", **headers(self.ana, self.site_a, key=None))
		self.assertEqual(response.status_code, 401)
