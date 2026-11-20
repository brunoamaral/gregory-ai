"""Tests for ``?include=lab`` on ``/authors/``, ``/authors/{id}/`` and ``/authors/search/``.

Run with:
    pytest api/tests/test_author_include_lab.py
"""

import json

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from api.tests.test_labs_api import (
	LabApiBase,
	_api_scheme,
	link_category,
	link_subject,
	make_author,
	make_lab,
)
from api.tests.visibility_helpers import private_site_publishing
from gregory.models import Articles, LabMembership, OrganizationApiSettings


class AuthorLabBase(LabApiBase):
	def setUp(self):
		super().setUp()
		# /authors/search/ checks the team's organisation against the caller's scope.
		OrganizationApiSettings.objects.filter(organization=self.org).update(make_api_public=True)

	def publish(self, *authors, subject=None):
		"""Give the authors an article on a visible subject, so /authors/ lists them."""
		article = Articles.objects.create(
			title=f"Paper {authors[0].author_id}",
			link=f"https://example.com/p/{authors[0].author_id}",
			published_date=timezone.now(),
		)
		article.teams.add(self.team)
		article.subjects.add(subject or self.s1)
		article.authors.add(*authors)
		return article

	def by_id(self, resp):
		self.assertEqual(resp.status_code, 200, resp.content)
		return {row["author_id"]: row for row in resp.json()["results"]}


class OffByDefaultTests(AuthorLabBase):
	def test_no_key_without_include(self):
		head = make_author()
		lab = make_lab(head)
		link_subject(lab, self.s1)
		self.publish(head)
		rows = self.by_id(self.get("/authors/"))
		self.assertNotIn("lab", rows[head.author_id])
		detail = self.get(f"/authors/{head.author_id}/").json()
		self.assertNotIn("lab", detail)

	def test_no_lab_query_without_include(self):
		head = make_author()
		lab = make_lab(head)
		link_subject(lab, self.s1)
		self.publish(head)
		cache.clear()
		with CaptureQueriesContext(connection) as ctx:
			self.client.get("/authors/")
		self.assertFalse([q for q in ctx.captured_queries if "gregory_lab" in q["sql"]])

	def test_include_editorial_alone_has_no_lab_key(self):
		head = make_author()
		link_subject(make_lab(head), self.s1)
		self.publish(head)
		rows = self.by_id(self.get("/authors/", include="editorial"))
		self.assertNotIn("lab", rows[head.author_id])


class RoleTests(AuthorLabBase):
	def setUp(self):
		super().setUp()
		self.head = make_author(given="Ana", family="Silva")
		self.lab = make_lab(self.head, members=1, collaborators=1)
		link_subject(self.lab, self.s1)
		memberships = {m.role: m for m in self.lab.memberships.select_related("author")}
		self.member = memberships["member"].author
		self.collab = memberships["collaborator"].author
		self.publish(self.head, self.member, self.collab)

	def test_head_and_member(self):
		rows = self.by_id(self.get("/authors/", include="lab"))
		expected_head = {"author_id": self.head.author_id, "full_name": "Ana Silva"}
		self.assertEqual(
			rows[self.head.author_id]["lab"],
			{"id": self.lab.id, "name": "Ana Silva lab", "role": "head", "head": expected_head},
		)
		self.assertEqual(
			rows[self.member.author_id]["lab"],
			{"id": self.lab.id, "name": "Ana Silva lab", "role": "member", "head": expected_head},
		)

	def test_named_lab_uses_its_name(self):
		self.lab.name = "Silva Remyelination Group"
		self.lab.save()
		rows = self.by_id(self.get("/authors/", include="lab"))
		self.assertEqual(rows[self.head.author_id]["lab"]["name"], "Silva Remyelination Group")

	def test_collaborator_is_null(self):
		rows = self.by_id(self.get("/authors/", include="lab"))
		self.assertIn("lab", rows[self.collab.author_id])
		self.assertIsNone(rows[self.collab.author_id]["lab"])

	def test_author_with_no_membership_is_null(self):
		loner = make_author()
		self.publish(loner)
		rows = self.by_id(self.get("/authors/", include="lab"))
		self.assertIn("lab", rows[loner.author_id])
		self.assertIsNone(rows[loner.author_id]["lab"])

	def test_author_pinned_to_no_lab_is_null(self):
		pinned = make_author()
		LabMembership.objects.create(author=pinned, lab=None, role="member", source="manual")
		self.publish(pinned)
		rows = self.by_id(self.get("/authors/", include="lab"))
		self.assertIsNone(rows[pinned.author_id]["lab"])

	def test_headless_lab_member_has_null_head(self):
		lab = make_lab(None, name="Orphan lab")
		member = make_author()
		LabMembership.objects.create(author=member, lab=lab, role="member")
		link_subject(lab, self.s1)
		self.publish(member)
		rows = self.by_id(self.get("/authors/", include="lab"))
		self.assertEqual(
			rows[member.author_id]["lab"],
			{"id": lab.id, "name": "Orphan lab", "role": "member", "head": None},
		)

	def test_detail(self):
		body = self.get(f"/authors/{self.member.author_id}/", include="lab").json()
		self.assertEqual(body["lab"]["role"], "member")
		self.assertEqual(body["lab"]["head"]["author_id"], self.head.author_id)
		collab = self.get(f"/authors/{self.collab.author_id}/", include="lab").json()
		self.assertIsNone(collab["lab"])

	def test_only_the_documented_keys(self):
		rows = self.by_id(self.get("/authors/", include="lab"))
		lab = rows[self.head.author_id]["lab"]
		self.assertEqual(set(lab), {"id", "name", "role", "head"})
		self.assertEqual(set(lab["head"]), {"author_id", "full_name"})
		text = json.dumps(lab)
		for word in ("notes", "needs_review", "email", "collaborator", "orcid"):
			self.assertNotIn(word, text)

	def test_works_with_other_filters_and_sorting(self):
		rows = self.by_id(self.get("/authors/", include="lab", sort_by="article_count"))
		self.assertEqual(rows[self.head.author_id]["lab"]["role"], "head")
		rows = self.by_id(
			self.get("/authors/", include="lab", subject_id=self.s1.id, team_id=self.team.id)
		)
		self.assertEqual(rows[self.member.author_id]["lab"]["role"], "member")


class VisibilityTests(AuthorLabBase):
	def test_lab_invisible_to_caller_is_null(self):
		head = make_author()
		lab = make_lab(head)
		link_subject(lab, self.s_hidden)  # anonymous callers cannot see this subject
		self.publish(head)  # the author is listed through a visible subject
		rows = self.by_id(self.get("/authors/", include="lab"))
		self.assertIsNone(rows[head.author_id]["lab"])

	def test_lab_linked_only_to_mixed_category_is_null(self):
		head = make_author()
		lab = make_lab(head)
		link_category(lab, self.cat2)  # one visible and one hidden subject
		self.publish(head)
		rows = self.by_id(self.get("/authors/", include="lab"))
		self.assertIsNone(rows[head.author_id]["lab"])

	def test_hidden_link_does_not_make_a_lab_visible(self):
		head = make_author()
		lab = make_lab(head)
		link_subject(lab, self.s1, is_hidden=True)
		self.publish(head)
		rows = self.by_id(self.get("/authors/", include="lab"))
		self.assertIsNone(rows[head.author_id]["lab"])

	def test_private_site_key_sees_its_lab(self):
		private = private_site_publishing(self.s_hidden, organization=self.org)
		head = make_author()
		lab = make_lab(head)
		link_subject(lab, self.s_hidden)
		self.publish(head, subject=self.s_hidden)
		client = APIClient()
		client.credentials(HTTP_AUTHORIZATION=_api_scheme(self.org, private).api_key)
		resp = client.get("/authors/", {"include": "lab"})
		rows = {r["author_id"]: r for r in resp.json()["results"]}
		self.assertEqual(rows[head.author_id]["lab"]["id"], lab.id)
		# The anonymous caller cannot even see the author.
		self.assertNotIn(head.author_id, self.by_id(self.get("/authors/", include="lab")))

	def test_agrees_with_labs_endpoint(self):
		visible, invisible = make_author(), make_author()
		lab_ok, lab_hidden = make_lab(visible), make_lab(invisible)
		link_subject(lab_ok, self.s1)
		link_subject(lab_hidden, self.s_hidden)
		self.publish(visible, invisible)
		listed = {lab["id"] for lab in self.get("/labs/").json()["results"]}
		rows = self.by_id(self.get("/authors/", include="lab"))
		for author, lab in ((visible, lab_ok), (invisible, lab_hidden)):
			shown = rows[author.author_id]["lab"]
			self.assertEqual(shown is not None, lab.id in listed)
			if shown:
				body = self.client.get(f"/labs/{lab.id}/").json()
				self.assertEqual(shown["name"], body["name"])


class IncludeParsingTests(AuthorLabBase):
	def setUp(self):
		super().setUp()
		self.head = make_author()
		link_subject(make_lab(self.head), self.s1)
		self.publish(self.head)

	def test_editorial_and_lab_together(self):
		rows = self.by_id(self.get("/authors/", include="editorial,lab"))
		self.assertEqual(rows[self.head.author_id]["lab"]["role"], "head")

	def test_unknown_value_is_400(self):
		self.assertEqual(self.get("/authors/", include="lab,nope").status_code, 400)
		self.assertEqual(
			self.get(f"/authors/{self.head.author_id}/", include="nope").status_code, 400
		)

	def test_csv_leaves_it_out(self):
		resp = self.get("/authors/", include="lab", format="csv")
		self.assertEqual(resp.status_code, 200)
		self.assertNotIn("lab", resp.content.decode().splitlines()[0].split(","))

	def test_search_csv_leaves_it_out(self):
		resp = self.get(
			"/authors/search/",
			team_id=self.team.id,
			subject_id=self.s1.id,
			include="lab",
			format="csv",
		)
		self.assertEqual(resp.status_code, 200)
		self.assertNotIn("lab", resp.content.decode().splitlines()[0].split(","))

	def test_other_endpoints_ignore_lab(self):
		for path in ("/articles/", "/trials/"):
			resp = self.get(path, include="lab")
			self.assertEqual(resp.status_code, 200, path)
			for row in resp.json()["results"]:
				self.assertNotIn("lab", row)


class SearchTests(AuthorLabBase):
	def setUp(self):
		super().setUp()
		self.head = make_author(given="Ana", family="Silva")
		self.lab = make_lab(self.head, members=1)
		link_subject(self.lab, self.s1)
		self.member = self.lab.memberships.get(role="member").author
		self.publish(self.head, self.member)
		self.params = {"team_id": self.team.id, "subject_id": self.s1.id}

	def test_get_search(self):
		rows = self.by_id(self.get("/authors/search/", include="lab", **self.params))
		self.assertEqual(rows[self.head.author_id]["lab"]["role"], "head")
		self.assertEqual(rows[self.member.author_id]["lab"]["role"], "member")

	def test_get_search_off_by_default(self):
		rows = self.by_id(self.get("/authors/search/", **self.params))
		self.assertNotIn("lab", rows[self.head.author_id])

	def test_post_body_include(self):
		cache.clear()
		resp = self.client.post(
			"/authors/search/", {**self.params, "include": "lab"}, format="json"
		)
		rows = self.by_id(resp)
		self.assertEqual(rows[self.head.author_id]["lab"]["role"], "head")
		cache.clear()
		resp = self.client.post("/authors/search/", self.params, format="json")
		self.assertNotIn("lab", self.by_id(resp)[self.head.author_id])

	def test_post_unknown_include_is_400(self):
		resp = self.client.post(
			"/authors/search/", {**self.params, "include": "nope"}, format="json"
		)
		self.assertEqual(resp.status_code, 400)


class QueryCountTests(AuthorLabBase):
	def _make(self, n):
		authors = []
		for _ in range(n):
			head = make_author()
			lab = make_lab(head, members=1, name="Lab")
			link_subject(lab, self.s1)
			authors += [head, lab.memberships.get(role="member").author]
		self.publish(*authors)

	def _count(self, path, **params):
		cache.clear()
		with CaptureQueriesContext(connection) as ctx:
			resp = self.client.get(path, {"include": "lab", "page_size": 100, **params})
		self.assertEqual(resp.status_code, 200)
		self.last_sql = [q["sql"] for q in ctx.captured_queries]
		self.count_sql = [q["sql"] for q in ctx.captured_queries if "COUNT(*)" in q["sql"]]
		return len(ctx), len(resp.json()["results"])

	def test_constant_between_2_and_10_authors(self):
		# /authors/ pages at 10 and takes no page_size, so 10 is its fullest page.
		self._make(1)
		self._count("/authors/")  # warm the one-off Site lookup, which is not ours
		small, n_small = self._count("/authors/")
		sm = self.last_sql
		self._make(4)
		large, n_large = self._count("/authors/")
		self.assertEqual((n_small, n_large), (2, 10))
		# The paginator's COUNT(*) must not pick up the lab join or subquery.
		self.assertNotIn("lab_memberships", self.count_sql[0])
		self.assertEqual(small, large, "\n".join(self.last_sql) + "\n----\n" + "\n".join(sm))

	def test_search_constant_between_2_and_20_authors(self):
		params = {"team_id": self.team.id, "subject_id": self.s1.id}
		self._make(1)
		self._count("/authors/search/", **params)  # warm the one-off Site lookup
		small, _ = self._count("/authors/search/", **params)
		self._make(9)
		large, n = self._count("/authors/search/", **params)
		self.assertEqual(n, 20)
		self.assertEqual(small, large)
