"""Tests for ``lab_id`` on ``GET /articles/`` and ``/articles/search/``.

Run with:
    pytest api/tests/test_articles_lab_filter.py
"""

import csv
import io
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from api.tests.test_labs_api import _api_scheme, link_subject, make_author, make_lab
from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from gregory.models import (
	Articles,
	LabMembership,
	OrganizationApiSettings,
	Subject,
	Team,
	TeamCategory,
)
from gregory.services.lab_topics import apply_topic_links, compute_topic_links
from organizations.models import Organization


class LabFilterBase(TestCase):
	def setUp(self):
		cache.clear()
		self.org = Organization.objects.create(name="Lab Filter Org", slug="lab-filter-org")
		# /articles/search/ 404s a team whose organisation the caller cannot reach.
		OrganizationApiSettings.objects.filter(organization=self.org).update(make_api_public=True)
		self.team = Team.objects.create(
			organization=self.org, name="Lab Filter Team", slug="lab-filter-team"
		)
		self.s1 = Subject.objects.create(subject_name="Alpha", subject_slug="lf-alpha", team=self.team)
		self.s2 = Subject.objects.create(subject_name="Beta", subject_slug="lf-beta", team=self.team)
		self.s_hidden = Subject.objects.create(
			subject_name="Hidden", subject_slug="lf-hidden", team=self.team
		)
		self.cat = TeamCategory.objects.create(
			team=self.team, category_name="Remyelination", category_slug="lf-remyelination"
		)
		self.cat.subjects.add(self.s1)
		publish_subjects(self.s1, self.s2, organization=self.org)

		self.head = make_author("Hedda")
		self.lab = make_lab(self.head, members=2, collaborators=1)
		self.members = [
			m.author for m in self.lab.memberships.filter(role="member").order_by("pk")
		]
		self.collaborator = self.lab.memberships.get(role="collaborator").author
		link_subject(self.lab, self.s1)
		self.client = APIClient()
		self._seq = 0

	def article(self, *authors, subjects=None, days_ago=0, title=None, categories=()):
		self._seq += 1
		article = Articles.objects.create(
			title=title or f"Lab paper {self._seq}",
			link=f"https://example.com/lab-filter/{self._seq}",
		)
		article.published_date = timezone.now() - timedelta(days=days_ago)
		article.save(update_fields=["published_date"])
		article.teams.add(self.team)
		for subject in subjects or [self.s1]:
			article.subjects.add(subject)
		for category in categories:
			article.categories.add(category)
		article.authors.add(*authors)
		return article

	def get(self, path="/articles/", **params):
		cache.clear()
		return self.client.get(path, params)

	def ids(self, resp):
		self.assertEqual(resp.status_code, 200, resp.content)
		return [a["article_id"] for a in resp.json()["results"]]


class WhoCountsTests(LabFilterBase):
	def test_head_and_member_papers_count_collaborator_and_outsider_papers_do_not(self):
		by_head = self.article(self.head)
		by_member = self.article(self.members[0])
		self.article(self.collaborator)
		self.article(make_author())
		self.assertCountEqual(
			self.ids(self.get(lab_id=self.lab.id)), [by_head.pk, by_member.pk]
		)

	def test_paper_by_several_lab_people_appears_once(self):
		shared = self.article(self.head, *self.members, self.collaborator)
		resp = self.get(lab_id=self.lab.id)
		self.assertEqual(self.ids(resp), [shared.pk])
		self.assertEqual(resp.json()["count"], 1)

	def test_duplicate_author_rows_of_a_member_count(self):
		duplicate = make_author("Hedda", family=self.head.family_name)
		LabMembership.objects.create(author=duplicate, lab=self.lab, role="member")
		paper = self.article(duplicate)
		self.assertEqual(self.ids(self.get(lab_id=self.lab.id)), [paper.pk])

	def test_other_labs_people_do_not_count(self):
		other = make_lab(make_author(), members=1)
		link_subject(other, self.s1)
		self.article(other.head)
		mine = self.article(self.head)
		self.assertEqual(self.ids(self.get(lab_id=self.lab.id)), [mine.pk])

	def test_no_duplicates_across_pages(self):
		papers = [self.article(self.head, *self.members, days_ago=i) for i in range(7)]
		seen = []
		for page in (1, 2, 3):
			seen += self.ids(
				self.get(lab_id=self.lab.id, ordering="-published_date", page=page, page_size=3)
			)
		self.assertEqual(seen, [p.pk for p in papers])


class CombinationTests(LabFilterBase):
	def test_combines_with_subject_team_ordering_and_page_size(self):
		newest = self.article(self.head, days_ago=1)
		self.article(self.members[0], days_ago=5)
		self.article(self.head, subjects=[self.s2])
		resp = self.get(
			lab_id=self.lab.id,
			team_id=self.team.id,
			subject_id=self.s1.id,
			ordering="-published_date",
			page_size=1,
		)
		self.assertEqual(self.ids(resp), [newest.pk])
		self.assertEqual(resp.json()["count"], 2)

	def test_combines_with_author_id_and_search(self):
		self.article(self.head, title="Remyelination in mice")
		hit = self.article(self.members[0], title="Remyelination in humans")
		self.article(self.members[0], title="Something else")
		self.assertEqual(
			self.ids(self.get(lab_id=self.lab.id, author_id=self.members[0].pk, search="remyelination")),
			[hit.pk],
		)

	def test_articles_search_get_and_post(self):
		paper = self.article(self.head)
		self.article(make_author())
		url = reverse("article-search")
		params = {"team_id": self.team.id, "subject_id": self.s1.id, "lab_id": self.lab.id}
		self.assertEqual(self.ids(self.get(url, **params)), [paper.pk])
		cache.clear()
		self.assertEqual(self.ids(self.client.post(url, params, format="json")), [paper.pk])

	def test_csv(self):
		paper = self.article(self.head, title="CSV lab paper")
		self.article(make_author(), title="Not the lab's")
		resp = self.get(lab_id=self.lab.id, format="csv")
		self.assertEqual(resp.status_code, 200)
		body = b"".join(resp.streaming_content) if resp.streaming else resp.content
		rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
		self.assertEqual([r["title"] for r in rows], [paper.title])


class ErrorAndVisibilityTests(LabFilterBase):
	def test_non_integer_is_400(self):
		for value in ("abc", "1.5"):
			self.assertEqual(self.get(lab_id=value).status_code, 400, value)

	def test_unknown_lab_is_empty_not_an_error(self):
		self.article(self.head)
		resp = self.get(lab_id=999999)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.json()["count"], 0)

	def test_lab_without_members_is_empty(self):
		empty = make_lab(None)
		link_subject(empty, self.s1)
		self.article(self.head)
		self.assertEqual(self.get(lab_id=empty.id).json()["count"], 0)

	def test_lab_hidden_from_the_caller_is_empty(self):
		hidden = make_lab(make_author())
		link_subject(hidden, self.s_hidden)
		# The paper is in a visible subject; only the lab is out of sight.
		self.article(hidden.head)
		self.assertEqual(self.get(lab_id=hidden.id).json()["count"], 0)

	def test_lab_with_only_hidden_links_is_empty(self):
		self.lab.subject_links.update(is_hidden=True)
		self.article(self.head)
		self.assertEqual(self.get(lab_id=self.lab.id).json()["count"], 0)

	def test_private_site_key_sees_its_lab(self):
		site = private_site_publishing(self.s_hidden, organization=self.org)
		lab = make_lab(make_author())
		link_subject(lab, self.s_hidden)
		paper = self.article(lab.head, subjects=[self.s_hidden])
		client = APIClient()
		client.credentials(HTTP_AUTHORIZATION=_api_scheme(self.org, site).api_key)
		resp = client.get("/articles/", {"lab_id": lab.id})
		self.assertEqual(self.ids(resp), [paper.pk])

	def test_article_visibility_still_applies(self):
		self.article(self.head, subjects=[self.s_hidden])
		visible = self.article(self.head)
		self.assertEqual(self.ids(self.get(lab_id=self.lab.id)), [visible.pk])


class AgreesWithLabCountsTests(LabFilterBase):
	"""The frontend shows "View all {n} papers" next to the lab's own counts, so the
	filter must count what ``rebuild_lab_topics`` counts."""

	def test_counts_match_the_lab_topic_links(self):
		self.lab.subject_links.all().delete()
		self.article(self.head, *self.members, categories=[self.cat])
		self.article(self.members[0], categories=[self.cat])
		self.article(self.members[1])
		self.article(self.head, subjects=[self.s1, self.s2])
		self.article(self.members[0], subjects=[self.s2])
		self.article(self.members[1], subjects=[self.s2])
		self.article(self.collaborator)
		self.article(self.collaborator, subjects=[self.s2])
		duplicate = make_author("Hedda", family=self.head.family_name)
		LabMembership.objects.create(author=duplicate, lab=self.lab, role="member")
		self.article(duplicate, categories=[self.cat])
		apply_topic_links(compute_topic_links())

		cache.clear()
		lab = self.client.get(f"/labs/{self.lab.id}/").json()
		self.assertEqual({s["id"] for s in lab["subjects"]}, {self.s1.id, self.s2.id})
		self.assertEqual(len(lab["categories"]), 1)
		for subject in lab["subjects"]:
			resp = self.get(lab_id=self.lab.id, subject_id=subject["id"])
			self.assertEqual(resp.json()["count"], subject["articles"], subject)
		for category in lab["categories"]:
			resp = self.get(lab_id=self.lab.id, category_id=category["id"])
			self.assertEqual(resp.json()["count"], category["articles"], category)
