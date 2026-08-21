"""
Tests for subscriptions.utils.author_outreach_send — see
AUTHOR-OUTREACH-SPEC.md "Copy", "Configuration", "UTM", "Safety limits" and
AUTHOR-OUTREACH-PLAN.md "PR 5 — Rendering and sending".

Covers: the packaged default templates render with every placeholder,
correct UTM tagging, the opt-out link's Postmark no-track marker, the
site-level campaign.body_template override (rendered against
strings/dicts only), and the privacy regression guard that no rendered
link carries a person-resolvable identifier as a query parameter.
Circuit-breaker threshold behaviour lives here too — it is pure
DB-in/reason-out logic with no need for the send command around it.

Factory helper methods build fixtures directly, matching the convention in
test_author_outreach_eligibility.py and test_build_author_outreach_command.py.
"""

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from django.contrib.sites.models import Site
from django.test import TestCase
from django.utils import timezone

from gregory.models import Articles, Authors, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import AuthorOutreach, AuthorOutreachCampaign, EmailMessage
from subscriptions.utils.author_outreach_send import (
	build_render_context,
	evaluate_circuit_breakers,
	render_author_outreach_email,
)


class AuthorOutreachRenderTestCase(TestCase):
	def _new_world(self, tag, mode=AuthorOutreachCampaign.MODE_UPCOMING, campaign_kwargs=None, has_author_pages=True):
		org = Organization.objects.create(name=f"Org {tag}", slug=f"org-{tag}")
		team = Team.objects.create(name=f"Team {tag}", organization=org, slug=f"team-{tag}")
		site = Site.objects.create(domain=f"{tag}.example.com", name=tag)
		custom_settings = CustomSetting.objects.create(
			site=site,
			title=f"CS {tag}",
			sender_name=f"Sender {tag}",
			sender_email_prefix="gregory",
			has_author_pages=has_author_pages,
			api_domain="",
		)

		campaign_defaults = dict(
			site=site,
			name=f"Campaign {tag}",
			utm_campaign_slug=f"campaign-{tag}",
			mode=mode,
			enabled=True,
			reply_to="bruno@brain-regeneration.com",
		)
		if campaign_kwargs:
			campaign_defaults.update(campaign_kwargs)
		campaign = AuthorOutreachCampaign.objects.create(**campaign_defaults)

		author = Authors.objects.create(
			given_name="Jane",
			family_name=f"Researcher {tag}",
			ORCID=f"orcid-{tag}",
			emails=[f"jane.{tag}@example.org"],
			orcid_verified_email=True,
			orcid_claimed=True,
		)

		article1 = Articles.objects.create(
			title=f"Article One {tag}",
			link=f"https://example.com/one-{tag}",
			doi=f"10.9999/one-{tag}",
			published_date=timezone.now() - timedelta(days=1),
		)
		article1.authors.add(author)
		article2 = Articles.objects.create(
			title=f"Article Two {tag}",
			link=f"https://example.com/two-{tag}",
			doi=f"10.9999/two-{tag}",
			published_date=timezone.now() - timedelta(days=5),
		)
		article2.authors.add(author)

		row = AuthorOutreach.objects.create(
			campaign=campaign,
			site=site,
			author=author,
			email=author.emails[0],
			status=AuthorOutreach.STATUS_APPROVED,
		)
		row.articles.set([article1, article2])

		return SimpleNamespace(
			org=org,
			team=team,
			site=site,
			custom_settings=custom_settings,
			campaign=campaign,
			author=author,
			article1=article1,
			article2=article2,
			row=row,
		)

	def _all_urls(self, *texts):
		"""Every http(s) URL substring found across the given strings."""
		import re

		urls = []
		for text in texts:
			urls.extend(re.findall(r"https?://[^\s\"'<>]+", text))
		return urls

	# ------------------------------------------------------------------
	# Packaged default template
	# ------------------------------------------------------------------

	def test_packaged_default_renders_with_every_placeholder(self):
		w = self._new_world("pkg1")
		subject, html_body, text_body = render_author_outreach_email(
			w.row, w.campaign, w.site, w.custom_settings
		)
		self.assertTrue(subject)
		for body in (html_body, text_body):
			self.assertIn(w.author.full_name.split()[0], body)  # given name at least
			self.assertIn("Article One pkg1", body)
			self.assertIn("Article Two pkg1", body)
			self.assertIn("/subscriptions/author-optout/", body)
			self.assertIn(f"/authors/{w.author.ORCID}/", body)

	def test_html_body_contains_no_base_email_wrapper_markup(self):
		w = self._new_world("nowrap")
		_, html_body, _ = render_author_outreach_email(w.row, w.campaign, w.site, w.custom_settings)
		# base_email.html's signature scaffolding (header/footer component
		# includes, its distinctive table-based layout classes) must never
		# appear — this is intentionally NOT the digest/announcement style.
		self.assertNotIn("email-footer-container", html_body)
		self.assertNotIn("components/header.html", html_body)
		self.assertNotIn("<table", html_body)

	def test_opt_out_link_carries_postmark_no_track_marker(self):
		"""
		Postmark's own developer docs
		(https://postmarkapp.com/developer/user-guide/tracking-links) name
		`data-pm-no-track` as the attribute that excludes one <a> from
		click tracking even when TrackLinks is enabled for the message —
		see the example the docs give:
		<a data-pm-no-track href="http://www.somedomain.com">...</a>
		"""
		w = self._new_world("notrack")
		_, html_body, _ = render_author_outreach_email(w.row, w.campaign, w.site, w.custom_settings)
		self.assertIn("data-pm-no-track", html_body)
		# It's on the opt-out anchor specifically, not just present anywhere.
		import re

		match = re.search(r'<a[^>]*data-pm-no-track[^>]*href="([^"]+)"', html_body)
		self.assertIsNotNone(match, "data-pm-no-track not found on an <a> with an href")
		self.assertIn("/subscriptions/author-optout/", match.group(1))

	def test_article_and_author_page_links_are_not_marked_no_track(self):
		w = self._new_world("trackok")
		_, html_body, _ = render_author_outreach_email(w.row, w.campaign, w.site, w.custom_settings)
		import re

		for m in re.finditer(r"<a([^>]*)href=\"([^\"]+)\"", html_body):
			attrs, href = m.group(1), m.group(2)
			if "/subscriptions/author-optout/" in href:
				continue
			self.assertNotIn("data-pm-no-track", attrs, f"unexpected no-track marker on {href}")

	# ------------------------------------------------------------------
	# UTM
	# ------------------------------------------------------------------

	def test_article_and_author_page_urls_carry_correct_utm_params(self):
		w = self._new_world("utm1")
		context = build_render_context(w.row, w.campaign, w.site, w.custom_settings)

		article_url = context["articles"][0]["url"]
		qs = parse_qs(urlparse(article_url).query)
		self.assertEqual(qs["utm_medium"], ["email"])
		self.assertEqual(qs["utm_source"], ["author_outreach"])
		self.assertEqual(qs["utm_campaign"], [w.campaign.utm_campaign_slug])
		self.assertEqual(qs["utm_content"], ["article_link"])

		author_qs = parse_qs(urlparse(context["author_page_url"]).query)
		self.assertEqual(author_qs["utm_content"], ["author_page"])
		self.assertEqual(author_qs["utm_campaign"], [w.campaign.utm_campaign_slug])

		site_qs = parse_qs(urlparse(context["site_url"]).query)
		self.assertEqual(site_qs["utm_content"], ["site"])

	def test_opt_out_url_is_never_utm_tagged(self):
		w = self._new_world("nouturm")
		context = build_render_context(w.row, w.campaign, w.site, w.custom_settings)
		self.assertNotIn("?", context["opt_out_url"])

	def test_add_utm_params_does_not_tag_third_party_host(self):
		"""Regression guard for AUTHOR-OUTREACH-PLAN.md PR 5's 'Confirm
		add_utm_params still only tags links on the sending site's own
		host' — a DOI/registry link on a different host must never be
		tagged even if a template author tries to pass it through."""
		from gregory.templatetags.gregory_tags import add_utm_params
		from subscriptions.utils.utm import build_utm_params

		w = self._new_world("thirdparty")
		utm_params = build_utm_params("author_outreach", w.campaign, "article_link")
		doi_url = f"https://doi.org/{w.article1.doi}"
		tagged = add_utm_params(doi_url, utm_params, w.site.domain)
		self.assertEqual(tagged, doi_url)

	# ------------------------------------------------------------------
	# Privacy regression guard
	# ------------------------------------------------------------------

	def test_no_rendered_link_carries_a_person_identifier_as_a_query_parameter(self):
		"""
		AUTHOR-OUTREACH-SPEC.md "Non-goals": "No per-recipient identifier
		in any URL. The opt-out token is the sole exception[...]". This
		asserts that exception is the ONLY per-person value anywhere, and
		specifically that nothing resolvable to this author (author_id,
		ORCID, or email) is ever a query *parameter* key or value — the
		ORCID appearing in the author page URL's *path* is expected and
		is not what this guards against.
		"""
		w = self._new_world("privacy1")
		_, html_body, text_body = render_author_outreach_email(
			w.row, w.campaign, w.site, w.custom_settings
		)

		forbidden_values = {
			str(w.author.pk),
			w.author.ORCID,
			w.author.emails[0],
			w.author.emails[0].lower(),
		}

		for url in self._all_urls(html_body, text_body):
			parsed = urlparse(url)
			qs = parse_qs(parsed.query)
			for key, values in qs.items():
				self.assertNotIn(
					key.lower(),
					{"author", "author_id", "orcid", "email", "id", "uid", "user"},
					f"query key {key!r} in {url!r} looks person-identifying",
				)
				for value in values:
					self.assertNotIn(
						value,
						forbidden_values,
						f"query value {value!r} in {url!r} resolves to the author",
					)
					self.assertNotIn("@", value, f"query value {value!r} in {url!r} looks like an email")

	def test_author_page_url_keys_orcid_in_path_not_query(self):
		"""The one place the ORCID legitimately appears — confirms the
		privacy guard above isn't vacuously passing because the link was
		empty or malformed."""
		w = self._new_world("orcidpath")
		context = build_render_context(w.row, w.campaign, w.site, w.custom_settings)
		parsed = urlparse(context["author_page_url"])
		self.assertIn(w.author.ORCID, parsed.path)
		self.assertNotIn(w.author.ORCID, parsed.query)

	# ------------------------------------------------------------------
	# Site-level override (campaign.body_template)
	# ------------------------------------------------------------------

	def test_body_template_override_renders_against_primitives_only(self):
		w = self._new_world(
			"override1",
			campaign_kwargs={
				"body_template": (
					"<p>Dear {{ author_name }},</p>"
					"<p>Your paper <a href=\"{{ article_url }}\">{{ article_title }}</a> "
					"was featured.</p>"
					"<p><a data-pm-no-track href=\"{{ opt_out_url }}\">opt out</a></p>"
				)
			},
		)
		subject, html_body, text_body = render_author_outreach_email(
			w.row, w.campaign, w.site, w.custom_settings
		)
		self.assertIn("Dear", html_body)
		self.assertIn("Article One override1", html_body)
		self.assertIn("data-pm-no-track", html_body)
		# The derived .txt alternative keeps the link, not just the label.
		self.assertIn("/subscriptions/author-optout/", text_body)
		self.assertIn("opt out (", text_body)

	def test_body_template_override_cannot_reach_orm_relations(self):
		"""
		AUTHOR-OUTREACH-SPEC.md "Configuration": rendered against an
		explicit context of strings and dicts only, never a model
		instance. An admin-authored template that tries to walk a
		relation Django would normally allow (e.g. author.emails, or a
		dotted lookup) gets nothing, because the context never contains
		an `author` key or any object with attributes at all — only the
		named string/list-of-dict placeholders.
		"""
		w = self._new_world(
			"noorm",
			campaign_kwargs={
				"body_template": "<p>{{ author.emails }}{{ author.ORCID }}{{ row.email }}</p>"
			},
		)
		_, html_body, _ = render_author_outreach_email(w.row, w.campaign, w.site, w.custom_settings)
		self.assertNotIn(w.author.emails[0], html_body)
		self.assertNotIn(w.author.ORCID, html_body)

	def test_retrospective_campaign_with_blank_body_template_refuses_to_render(self):
		w = self._new_world("retro1", mode=AuthorOutreachCampaign.MODE_RETROSPECTIVE)
		with self.assertRaises(ValueError):
			render_author_outreach_email(w.row, w.campaign, w.site, w.custom_settings)

	def test_retrospective_campaign_with_body_template_renders_fine(self):
		w = self._new_world(
			"retro2",
			mode=AuthorOutreachCampaign.MODE_RETROSPECTIVE,
			campaign_kwargs={"body_template": "<p>Hi {{ author_name }}, this was featured.</p>"},
		)
		subject, html_body, text_body = render_author_outreach_email(
			w.row, w.campaign, w.site, w.custom_settings
		)
		self.assertIn("was featured", html_body)


class CircuitBreakerTestCase(TestCase):
	def _new_campaign(self, tag, **kwargs):
		org = Organization.objects.create(name=f"Org {tag}", slug=f"org-{tag}")
		Team.objects.create(name=f"Team {tag}", organization=org, slug=f"team-{tag}")
		site = Site.objects.create(domain=f"{tag}.example.com", name=tag)
		CustomSetting.objects.create(site=site, title=f"CS {tag}", has_author_pages=True)
		defaults = dict(
			site=site,
			name=f"Campaign {tag}",
			utm_campaign_slug=f"campaign-{tag}",
			enabled=True,
		)
		defaults.update(kwargs)
		return AuthorOutreachCampaign.objects.create(**defaults)

	def _attach_message(self, campaign, tag, **message_kwargs):
		"""Create one EmailMessage wired to `campaign` via an AuthorOutreach
		row — the only path evaluate_circuit_breakers can read a
		campaign's aggregates through (EmailMessage carries no direct FK
		to AuthorOutreachCampaign)."""
		author = Authors.objects.create(
			given_name="A",
			family_name=f"Author {tag}",
			ORCID=f"orcid-{tag}",
			emails=[f"{tag}@example.org"],
			orcid_verified_email=True,
			orcid_claimed=True,
		)
		message = EmailMessage.objects.create(
			recipient=f"{tag}@example.org",
			tag="author_outreach",
			site=campaign.site,
			**message_kwargs,
		)
		AuthorOutreach.objects.create(
			campaign=campaign,
			site=campaign.site,
			author=author,
			email=f"{tag}@example.org",
			status=AuthorOutreach.STATUS_SENT,
			email_message=message,
		)
		return message

	def test_no_breaker_trips_on_a_clean_campaign(self):
		campaign = self._new_campaign("clean")
		self.assertIsNone(evaluate_circuit_breakers(campaign))

	def test_complaint_absolute_threshold_does_not_trip_one_below(self):
		campaign = self._new_campaign("compbelow", complaint_halt_absolute=2)
		self._attach_message(campaign, "c1", accepted=True, complained_at=timezone.now())
		self.assertIsNone(evaluate_circuit_breakers(campaign))

	def test_complaint_absolute_threshold_trips_at_exactly_the_threshold(self):
		campaign = self._new_campaign("compat", complaint_halt_absolute=2)
		self._attach_message(campaign, "c1", accepted=True, complained_at=timezone.now())
		self._attach_message(campaign, "c2", accepted=True, complained_at=timezone.now())
		reason = evaluate_circuit_breakers(campaign)
		self.assertIsNotNone(reason)
		self.assertIn("spam complaint", reason)

	def test_bounce_absolute_threshold_does_not_trip_one_below(self):
		campaign = self._new_campaign("bouncebelow", bounce_halt_absolute=3)
		for i in range(2):
			self._attach_message(campaign, f"b{i}", accepted=True, bounce_type="HardBounce")
		self.assertIsNone(evaluate_circuit_breakers(campaign))

	def test_bounce_absolute_threshold_trips_at_exactly_the_threshold(self):
		campaign = self._new_campaign("bounceat", bounce_halt_absolute=3)
		for i in range(3):
			self._attach_message(campaign, f"b{i}", accepted=True, bounce_type="HardBounce")
		reason = evaluate_circuit_breakers(campaign)
		self.assertIsNotNone(reason)
		self.assertIn("hard bounce", reason)

	def test_soft_bounce_never_counts_toward_the_hard_bounce_breaker(self):
		campaign = self._new_campaign("softbounce", bounce_halt_absolute=1)
		self._attach_message(campaign, "s1", accepted=True, bounce_type="SoftBounce")
		self.assertIsNone(evaluate_circuit_breakers(campaign))

	def test_inactive_406_threshold_does_not_trip_one_below(self):
		campaign = self._new_campaign("inactivebelow", inactive_halt_absolute=5)
		for i in range(4):
			self._attach_message(campaign, f"i{i}", accepted=False, error_code=406)
		self.assertIsNone(evaluate_circuit_breakers(campaign))

	def test_inactive_406_threshold_trips_at_exactly_the_threshold(self):
		campaign = self._new_campaign("inactiveat", inactive_halt_absolute=5)
		for i in range(5):
			self._attach_message(campaign, f"i{i}", accepted=False, error_code=406)
		reason = evaluate_circuit_breakers(campaign)
		self.assertIsNotNone(reason)
		self.assertIn("406", reason)

	def test_complaint_rate_does_not_trip_below_min_sent(self):
		campaign = self._new_campaign(
			"ratebelowmin",
			complaint_halt_absolute=999,
			complaint_halt_rate_min_sent=5,
			complaint_halt_rate_percent=0.1,
		)
		# 1 complaint out of 4 accepted sends = 25%, but min_sent (5) not reached.
		self._attach_message(campaign, "r1", accepted=True, complained_at=timezone.now())
		for i in range(3):
			self._attach_message(campaign, f"ok{i}", accepted=True)
		self.assertIsNone(evaluate_circuit_breakers(campaign))

	def test_complaint_rate_trips_once_min_sent_reached_and_rate_exceeded(self):
		campaign = self._new_campaign(
			"rateover",
			complaint_halt_absolute=999,
			complaint_halt_rate_min_sent=5,
			complaint_halt_rate_percent=0.1,
		)
		self._attach_message(campaign, "r1", accepted=True, complained_at=timezone.now())
		for i in range(4):
			self._attach_message(campaign, f"ok{i}", accepted=True)
		reason = evaluate_circuit_breakers(campaign)
		self.assertIsNotNone(reason)
		self.assertIn("Complaint rate", reason)

	def test_bounce_rate_trips_once_min_sent_reached_and_rate_exceeded(self):
		campaign = self._new_campaign(
			"bouncerate",
			bounce_halt_absolute=999,
			bounce_halt_rate_min_sent=4,
			bounce_halt_rate_percent=5.0,
		)
		self._attach_message(campaign, "b1", accepted=True, bounce_type="HardBounce")
		for i in range(3):
			self._attach_message(campaign, f"ok{i}", accepted=True)
		reason = evaluate_circuit_breakers(campaign)
		self.assertIsNotNone(reason)
		self.assertIn("Hard-bounce rate", reason)

	def test_thresholds_are_read_from_campaign_fields_not_hardcoded(self):
		"""A campaign with a much lower absolute threshold than the model
		default trips earlier than the default would."""
		campaign = self._new_campaign("customthresh", complaint_halt_absolute=1)
		self._attach_message(campaign, "c1", accepted=True, complained_at=timezone.now())
		reason = evaluate_circuit_breakers(campaign)
		self.assertIsNotNone(reason)
