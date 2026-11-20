"""Tests for ``GET /labs/`` and ``GET /labs/{id}/``.

Run with:
    pytest api/tests/test_labs_api.py
"""

import json
from datetime import timedelta
from itertools import count

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now
from rest_framework.test import APIClient, APIRequestFactory

from api.labs import (
	lab_display_name,
	lab_display_name_expr,
	visible_category_links,
	visible_labs_q,
	visible_subject_links,
)
from api.models import APIAccessScheme
from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from api.views import LabViewSet
from gregory.models import (
	Authors,
	Institution,
	Lab,
	LabCategory,
	LabMembership,
	LabSubject,
	Subject,
	Team,
	TeamCategory,
)
from organizations.models import Organization

_seq = count(1)

FORBIDDEN_KEYS = {
	"notes",
	"needs_review",
	"review_reason",
	"is_manual",
	"is_hidden",
	"collaborators",
	"collaborator_count",
	"core_count",
	"email",
	"emails",
	"orcid_verified_email",
	"current_affiliation",
	"affiliation",
	"affiliations",
	"same_institution",
	"shared_articles",
	"role",
	"computed_at",
	"last_author_articles",
	"external_ids",
	"biography",
}


def _api_scheme(org, site):
	return APIAccessScheme.objects.create(
		client_name=f"lab-key-{next(_seq)}",
		client_contacts="lab@example.com",
		organization=org,
		site=site,
		ip_addresses="",
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


def make_author(given="Ada", family=None, orcid=None, full_name=None):
	n = next(_seq)
	family = family or f"Author{n}"
	return Authors.objects.create(
		given_name=given,
		family_name=family,
		full_name=f"{given} {family}" if full_name is None else full_name,
		ORCID=orcid,
	)


def make_lab(head=None, name="", institution=None, members=0, collaborators=0, **kwargs):
	"""A lab with ``members`` role-member rows and ``collaborators`` collaborator rows."""
	lab = Lab.objects.create(head=head, name=name, institution=institution, **kwargs)
	if head is not None:
		LabMembership.objects.create(author=head, lab=lab, role="head", shared_articles=9)
	for i in range(members):
		LabMembership.objects.create(
			author=make_author(), lab=lab, role="member", shared_articles=i
		)
	for _ in range(collaborators):
		LabMembership.objects.create(author=make_author(), lab=lab, role="collaborator")
	return lab


def link_subject(lab, subject, articles=10, relevant=2, recent=3, year=2025, **kwargs):
	return LabSubject.objects.create(
		lab=lab,
		subject=subject,
		article_count=articles,
		relevant_count=relevant,
		recent_article_count=recent,
		latest_article_year=year,
		**kwargs,
	)


def link_category(lab, category, articles=10, relevant=2, recent=3, year=2025, **kwargs):
	return LabCategory.objects.create(
		lab=lab,
		category=category,
		article_count=articles,
		relevant_count=relevant,
		recent_article_count=recent,
		latest_article_year=year,
		**kwargs,
	)


class LabApiBase(TestCase):
	def setUp(self):
		cache.clear()
		self.org = Organization.objects.create(name="Lab Org", slug="lab-org")
		self.team = Team.objects.create(organization=self.org, name="Lab Team", slug="lab-team")
		self.s1 = Subject.objects.create(subject_name="Alpha", subject_slug="alpha", team=self.team)
		self.s2 = Subject.objects.create(subject_name="Beta", subject_slug="beta", team=self.team)
		self.s_hidden = Subject.objects.create(
			subject_name="Hidden", subject_slug="hidden", team=self.team
		)
		self.cat1 = TeamCategory.objects.create(
			team=self.team, category_name="Remyelination", category_slug="remyelination"
		)
		self.cat1.subjects.add(self.s1)
		self.cat2 = TeamCategory.objects.create(
			team=self.team, category_name="Mixed", category_slug="mixed"
		)
		self.cat2.subjects.add(self.s1, self.s_hidden)
		self.cat_empty = TeamCategory.objects.create(
			team=self.team, category_name="Empty", category_slug="empty"
		)
		# Anonymous callers see s1 and s2, never s_hidden.
		self.site = publish_subjects(self.s1, self.s2, organization=self.org)
		self.client = APIClient()

	def get(self, path, **params):
		cache.clear()
		return self.client.get(path, params)

	def ids(self, resp):
		self.assertEqual(resp.status_code, 200, resp.content)
		return [lab["id"] for lab in resp.json()["results"]]


class VisibilityTests(LabApiBase):
	def test_lab_with_visible_subject_link_is_listed(self):
		lab = make_lab(make_author())
		link_subject(lab, self.s1)
		self.assertEqual(self.ids(self.get("/labs/")), [lab.id])

	def test_lab_linked_only_to_hidden_subject_is_absent_and_404(self):
		lab = make_lab(make_author())
		link_subject(lab, self.s_hidden)
		self.assertEqual(self.ids(self.get("/labs/")), [])
		self.assertEqual(self.client.get(f"/labs/{lab.id}/").status_code, 404)

	def test_lab_without_links_is_absent(self):
		lab = make_lab(make_author())
		self.assertEqual(self.ids(self.get("/labs/")), [])
		self.assertEqual(self.client.get(f"/labs/{lab.id}/").status_code, 404)

	def test_hidden_links_never_appear(self):
		lab = make_lab(make_author())
		link_subject(lab, self.s1)
		link_subject(lab, self.s2, is_hidden=True)
		link_category(lab, self.cat1, is_hidden=True)
		body = self.client.get(f"/labs/{lab.id}/").json()
		self.assertEqual([s["id"] for s in body["subjects"]], [self.s1.id])
		self.assertEqual(body["categories"], [])

	def test_lab_with_only_hidden_links_is_absent(self):
		lab = make_lab(make_author())
		link_subject(lab, self.s1, is_hidden=True)
		link_category(lab, self.cat1, is_hidden=True)
		self.assertEqual(self.ids(self.get("/labs/")), [])
		self.assertEqual(self.client.get(f"/labs/{lab.id}/").status_code, 404)

	def test_lab_visible_through_category_link_alone(self):
		lab = make_lab(make_author())
		link_category(lab, self.cat1)
		self.assertEqual(self.ids(self.get("/labs/")), [lab.id])

	def test_each_site_sees_only_its_own_links(self):
		site_a = private_site_publishing(self.s1, organization=self.org)
		other_org = Organization.objects.create(name="Other Org", slug="other-org")
		site_b = private_site_publishing(self.s2, organization=other_org)
		lab = make_lab(make_author())
		link_subject(lab, self.s1, articles=11)
		link_subject(lab, self.s2, articles=22)
		only_b = make_lab(make_author())
		link_subject(only_b, self.s2)

		for site, org, subject, other in (
			(site_a, self.org, self.s1, self.s2),
			(site_b, other_org, self.s2, self.s1),
		):
			client = APIClient()
			client.credentials(HTTP_AUTHORIZATION=_api_scheme(org, site).api_key)
			resp = client.get("/labs/")
			self.assertEqual(resp.status_code, 200)
			for entry in resp.json()["results"]:
				subject_ids = [s["id"] for s in entry["subjects"]]
				self.assertIn(subject.id, subject_ids)
				self.assertNotIn(other.id, subject_ids)
			cache.clear()

		client = APIClient()
		client.credentials(HTTP_AUTHORIZATION=_api_scheme(self.org, site_a).api_key)
		self.assertEqual(client.get(f"/labs/{only_b.id}/").status_code, 404)

	def test_private_site_key_sees_that_sites_labs(self):
		hidden_site = private_site_publishing(self.s_hidden, organization=self.org)
		lab = make_lab(make_author())
		link_subject(lab, self.s_hidden)
		self.assertEqual(self.ids(self.get("/labs/")), [])
		client = APIClient()
		client.credentials(HTTP_AUTHORIZATION=_api_scheme(self.org, hidden_site).api_key)
		resp = client.get("/labs/")
		self.assertEqual([entry["id"] for entry in resp.json()["results"]], [lab.id])


class CategoryRuleTests(LabApiBase):
	def test_category_with_a_hidden_subject_is_not_a_visible_link(self):
		lab = make_lab(make_author())
		link_category(lab, self.cat2)
		self.assertEqual(self.ids(self.get("/labs/")), [])
		self.assertEqual(self.client.get(f"/labs/{lab.id}/").status_code, 404)
		self.assertEqual(self.ids(self.get("/labs/", category_id=self.cat2.id)), [])

	def test_mixed_category_does_not_leak_into_a_visible_lab(self):
		lab = make_lab(make_author())
		link_subject(lab, self.s1)
		link_category(lab, self.cat2, articles=99)
		link_category(lab, self.cat1, articles=5)
		body = self.client.get(f"/labs/{lab.id}/").json()
		self.assertEqual([c["id"] for c in body["categories"]], [self.cat1.id])
		self.assertEqual(body["categories"][0]["articles"], 5)

	def test_lab_shows_only_the_fully_visible_category(self):
		lab = make_lab(make_author())
		link_category(lab, self.cat2)
		link_category(lab, self.cat1)
		self.assertEqual(self.ids(self.get("/labs/")), [lab.id])
		body = self.client.get(f"/labs/{lab.id}/").json()
		self.assertEqual([c["id"] for c in body["categories"]], [self.cat1.id])

	def test_category_without_subjects_is_never_visible(self):
		lab = make_lab(make_author())
		link_category(lab, self.cat_empty)
		self.assertEqual(self.ids(self.get("/labs/")), [])


class ScopeFunctionTests(LabApiBase):
	"""scope=None skips only subject scoping; an empty scope shows nothing."""

	def setUp(self):
		super().setUp()
		self.lab = make_lab(make_author())
		link_subject(self.lab, self.s_hidden)
		link_subject(self.lab, self.s1, is_hidden=True)
		link_category(self.lab, self.cat2)
		link_category(self.lab, self.cat1, is_hidden=True)
		self.empty_cat_lab = make_lab(make_author())
		link_category(self.empty_cat_lab, self.cat_empty)

	def test_none_scope_shows_every_unhidden_link(self):
		self.assertEqual({l.subject_id for l in visible_subject_links(None)}, {self.s_hidden.id})
		self.assertEqual({l.category_id for l in visible_category_links(None)}, {self.cat2.id})
		labs = Lab.objects.filter(visible_labs_q(None))
		self.assertEqual(list(labs), [self.lab])

	def test_empty_scope_shows_nothing(self):
		self.assertFalse(visible_subject_links(set()).exists())
		self.assertFalse(visible_category_links(set()).exists())
		self.assertFalse(Lab.objects.filter(visible_labs_q(set())).exists())

	def _call(self, scope, path="/labs/"):
		request = APIRequestFactory().get(path)
		if scope is not ...:
			request.visible_subject_ids = scope
		return LabViewSet.as_view({"get": "list"})(request)

	def test_view_without_middleware_attribute_is_unscoped(self):
		cache.clear()
		resp = self._call(...)
		self.assertEqual([l["id"] for l in resp.data["results"]], [self.lab.id])
		entry = resp.data["results"][0]
		self.assertEqual([s["id"] for s in entry["subjects"]], [self.s_hidden.id])
		self.assertEqual([c["id"] for c in entry["categories"]], [self.cat2.id])

	def test_view_with_empty_scope_shows_nothing(self):
		cache.clear()
		resp = self._call(set())
		self.assertEqual(resp.data["results"], [])


class FieldTests(LabApiBase):
	def test_full_object_shape(self):
		inst = Institution.objects.create(
			openalex_id="I141596103", ror="01c27hj86", name="Universidade de Lisboa", country_code="PT"
		)
		head = make_author("Ana", "Silva", orcid="0000-0002-1825-0097")
		lab = make_lab(head, institution=inst, members=2, is_manual=True, member_count=2)
		link_subject(lab, self.s1, articles=31, relevant=6, recent=9, year=2026)
		link_category(lab, self.cat1, articles=12, relevant=4, recent=3, year=2025)
		body = self.client.get(f"/labs/{lab.id}/").json()
		self.assertEqual(body["id"], lab.id)
		self.assertEqual(body["name"], "Ana Silva lab")
		self.assertTrue(body["curated"])
		self.assertEqual(
			body["head"],
			{"author_id": head.author_id, "full_name": "Ana Silva", "orcid": "0000-0002-1825-0097"},
		)
		self.assertEqual(
			body["institution"],
			{
				"name": "Universidade de Lisboa",
				"country_code": "PT",
				"openalex_id": "I141596103",
				"ror": "01c27hj86",
			},
		)
		self.assertEqual(body["member_count"], 2)
		self.assertEqual(len(body["members"]), 2)
		self.assertEqual(
			body["subjects"],
			[
				{
					"id": self.s1.id,
					"name": "Alpha",
					"articles": 31,
					"relevant_articles": 6,
					"recent_articles": 9,
					"latest_article_year": 2026,
				}
			],
		)
		self.assertEqual(
			body["categories"],
			[
				{
					"id": self.cat1.id,
					"name": "Remyelination",
					"slug": "remyelination",
					"articles": 12,
					"relevant_articles": 4,
					"recent_articles": 3,
					"latest_article_year": 2025,
				}
			],
		)
		self.assertEqual(
			set(body),
			{
				"id", "name", "curated", "head", "institution", "member_count", "members",
				"subjects", "categories", "topics_last_changed",
			},
		)

	def test_no_forbidden_field_anywhere(self):
		inst = Institution.objects.create(openalex_id="I1", name="Inst", country_code="PT")
		head = Authors.objects.create(
			given_name="Ana",
			family_name="Head",
			full_name="Ana Head",
			current_affiliation="SECRET-AFFILIATION",
			orcid_verified_email=True,
			biography="SECRET-BIO",
		)
		lab = make_lab(
			head,
			institution=inst,
			members=2,
			collaborators=2,
			notes="SECRET-NOTE",
			needs_review=True,
			review_reason="SECRET-REASON",
		)
		link_subject(lab, self.s1)
		link_category(lab, self.cat1)
		collaborator = lab.memberships.filter(role="collaborator").first().author

		for resp in (self.get("/labs/"), self.client.get(f"/labs/{lab.id}/")):
			self.assertEqual(resp.status_code, 200)
			text = resp.content.decode()

			def walk(node):
				if isinstance(node, dict):
					for key, value in node.items():
						self.assertNotIn(key, FORBIDDEN_KEYS, f"forbidden key {key}")
						walk(value)
				elif isinstance(node, list):
					for item in node:
						walk(item)

			walk(json.loads(text))
			for secret in ("SECRET-", "@example.com", collaborator.full_name):
				self.assertNotIn(secret, text)

	def test_headless_lab_without_institution_and_blank_name(self):
		lab = make_lab(None)
		link_subject(lab, self.s1)
		body = self.client.get(f"/labs/{lab.id}/").json()
		self.assertIsNone(body["head"])
		self.assertIsNone(body["institution"])
		self.assertEqual(body["name"], f"Lab {lab.id}")

	def test_name_is_never_blank(self):
		named = make_lab(make_author(), name="  Silva group  ")
		headed = make_lab(make_author("Rui", "Costa"))
		unnamed_head = make_lab(Authors.objects.create(given_name="Eva", family_name="Lima", full_name=""))
		for lab in (named, headed, unnamed_head):
			link_subject(lab, self.s1)
		names = {lab["id"]: lab["name"] for lab in self.get("/labs/").json()["results"]}
		self.assertEqual(names[named.id], "Silva group")
		self.assertEqual(names[headed.id], "Rui Costa lab")
		self.assertEqual(names[unnamed_head.id], "Eva Lima lab")
		head_json = self.client.get(f"/labs/{unnamed_head.id}/").json()["head"]
		self.assertEqual(head_json["full_name"], "Eva Lima")

	def test_blank_ids_are_null(self):
		inst = Institution.objects.create(openalex_id="I2", name="Bare", ror="", country_code="")
		head = Authors.objects.create(given_name="No", family_name="Orcid", full_name="No Orcid", ORCID="")
		lab = make_lab(head, institution=inst)
		link_subject(lab, self.s1)
		body = self.client.get(f"/labs/{lab.id}/").json()
		self.assertIsNone(body["head"]["orcid"])
		self.assertIsNone(body["institution"]["ror"])
		self.assertIsNone(body["institution"]["country_code"])

	def test_members_exclude_head_and_collaborators_and_are_ordered(self):
		head = make_author()
		lab = make_lab(head, collaborators=2, member_count=3)
		for shared in (1, 7, 7):
			LabMembership.objects.create(
				author=make_author(), lab=lab, role="member", shared_articles=shared
			)
		link_subject(lab, self.s1)
		body = self.client.get(f"/labs/{lab.id}/").json()
		self.assertEqual(body["member_count"], 3)
		member_ids = [m["author_id"] for m in body["members"]]
		self.assertEqual(len(member_ids), 3)
		self.assertNotIn(head.author_id, member_ids)
		expected = [
			m.author_id
			for m in lab.memberships.filter(role="member").order_by("-shared_articles", "author_id")
		]
		self.assertEqual(member_ids, expected)

	def test_links_are_ranked(self):
		lab = make_lab(make_author())
		link_subject(lab, self.s1, articles=5, relevant=1)
		link_subject(lab, self.s2, articles=3, relevant=4)
		body = self.client.get(f"/labs/{lab.id}/").json()
		self.assertEqual([s["id"] for s in body["subjects"]], [self.s2.id, self.s1.id])

	def test_topics_last_changed_is_latest_visible_computed_at(self):
		lab = make_lab(make_author())
		early = now() - timedelta(days=10)
		late = now() - timedelta(days=1)
		newest_but_hidden = now()
		link_subject(lab, self.s1, computed_at=early)
		link_category(lab, self.cat1, computed_at=late)
		link_subject(lab, self.s2, computed_at=newest_but_hidden, is_hidden=True)
		body = self.client.get(f"/labs/{lab.id}/").json()
		self.assertEqual(body["topics_last_changed"][:19], late.strftime("%Y-%m-%dT%H:%M:%S"))

	def test_topics_last_changed_null_without_stamps(self):
		lab = make_lab(make_author())
		link_subject(lab, self.s1)
		self.assertIsNone(self.client.get(f"/labs/{lab.id}/").json()["topics_last_changed"])

	def test_pagination_envelope(self):
		for _ in range(3):
			link_subject(make_lab(make_author()), self.s1)
		for _ in range(9):
			link_subject(make_lab(make_author()), self.s1)
		body = self.get("/labs/").json()
		self.assertEqual(body["count"], 12)
		self.assertEqual(len(body["results"]), 10)
		self.assertIsNotNone(body["next"])
		self.assertIn("previous", body)


class FilterTests(LabApiBase):
	def setUp(self):
		super().setUp()
		self.pt = Institution.objects.create(openalex_id="I10", name="Universidade do Porto", country_code="PT")
		self.us = Institution.objects.create(openalex_id="I11", name="Mayo Clinic", country_code="US")
		self.head_a = make_author("Ana", "Silva")
		self.head_b = make_author("Bob", "Jones")
		self.head_c = make_author("Cleo", "Brown")
		self.lab_a = make_lab(self.head_a, institution=self.pt, members=1, name="Neuro Group")
		self.lab_b = make_lab(self.head_b, institution=self.us, members=2)
		self.lab_c = make_lab(self.head_c, members=3)
		link_subject(self.lab_a, self.s1, articles=30, relevant=5, recent=8)
		link_subject(self.lab_b, self.s1, articles=20, relevant=9, recent=2)
		link_subject(self.lab_b, self.s2, articles=7, relevant=1, recent=1)
		link_subject(self.lab_c, self.s2, articles=40, relevant=0, recent=0)
		link_category(self.lab_a, self.cat1, articles=3, relevant=3, recent=3)
		link_category(self.lab_b, self.cat1, articles=9, relevant=1, recent=0)

	def test_default_order_is_name_then_id(self):
		# "Ana Silva lab" is named "Neuro Group"; Bob Jones lab, Cleo Brown lab
		self.assertEqual(
			self.ids(self.get("/labs/")), [self.lab_b.id, self.lab_c.id, self.lab_a.id]
		)

	def test_subject_filter(self):
		self.assertEqual(
			set(self.ids(self.get("/labs/", subject_id=self.s1.id))), {self.lab_a.id, self.lab_b.id}
		)

	def test_subject_filter_unknown_or_invisible_is_empty(self):
		link_subject(self.lab_a, self.s_hidden)
		self.assertEqual(self.ids(self.get("/labs/", subject_id=self.s_hidden.id)), [])
		self.assertEqual(self.ids(self.get("/labs/", subject_id=999999)), [])

	def test_hidden_link_does_not_match_subject_filter(self):
		LabSubject.objects.filter(lab=self.lab_a, subject=self.s1).update(is_hidden=True)
		self.assertEqual(self.ids(self.get("/labs/", subject_id=self.s1.id)), [self.lab_b.id])

	def test_category_filter(self):
		self.assertEqual(
			set(self.ids(self.get("/labs/", category_id=self.cat1.id))), {self.lab_a.id, self.lab_b.id}
		)

	def test_category_with_hidden_subject_filter_is_empty(self):
		link_category(self.lab_a, self.cat2)
		self.assertEqual(self.ids(self.get("/labs/", category_id=self.cat2.id)), [])

	def test_both_topic_filters_mean_both(self):
		self.assertEqual(
			set(self.ids(self.get("/labs/", subject_id=self.s2.id, category_id=self.cat1.id))),
			{self.lab_b.id},
		)

	def test_search_matches_lab_head_and_institution(self):
		self.assertEqual(self.ids(self.get("/labs/", search="neuro")), [self.lab_a.id])
		self.assertEqual(self.ids(self.get("/labs/", search="BOB JONES")), [self.lab_b.id])
		self.assertEqual(self.ids(self.get("/labs/", search="jones")), [self.lab_b.id])
		self.assertEqual(self.ids(self.get("/labs/", search="mayo")), [self.lab_b.id])
		self.assertEqual(self.ids(self.get("/labs/", search="nomatch")), [])

	def test_search_matches_head_name_even_when_lab_is_named(self):
		self.assertEqual(self.ids(self.get("/labs/", search="silva")), [self.lab_a.id])

	def test_search_matches_a_head_with_no_stored_full_name(self):
		Authors.objects.filter(pk=self.head_a.pk).update(full_name="")
		self.assertEqual(self.ids(self.get("/labs/", search="silva")), [self.lab_a.id])

	def test_the_python_and_sql_names_agree_on_a_blank_full_name(self):
		head = make_author(given="Rui", family="Costa")
		Authors.objects.filter(pk=head.pk).update(full_name="  ")
		lab = make_lab(head=head)
		annotated = Lab.objects.annotate(display_name=lab_display_name_expr()).get(pk=lab.pk)
		lab.head.refresh_from_db()
		self.assertEqual(annotated.display_name, "Rui Costa lab")
		self.assertEqual(lab_display_name(lab), "Rui Costa lab")

	def test_country_is_exact_and_case_insensitive(self):
		self.assertEqual(self.ids(self.get("/labs/", country="pt")), [self.lab_a.id])
		self.assertEqual(self.ids(self.get("/labs/", country="P")), [])

	def test_author_filter_counts_head_and_member_not_collaborator(self):
		member = self.lab_b.memberships.filter(role="member").first().author
		self.assertEqual(self.ids(self.get("/labs/", author_id=self.head_a.author_id)), [self.lab_a.id])
		self.assertEqual(self.ids(self.get("/labs/", author_id=member.author_id)), [self.lab_b.id])
		collab = make_author()
		LabMembership.objects.create(author=collab, lab=self.lab_a, role="collaborator")
		self.assertEqual(self.ids(self.get("/labs/", author_id=collab.author_id)), [])

	def test_min_recent_articles_uses_the_subject_link(self):
		self.assertEqual(
			self.ids(self.get("/labs/", subject_id=self.s1.id, min_recent_articles=5)), [self.lab_a.id]
		)
		self.assertEqual(
			set(self.ids(self.get("/labs/", subject_id=self.s1.id, min_recent_articles=2))),
			{self.lab_a.id, self.lab_b.id},
		)

	def test_min_recent_articles_with_both_uses_the_category(self):
		# lab_b: subject s1 has 2 recent, category cat1 has 0 -> category wins.
		resp = self.get(
			"/labs/", subject_id=self.s1.id, category_id=self.cat1.id, min_recent_articles=1
		)
		self.assertEqual(self.ids(resp), [self.lab_a.id])

	def test_min_recent_articles_needs_a_topic(self):
		self.assertEqual(self.get("/labs/", min_recent_articles=1).status_code, 400)

	def test_non_integer_ids_are_400(self):
		for param in ("subject_id", "category_id", "author_id", "min_recent_articles"):
			self.assertEqual(self.get("/labs/", **{param: "abc"}).status_code, 400, param)
		self.assertEqual(self.get("/labs/", subject_id=self.s1.id, min_recent_articles=-1).status_code, 400)

	def test_filters_combine(self):
		resp = self.get("/labs/", subject_id=self.s1.id, country="US")
		self.assertEqual(self.ids(resp), [self.lab_b.id])


class OrderingTests(FilterTests):
	def order(self, ordering, **params):
		return self.ids(self.get("/labs/", ordering=ordering, **params))

	def test_name_descending(self):
		self.assertEqual(self.order("-name"), [self.lab_a.id, self.lab_c.id, self.lab_b.id])

	def test_member_count(self):
		Lab.objects.filter(pk=self.lab_a.pk).update(member_count=1)
		Lab.objects.filter(pk=self.lab_b.pk).update(member_count=2)
		Lab.objects.filter(pk=self.lab_c.pk).update(member_count=3)
		self.assertEqual(self.order("member_count"), [self.lab_a.id, self.lab_b.id, self.lab_c.id])
		self.assertEqual(self.order("-member_count"), [self.lab_c.id, self.lab_b.id, self.lab_a.id])

	def test_count_orderings_on_subject(self):
		s = {"subject_id": self.s2.id}
		self.assertEqual(self.order("-articles", **s), [self.lab_c.id, self.lab_b.id])
		self.assertEqual(self.order("articles", **s), [self.lab_b.id, self.lab_c.id])
		self.assertEqual(self.order("-relevant_articles", **s), [self.lab_b.id, self.lab_c.id])
		self.assertEqual(self.order("-recent_articles", **s), [self.lab_b.id, self.lab_c.id])

	def test_count_orderings_with_both_use_the_category(self):
		both = {"subject_id": self.s1.id, "category_id": self.cat1.id}
		# subject counts say lab_a first by articles (30 vs 20); category: 3 vs 9
		self.assertEqual(self.order("-articles", **both), [self.lab_b.id, self.lab_a.id])

	def test_count_orderings_need_a_topic(self):
		for field in ("articles", "relevant_articles", "recent_articles", "-articles"):
			self.assertEqual(self.get("/labs/", ordering=field).status_code, 400, field)

	def test_unknown_ordering_is_400(self):
		self.assertEqual(self.get("/labs/", ordering="head").status_code, 400)

	def test_ties_fall_back_to_id_and_pages_are_stable(self):
		labs = []
		for _ in range(12):
			lab = make_lab(make_author(), name="Same")
			link_subject(lab, self.s1, articles=1, relevant=1, recent=1)
			labs.append(lab.id)
		same = [l for l in labs]
		seen = []
		for page in (1, 2):
			resp = self.get("/labs/", ordering="-articles", subject_id=self.s1.id, page=page)
			seen.extend(self.ids(resp))
		tied = [i for i in seen if i in same]
		self.assertEqual(tied, sorted(same))
		self.assertEqual(len(seen), len(set(seen)))
		resp = self.get("/labs/", ordering="name")
		names = [(l["name"], l["id"]) for l in resp.json()["results"]]
		self.assertEqual(names, sorted(names, key=lambda n: (n[0].lower(), n[1])))


class QueryCountTests(LabApiBase):
	def _build(self, n, offset=0):
		for i in range(n):
			inst = Institution.objects.create(openalex_id=f"IQ{offset + i}", name=f"Inst {offset + i}")
			lab = make_lab(make_author(), institution=inst, members=4, collaborators=2)
			link_subject(lab, self.s1)
			link_subject(lab, self.s2)
			link_category(lab, self.cat1)
			link_category(lab, self.cat2)  # hidden from the caller

	def _queries(self, path):
		cache.clear()
		with CaptureQueriesContext(connection) as ctx:
			resp = self.client.get(path)
		self.assertEqual(resp.status_code, 200)
		return len(ctx), len(resp.json()["results"])

	def test_query_count_does_not_grow_with_labs_links_or_members(self):
		self._build(2)
		self._queries("/labs/")  # warm one-time caches (sites framework, content types)
		small, small_n = self._queries("/labs/")
		self._build(18, offset=2)
		large, large_n = self._queries("/labs/")
		self.assertEqual((small_n, large_n), (2, 10))  # 20 labs exist: a full page of 10
		self.assertEqual(small, large)

	def test_filtered_query_count_is_constant_too(self):
		path = f"/labs/?subject_id={self.s1.id}&category_id={self.cat1.id}&ordering=-articles"
		self._build(2)
		self._queries(path)
		small, _ = self._queries(path)
		self._build(18, offset=2)
		large, n = self._queries(path)
		self.assertEqual(n, 10)
		self.assertEqual(small, large)

	def test_detail_query_count(self):
		self._build(2)
		lab = Lab.objects.first()
		cache.clear()
		self.client.get(f"/labs/{lab.id}/")
		with CaptureQueriesContext(connection) as ctx:
			self.assertEqual(self.client.get(f"/labs/{lab.id}/").status_code, 200)
		self.assertLessEqual(len(ctx), 12)
