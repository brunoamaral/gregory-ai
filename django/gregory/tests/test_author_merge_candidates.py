"""Author merge candidates: the pure classification, the merge fixes that make bulk merging
safe (outreach rows, opt-outs, emails, set-based article transfer), the live loading, the
superuser-only admin screen, and the ``merge_author_candidates`` command."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from gregory.models import (
	ArticleAuthorship,
	Articles,
	AuthorMergeDismissal,
	Authors,
)
from gregory.services import author_merge_candidates as svc
from gregory.services.author_merge import (
	OutreachConflictError,
	duplicated_sites,
	merge_authors,
	union_emails,
)
from subscriptions.models import (
	AuthorContactOptOut,
	AuthorOutreach,
	AuthorOutreachCampaign,
)

User = get_user_model()


def row(n, given="Ana", family="Silva", orcid="", articles=0, oa="A1"):
	return svc.CandidateRow(
		author_id=n,
		given_name=given,
		family_name=family,
		ORCID=orcid,
		openalex_id=oa,
		emails=[],
		article_count=articles,
	)


class NormaliseAndClassifyTests(SimpleTestCase):
	def test_normalise_name(self):
		self.assertEqual(svc.normalise_name("  José-María  O'Neil. "), "jose maria oneil")
		self.assertEqual(svc.normalise_name("Dvořák"), "dvorak")
		self.assertEqual(svc.normalise_name("J.R."), "j r")
		self.assertEqual(svc.normalise_name(None), "")
		self.assertEqual(svc.normalise_name("Müller"), svc.normalise_name("MULLER"))

	def test_names_agree(self):
		self.assertTrue(svc.names_agree([row(1, "Ana", "Silva"), row(2, "A.", "silva"), row(3, "Ánna", "SILVA")]))
		self.assertFalse(svc.names_agree([row(1, "Ana", "Silva"), row(2, "Bea", "Silva")]))
		self.assertFalse(svc.names_agree([row(1, "Ana", "Silva"), row(2, "Ana", "Silva Costa")]))
		self.assertFalse(svc.names_agree([row(1, "Ana", "Silva"), row(2, "", "Silva")]))
		self.assertFalse(svc.names_agree([row(1, "Ana", "Silva"), row(2, "Ana", "")]))
		# "SF Ahmed" vs "Samar Farouk Ahmed": same initial, same family: still clear-cut
		self.assertTrue(svc.names_agree([row(1, "SF", "Ahmed"), row(2, "Samar Farouk", "Ahmed")]))

	def test_classify(self):
		same = [row(1), row(2, "A.", "SILVA")]
		self.assertEqual(svc.classify_rows(same), (svc.CLEAR_CUT, ""))
		self.assertEqual(svc.classify_rows([row(1), row(2, "Bea")])[0], svc.NAMES_DIFFER)
		status, reason = svc.classify_rows(
			[row(1, orcid="0000-0002-1825-0097"), row(2, orcid="0000-0001-5109-3700")]
		)
		self.assertEqual(status, svc.BLOCKED)
		self.assertIn("conflicting ORCIDs", reason)
		# the same ORCID written as a URL is not a conflict
		self.assertEqual(
			svc.classify_rows(
				[row(1, orcid="0000-0002-1825-0097"), row(2, orcid="https://orcid.org/0000-0002-1825-0097")]
			)[0],
			svc.CLEAR_CUT,
		)
		status, reason = svc.classify_rows(same, conflicting_sites=[3])
		self.assertEqual(status, svc.BLOCKED)
		self.assertIn("outreach", reason)

	def test_default_survivor(self):
		self.assertEqual(svc.default_survivor([row(5, articles=9), row(2, orcid="0000-0002-1825-0097", articles=1)]), 2)
		self.assertEqual(svc.default_survivor([row(5, articles=9), row(2, articles=1)]), 5)
		self.assertEqual(svc.default_survivor([row(5, articles=3), row(2, articles=3)]), 2)

	def test_build_groups_and_dismissals(self):
		rows = [
			row(1, oa="A1", articles=1),
			row(2, oa="A1", articles=1),
			row(3, oa="A2", articles=5),
			row(4, oa="A2", articles=5),
			row(5, oa="A3"),  # alone: not a group
		]
		groups = svc.build_groups(rows, [(7, 3), (7, 4)], [("A1", [1, 2])])
		self.assertEqual([g.openalex_id for g in groups], ["A2", "A1"])  # most articles first
		a2, a1 = groups
		self.assertEqual(a2.status, svc.BLOCKED)  # same site twice
		self.assertTrue(a1.dismissed)
		self.assertFalse(a2.dismissed)
		# a dismissal of other ids doesn't hide the group
		groups = svc.build_groups(rows, [], [("A1", [1, 2, 9])])
		self.assertFalse([g for g in groups if g.openalex_id == "A1"][0].dismissed)

	def test_counts_and_filter(self):
		rows = [row(1, oa="A1"), row(2, oa="A1"), row(3, "Bea", oa="A2"), row(4, oa="A2"), row(5, oa="A3"), row(6, oa="A3")]
		groups = svc.build_groups(rows, [], [("A3", [5, 6])])
		self.assertEqual(
			svc.count_by_class(groups),
			{svc.CLEAR_CUT: 1, svc.NAMES_DIFFER: 1, svc.BLOCKED: 0, svc.DISMISSED: 1},
		)
		self.assertEqual([g.openalex_id for g in svc.filter_groups(groups, svc.CLEAR_CUT)], ["A1"])
		self.assertEqual([g.openalex_id for g in svc.filter_groups(groups, svc.DISMISSED)], ["A3"])
		self.assertEqual([g.openalex_id for g in svc.filter_groups(groups, svc.NAMES_DIFFER, "bea")], ["A2"])
		self.assertEqual(svc.filter_groups(groups, svc.NAMES_DIFFER, "zzz"), [])
		self.assertEqual([g.openalex_id for g in svc.filter_groups(groups, svc.CLEAR_CUT, "a1")], ["A1"])

	def test_helpers(self):
		self.assertEqual(duplicated_sites([(1, 10), (1, 11), (2, 10), (2, 10)]), {1})
		self.assertEqual(
			union_emails(["A@x.org", "b@x.org"], ["a@X.org", "c@x.org", ""], None),
			["A@x.org", "b@x.org", "c@x.org"],
		)


def author(given="Ana", family="Silva", oa=None, orcid=None, emails=None):
	return Authors.objects.create(
		given_name=given, family_name=family, openalex_id=oa, ORCID=orcid, emails=emails or []
	)


def article(n, *authors):
	art = Articles.objects.create(title=f"Paper {n}", link=f"https://example.com/p{n}")
	art.authors.add(*authors)
	for index, who in enumerate(authors):
		ArticleAuthorship.objects.create(
			article=art,
			author=who,
			position="middle",
			author_index=index,
			openalex_author_id=who.openalex_id or "",
		)
	return art


def outreach(who, site_domain, email=None):
	site, _ = Site.objects.get_or_create(domain=site_domain, defaults={"name": site_domain})
	campaign, _ = AuthorOutreachCampaign.objects.get_or_create(
		site=site, name=f"c-{site_domain}", utm_campaign_slug=f"c-{site_domain}"
	)
	return AuthorOutreach.objects.create(
		campaign=campaign, site=site, author=who, email=email or f"{who.pk}@example.com"
	)


class MergeAuthorsFixesTests(TestCase):
	def test_outreach_rows_move_to_survivor(self):
		keep, gone = author(oa="A1"), author("A.", oa="A1")
		row_ = outreach(gone, "one.example.com")
		stats = {}
		merge_authors(keep, [gone], stats=stats)
		row_.refresh_from_db()
		self.assertEqual(row_.author_id, keep.pk)
		self.assertFalse(Authors.objects.filter(pk=gone.pk).exists())
		self.assertEqual(stats["outreach_rows"], 1)
		self.assertEqual(stats["removed"], 1)

	def test_outreach_on_different_sites_both_move(self):
		keep, gone = author(oa="A1"), author("A.", oa="A1")
		outreach(keep, "one.example.com")
		outreach(gone, "two.example.com")
		merge_authors(keep, [gone])
		self.assertEqual(AuthorOutreach.objects.filter(author=keep).count(), 2)

	def test_outreach_conflict_raises_before_changing_anything(self):
		keep, gone = author(oa="A1", emails=["k@x.org"]), author("A.", oa="A1", emails=["g@x.org"])
		art = article(1, gone)
		outreach(keep, "one.example.com")
		outreach(gone, "one.example.com")
		with self.assertRaises(OutreachConflictError):
			merge_authors(keep, [gone])
		self.assertTrue(Authors.objects.filter(pk=gone.pk).exists())
		self.assertEqual(list(art.authors.values_list("pk", flat=True)), [gone.pk])
		keep.refresh_from_db()
		self.assertEqual(keep.emails, ["k@x.org"])

	def test_two_removed_with_same_site_conflict(self):
		keep, a, b = author(oa="A1"), author("A.", oa="A1"), author("An", oa="A1")
		outreach(a, "one.example.com")
		outreach(b, "one.example.com")
		with self.assertRaises(OutreachConflictError):
			merge_authors(keep, [a, b])

	def test_opt_outs_repointed(self):
		keep, gone = author(oa="A1"), author("A.", oa="A1")
		opt = AuthorContactOptOut.objects.create(
			author=gone, email="x@example.com", reason=AuthorContactOptOut.REASON_OPT_OUT
		)
		stats = {}
		merge_authors(keep, [gone], stats=stats)
		opt.refresh_from_db()
		self.assertEqual(opt.author_id, keep.pk)
		self.assertEqual(stats["opt_outs"], 1)

	def test_emails_union_in_order_case_insensitive(self):
		keep = author(oa="A1", emails=["a@x.org", "B@x.org"])
		gone = author("A.", oa="A1", emails=["b@X.org", "c@x.org"])
		other = author("An", oa="A1", emails=["C@x.org", "d@x.org"])
		merge_authors(keep, [gone, other])
		keep.refresh_from_db()
		self.assertEqual(keep.emails, ["a@x.org", "B@x.org", "c@x.org", "d@x.org"])

	def test_article_links_transfer(self):
		keep, a, b = author(oa="A1"), author("A.", oa="A1"), author("An", oa="A1")
		only_a = article(1, a)
		shared_with_keep = article(2, keep, a)
		shared_by_removed = article(3, a, b)
		stats = {}
		_, moved = merge_authors(keep, [a, b], stats=stats)
		self.assertEqual(moved, 2)  # only_a and shared_by_removed; article 2 was already keep's
		for art in (only_a, shared_with_keep, shared_by_removed):
			self.assertEqual(list(art.authors.values_list("pk", flat=True)), [keep.pk])
		self.assertEqual(ArticleAuthorship.objects.filter(author=keep).count(), 3)

	def test_query_count_does_not_grow_with_articles(self):
		def merge_with(n_articles):
			keep, gone = author(oa="A1"), author("A.", oa="A1")
			for i in range(n_articles):
				article(1000 * n_articles + i, gone)
			with CaptureQueriesContext(connection) as ctx:
				merge_authors(keep, [gone])
			Authors.objects.filter(pk=keep.pk).delete()
			return len(ctx)

		self.assertEqual(merge_with(2), merge_with(12))

	def test_articles_history_does_not_track_authors(self):
		# the set-based transfer bypasses M2M signals: that is only safe while history ignores it
		tracked = [f.name for f in Articles.history.model._meta.get_fields()]
		self.assertNotIn("authors", tracked)


class LoadGroupsTests(TestCase):
	def test_group_loading_and_classification(self):
		a, b = author("Ana", "Silva", oa="A1"), author("A.", "Silva", oa="A1")
		c, d = author("Ana", "Silva", oa="A2"), author("Bea", "Costa", oa="A2")
		e, f = author("Ana", "Silva", oa="A3", orcid="0000-0002-1825-0097"), author(
			"Ana", "Silva", oa="A3", orcid="0000-0001-5109-3700"
		)
		g, h = author("Ana", "Silva", oa="A4"), author("A.", "Silva", oa="A4")
		author("Solo", "Person", oa="A5")
		article(1, a, b)
		outreach(g, "one.example.com")
		outreach(h, "one.example.com")
		groups = {x.openalex_id: x for x in svc.load_groups()}
		self.assertEqual(set(groups), {"A1", "A2", "A3", "A4"})
		self.assertEqual(groups["A1"].status, svc.CLEAR_CUT)
		self.assertEqual(groups["A1"].total_articles, 2)
		self.assertEqual(groups["A2"].status, svc.NAMES_DIFFER)
		self.assertEqual(groups["A3"].status, svc.BLOCKED)
		self.assertEqual(groups["A4"].status, svc.BLOCKED)
		self.assertEqual(groups["A3"].survivor_id, min(e.pk, f.pk))

	def test_query_count_does_not_grow_with_groups(self):
		def make(count, base):
			for i in range(count):
				x = author("Ana", "Silva", oa=f"B{base}{i}")
				y = author("A.", "Silva", oa=f"B{base}{i}")
				article(base * 100 + i, x, y)
				outreach(x, f"s{base}{i}.example.com")

		make(2, 1)
		with CaptureQueriesContext(connection) as small:
			svc.load_groups()
		make(15, 2)
		with CaptureQueriesContext(connection) as large:
			groups = svc.load_groups()
		self.assertEqual(len(groups), 17)
		self.assertEqual(len(small), len(large))

	def test_dismissal_hides_until_group_changes(self):
		a, b = author(oa="A1"), author("A.", oa="A1")
		AuthorMergeDismissal.objects.create(openalex_id="A1", author_ids=sorted([a.pk, b.pk]))
		self.assertTrue(svc.load_groups()[0].dismissed)
		author("An", oa="A1")
		self.assertFalse(svc.load_groups()[0].dismissed)


class MergeGroupTests(TestCase):
	def test_merge_group_sets_orcid_and_moves_things(self):
		a = author("Ana", "Silva", oa="A1")
		b = author("A.", "Silva", oa="A1", orcid="https://orcid.org/0000-0002-1825-0097")
		article(1, a)
		out = svc.merge_group("A1", a.pk, [b.pk])
		self.assertEqual(out.removed, 1)
		a.refresh_from_db()
		self.assertEqual(a.ORCID, "0000-0002-1825-0097")
		self.assertFalse(Authors.objects.filter(pk=b.pk).exists())

	def test_blocked_group_refused(self):
		a = author("Ana", "Silva", oa="A1", orcid="0000-0002-1825-0097")
		b = author("A.", "Silva", oa="A1", orcid="0000-0001-5109-3700")
		with self.assertRaises(svc.MergeRefused):
			svc.merge_group("A1", a.pk, [b.pk])
		self.assertTrue(Authors.objects.filter(pk=b.pk).exists())

	def test_bulk_only_merges_clear_cut(self):
		a, b = author("Ana", "Silva", oa="A1"), author("A.", "Silva", oa="A1")
		article(1, a, b)
		author("Ana", "Silva", oa="A2")
		author("Bea", "Costa", oa="A2")
		c, d = author("Cy", "Lee", oa="A3"), author("C.", "Lee", oa="A3")
		AuthorMergeDismissal.objects.create(openalex_id="A3", author_ids=sorted([c.pk, d.pk]))
		result = svc.merge_clear_cut_groups(limit=10)
		self.assertEqual((result.groups_merged, result.rows_removed), (1, 1))
		self.assertEqual(result.skipped, {"names_differ": 1, "dismissed": 1})
		self.assertEqual(Authors.objects.filter(openalex_id="A1").count(), 1)
		self.assertEqual(Authors.objects.filter(openalex_id="A2").count(), 2)
		self.assertEqual(Authors.objects.filter(openalex_id="A3").count(), 2)

	def test_bulk_limit_and_remaining(self):
		for i in range(3):
			author("Ana", "Silva", oa=f"A{i}")
			author("A.", "Silva", oa=f"A{i}")
		result = svc.merge_clear_cut_groups(limit=2)
		self.assertEqual((result.groups_merged, result.remaining), (2, 1))


class CommandTests(TestCase):
	def test_dry_run_changes_nothing_then_real_run(self):
		author("Ana", "Silva", oa="A1")
		author("A.", "Silva", oa="A1")
		out = StringIO()
		call_command("merge_author_candidates", "--dry-run", stdout=out)
		self.assertIn("Would merge 1 group", out.getvalue())
		self.assertEqual(Authors.objects.count(), 2)
		out = StringIO()
		call_command("merge_author_candidates", "--limit", "5", stdout=out)
		self.assertIn("Merged 1 group", out.getvalue())
		self.assertEqual(Authors.objects.count(), 1)


class AdminScreenTests(TestCase):
	def setUp(self):
		self.superuser = User.objects.create_superuser("root", "root@example.com", "pw")
		self.staff = User.objects.create_user("staff", password="pw", is_staff=True)
		self.list_url = reverse("admin:gregory_authormergecandidate_changelist")
		self.a, self.b = author("Ana", "Silva", oa="A1"), author("A.", "Silva", oa="A1")
		article(1, self.a)

	def test_superuser_sees_screen_and_index_entry(self):
		self.client.force_login(self.superuser)
		response = self.client.get(self.list_url)
		self.assertContains(response, "Author merge candidates")
		self.assertContains(response, "Clear-cut (1)")
		self.assertContains(response, "Ana Silva")
		index = self.client.get(reverse("admin:index"))
		self.assertContains(index, self.list_url)

	def test_staff_gets_403_and_no_index_entry(self):
		self.client.force_login(self.staff)
		self.assertEqual(self.client.get(self.list_url).status_code, 403)
		for name in ("merge", "dismiss", "restore", "merge_all"):
			url = reverse(f"admin:gregory_authormergecandidate_{name}")
			self.assertEqual(self.client.post(url, {}).status_code, 403)
		self.assertNotContains(self.client.get(reverse("admin:index")), self.list_url)
		self.assertTrue(Authors.objects.filter(pk=self.b.pk).exists())

	def test_filter_search_and_pagination(self):
		self.client.force_login(self.superuser)
		self.assertContains(self.client.get(self.list_url, {"class": "names_differ"}), "No groups here")
		self.assertContains(self.client.get(self.list_url, {"q": "silva"}), "Ana Silva")
		self.assertContains(self.client.get(self.list_url, {"q": "nobody"}), "No groups here")
		for i in range(55):
			author("Ana", "Silva", oa=f"P{i}")
			author("A.", "Silva", oa=f"P{i}")
		response = self.client.get(self.list_url)
		self.assertContains(response, "Page 1 of 2")
		self.assertEqual(len(response.context["page"]), 50)

	def test_merge_post(self):
		self.client.force_login(self.superuser)
		response = self.client.post(
			reverse("admin:gregory_authormergecandidate_merge"),
			{"openalex_id": "A1", "keep": self.a.pk, "merge": [self.a.pk, self.b.pk], "class": "clear_cut"},
			follow=True,
		)
		self.assertContains(response, "Merged 1 author(s)")
		self.assertFalse(Authors.objects.filter(pk=self.b.pk).exists())

	def test_merge_post_needs_two_rows_and_survivor(self):
		self.client.force_login(self.superuser)
		self.client.post(
			reverse("admin:gregory_authormergecandidate_merge"),
			{"openalex_id": "A1", "keep": self.a.pk, "merge": [self.a.pk]},
		)
		self.assertTrue(Authors.objects.filter(pk=self.b.pk).exists())

	def test_blocked_group_cannot_be_merged(self):
		outreach(self.a, "one.example.com")
		outreach(self.b, "one.example.com")
		self.client.force_login(self.superuser)
		response = self.client.post(
			reverse("admin:gregory_authormergecandidate_merge"),
			{"openalex_id": "A1", "keep": self.a.pk, "merge": [self.a.pk, self.b.pk]},
			follow=True,
		)
		self.assertContains(response, "Blocked")
		self.assertTrue(Authors.objects.filter(pk=self.b.pk).exists())

	def test_dismiss_and_restore(self):
		self.client.force_login(self.superuser)
		self.client.post(
			reverse("admin:gregory_authormergecandidate_dismiss"),
			{"openalex_id": "A1", "ids": [self.a.pk, self.b.pk], "note": "two people"},
		)
		dismissal = AuthorMergeDismissal.objects.get()
		self.assertEqual(dismissal.author_ids, sorted([self.a.pk, self.b.pk]))
		self.assertEqual(dismissal.dismissed_by, self.superuser)
		self.assertEqual(dismissal.note, "two people")
		response = self.client.get(self.list_url)
		self.assertContains(response, "Clear-cut (0)")
		self.assertContains(response, "Dismissed (1)")
		self.client.post(reverse("admin:gregory_authormergecandidate_restore"), {"openalex_id": "A1"})
		self.assertEqual(AuthorMergeDismissal.objects.count(), 0)

	def test_dismiss_refused_when_group_changed(self):
		self.client.force_login(self.superuser)
		self.client.post(
			reverse("admin:gregory_authormergecandidate_dismiss"),
			{"openalex_id": "A1", "ids": [self.a.pk]},
		)
		self.assertEqual(AuthorMergeDismissal.objects.count(), 0)

	def test_merge_all_confirmation_then_batch(self):
		self.client.force_login(self.superuser)
		url = reverse("admin:gregory_authormergecandidate_merge_all")
		response = self.client.get(url)
		self.assertContains(response, "1</strong> clear-cut group")
		self.assertTrue(Authors.objects.filter(pk=self.b.pk).exists())  # GET changes nothing
		response = self.client.post(url, follow=True)
		self.assertContains(response, "Merged 1 clear-cut group")
		self.assertEqual(Authors.objects.filter(openalex_id="A1").count(), 1)
