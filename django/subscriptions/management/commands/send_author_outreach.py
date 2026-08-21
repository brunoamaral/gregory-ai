"""
Send the approved AuthorOutreach queue for one campaign. See
docs/author-outreach-spec.md "Queue and approval", "Safety limits", and
docs/author-outreach.md.

This is the first command in the feature that can send real email to a
real person, so every check below is a deliberate safety control, not
incidental plumbing:

- Only status="approved" rows are ever selected — a "pending" row is
  never sent, regardless of --limit or how the queue looks.
- Immediately before *every individual* send (not once per run): the
  recipient's address is re-checked against the opt-out/suppression
  tables (subscriptions.utils.author_outreach.is_contact_blocked — the
  exact same checks build_author_outreach applies at queue-build time),
  then the campaign's circuit breakers are re-evaluated against its live
  EmailMessage aggregates (subscriptions.utils.author_outreach_send.
  evaluate_circuit_breakers). Either can stop a row that looked fine when
  the queue was built or even one row ago in this same run.
- A breaker trip sets campaign.halted=True with a reason and stops the
  run outright — the row being considered when it tripped is left
  untouched (still "approved"), and every row after it is never reached.
  A campaign that is already halted refuses to send anything at all.
- A Postmark 406 (inactive recipient) marks the row "skipped" and counts
  toward the 406 breaker, exactly like the other three senders already
  treat a 406 as a suppression signal (see subscriptions.utils.postmark).
- Any other send failure marks the row "failed". There is deliberately no
  automatic retry: AuthorOutreach.unique_author_outreach_per_site means
  the (site, author) slot is burned either way, and re-opening one is the
  superuser-only "Reset for retry" admin action, not something this
  command ever does on its own.
"""

import logging
import time
import uuid

import requests
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from gregory.models import OrganizationSite
from sitesettings.models import CustomSetting
from subscriptions.management.commands.utils.get_credentials import (
	get_postmark_credentials,
)
from subscriptions.management.commands.utils.send_email import (
	record_sent_message,
	send_email,
)
from subscriptions.models import AuthorOutreach, AuthorOutreachCampaign, EmailMessage
from subscriptions.utils.author_outreach import (
	currently_qualifying_article_ids,
	is_contact_blocked,
)
from subscriptions.utils.author_outreach_send import (
	evaluate_circuit_breakers,
	render_author_outreach_email,
)
from subscriptions.utils.postmark import (
	POSTMARK_INACTIVE_RECIPIENT,
	classify_postmark_response,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = (
		"Send status=approved AuthorOutreach rows for one campaign. Never "
		"sends a pending row. Re-checks opt-out/suppression and the "
		"campaign's circuit breakers before every individual send; a "
		"tripped breaker halts the campaign and stops the run."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--campaign",
			required=True,
			help="AuthorOutreachCampaign.utm_campaign_slug to send.",
		)
		parser.add_argument(
			"--limit",
			type=int,
			default=None,
			help="Send at most this many rows this run.",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help=(
				"Render and list what would be sent without calling "
				"Postmark, without writing any EmailMessage row, and "
				"without changing any AuthorOutreach row's status."
			),
		)
		parser.add_argument(
			"--test-to",
			default=None,
			dest="test_to",
			help=(
				"Render one real AuthorOutreach row's content and send it "
				"to this address instead of the row's own recipient. The "
				"row's status, sent_at, and email_message are left "
				"completely untouched — this is for verifying rendering, "
				"not for advancing the queue."
			),
		)

	def handle(self, *args, **options):
		slug = options["campaign"]
		limit = options["limit"]
		dry_run = options["dry_run"]
		test_to = options["test_to"]

		try:
			campaign = AuthorOutreachCampaign.objects.get(utm_campaign_slug=slug)
		except AuthorOutreachCampaign.DoesNotExist:
			raise CommandError(f"No AuthorOutreachCampaign with utm_campaign_slug={slug!r}.")

		site = campaign.site
		custom_settings = (
			CustomSetting.objects.filter(site=site).order_by("setting_id").first()
		)
		if custom_settings is None:
			raise CommandError(f"No CustomSetting configured for site {site.domain!r}.")

		# Fail fast, before touching any row: a retrospective campaign with
		# no body_template would raise this same ValueError once per row —
		# see render_author_outreach_email's docstring for why the
		# packaged default is upcoming-mode only.
		if (
			not campaign.body_template
			and campaign.mode != AuthorOutreachCampaign.MODE_UPCOMING
		):
			raise CommandError(
				f"Campaign {slug!r} is in {campaign.mode!r} mode but has no "
				"body_template. The packaged default template's copy is "
				"upcoming-mode only — set body_template on the campaign "
				"before sending."
			)

		org_site = (
			OrganizationSite.objects.filter(site=site)
			.select_related("organization")
			.order_by("-is_default", "id")
			.first()
		)
		organization = org_site.organization if org_site else None
		postmark_api_token, api_url = get_postmark_credentials(
			custom_settings=custom_settings, organization=organization
		)
		if not postmark_api_token or not api_url:
			raise CommandError(
				f"No Postmark credentials found for site {site.domain!r}, its "
				"organisation, or Django settings."
			)

		sender_name = custom_settings.sender_name or custom_settings.title

		if test_to:
			self._send_test(
				campaign=campaign,
				site=site,
				custom_settings=custom_settings,
				sender_name=sender_name,
				postmark_api_token=postmark_api_token,
				api_url=api_url,
				test_to=test_to,
				dry_run=dry_run,
			)
			return

		if campaign.halted:
			self.stdout.write(
				self.style.ERROR(
					f"Campaign {slug!r} is halted ({campaign.halted_reason!r}). "
					"Nothing sent. Clear 'halted' in admin after investigating."
				)
			)
			return

		approved_rows = list(
			AuthorOutreach.objects.filter(
				campaign=campaign, status=AuthorOutreach.STATUS_APPROVED
			)
			# Both the send-time candidacy re-check and the renderer walk
			# row.articles; without this each row costs two extra queries.
			.prefetch_related("articles")
			.order_by("queued_at")
		)
		if limit is not None:
			approved_rows = approved_rows[:limit]

		if dry_run:
			self.stdout.write(
				self.style.WARNING(
					"DRY RUN — no email sent, no AuthorOutreach/EmailMessage rows changed"
				)
			)
			for row in approved_rows:
				self.stdout.write(f"  Would send to {row.email} (author {row.author}).")
			self.stdout.write(
				f"Would send {len(approved_rows)} email(s) for campaign {slug!r}."
			)
			return

		self._send_batch(
			campaign=campaign,
			site=site,
			custom_settings=custom_settings,
			sender_name=sender_name,
			postmark_api_token=postmark_api_token,
			api_url=api_url,
			rows=approved_rows,
		)

	# ------------------------------------------------------------------
	# --test-to
	# ------------------------------------------------------------------

	def _send_test(
		self,
		*,
		campaign,
		site,
		custom_settings,
		sender_name,
		postmark_api_token,
		api_url,
		test_to,
		dry_run,
	):
		row = (
			AuthorOutreach.objects.filter(campaign=campaign)
			.order_by("queued_at")
			.first()
		)
		if row is None:
			raise CommandError(
				f"No AuthorOutreach rows exist for campaign "
				f"{campaign.utm_campaign_slug!r} to preview."
			)

		subject, html_body, text_body = render_author_outreach_email(
			row, campaign, site, custom_settings
		)

		if dry_run:
			self.stdout.write(
				self.style.WARNING(
					f"[DRY RUN] Would send test render of row {row.pk} "
					f"(author {row.author}) to {test_to}. Subject: {subject!r}"
				)
			)
			return

		msg_token = uuid.uuid4()
		try:
			result = send_email(
				to=test_to,
				subject=subject,
				html=html_body,
				text=text_body,
				site=site,
				sender_name=sender_name,
				api_token=postmark_api_token,
				api_url=api_url,
				sender_prefix=custom_settings.sender_email_prefix,
				tag="author_outreach",
				metadata={"msg_token": str(msg_token), "campaign": campaign.utm_campaign_slug},
				reply_to=campaign.reply_to or None,
				track_opens=True,
				# HtmlOnly, not HtmlAndText: data-pm-no-track on the opt-out
				# link is HTML-only, so tracking the text body would rewrite
				# and track that URL anyway. See send_email.
				track_links="HtmlOnly",
			)
		except requests.RequestException as e:
			raise CommandError(f"Failed to send test email to {test_to}: {e}")

		# Recorded for audit (it was a real Postmark call), but never
		# attached to `row` — a --test-to send must never change the
		# row's status, sent_at, or email_message (see this command's
		# --test-to help text).
		record_sent_message(
			result,
			recipient=test_to,
			subject=subject,
			tag="author_outreach",
			site=site,
			subscriber=None,
			msg_token=msg_token,
		)

		delivered, error_code, detail = classify_postmark_response(result)
		if delivered:
			self.stdout.write(
				self.style.SUCCESS(
					f"Test email sent to {test_to} (rendered from row {row.pk}, "
					f"campaign {campaign.utm_campaign_slug!r}). Row unchanged."
				)
			)
		else:
			self.stdout.write(self.style.ERROR(f"Test email to {test_to} failed: {detail}"))

	# ------------------------------------------------------------------
	# Real batch send
	# ------------------------------------------------------------------

	def _send_batch(
		self,
		*,
		campaign,
		site,
		custom_settings,
		sender_name,
		postmark_api_token,
		api_url,
		rows,
	):
		slug = campaign.utm_campaign_slug
		sent_count = 0
		# Lazily computed on the first upcoming-mode row, then reused for the
		# rest of the run — see the re-validation block below.
		still_qualifying = None

		for index, row in enumerate(rows):
			today_start = timezone.now().replace(
				hour=0, minute=0, second=0, microsecond=0
			)
			messages_today = EmailMessage.objects.filter(
				author_outreach_rows__campaign=campaign, sent_at__gte=today_start
			).count()
			if messages_today >= campaign.daily_send_limit:
				self.stdout.write(
					self.style.WARNING(
						f"Daily send limit ({campaign.daily_send_limit}) reached "
						f"for campaign {slug!r}; stopping."
					)
				)
				break

			# Re-validate that this row's papers are still headed for the
			# next digest. Only `upcoming` mode makes a forward-looking
			# claim; a `retrospective` email says the paper *was* featured,
			# which no later change can falsify.
			#
			# Computed once per run rather than per row: the set cannot
			# meaningfully move during a run that sends a handful of
			# messages, and recomputing it per row would re-run
			# select_digest_articles for every recipient.
			#
			# ALL of the row's papers must still qualify, not merely one.
			# The email names each of them as forthcoming, so a row that
			# has lost any of its papers would send a partly false promise.
			# Prefetched above, so this costs no query.
			row_article_ids = {article.pk for article in row.articles.all()}

			# A row with no papers left would render the template's
			# multi-paper branch with an empty list — "your recent papers"
			# followed by nothing. Guard both modes, not just upcoming: a
			# retrospective row emptied by an article deletion or a manual
			# edit is just as broken, and the drift check below cannot catch
			# it (an empty set drops nothing, so it would read as "still
			# qualifying" and send).
			if not row_article_ids:
				row.status = AuthorOutreach.STATUS_PENDING
				row.approved_at = None
				row.approved_by = None
				row.error_message = (
					"Not sent: the row has no papers attached, so the email "
					"would name none. Returned to pending — re-build or "
					"cancel it."
				)
				row.save(
					update_fields=[
						"status",
						"approved_at",
						"approved_by",
						"error_message",
					]
				)
				self.stdout.write(
					self.style.WARNING(
						f"Returned {row.email} to pending: no papers attached."
					)
				)
				continue

			if campaign.mode == AuthorOutreachCampaign.MODE_UPCOMING:
				if still_qualifying is None:
					still_qualifying = currently_qualifying_article_ids(campaign)
				dropped = row_article_ids - still_qualifying
				if dropped:
					# Back to pending, not skipped or failed: the slot is
					# not burned, because nothing was sent and this author
					# may well qualify again next cycle. It needs a human
					# to look, so approval is cleared rather than kept.
					row.status = AuthorOutreach.STATUS_PENDING
					row.approved_at = None
					row.approved_by = None
					row.error_message = (
						f"Not sent: {len(dropped)} of {len(row_article_ids)} "
						"queued paper(s) are no longer in the next digest's "
						"selection, so the email's 'will be featured' claim "
						"would be false. Returned to pending for review — "
						"re-approve if they qualify again, or cancel."
					)
					row.save(
						update_fields=[
							"status",
							"approved_at",
							"approved_by",
							"error_message",
						]
					)
					self.stdout.write(
						self.style.WARNING(
							f"Returned {row.email} to pending: "
							f"{len(dropped)} queued paper(s) dropped out of "
							"the next digest since the queue was built."
						)
					)
					continue

			# Re-check the opt-out tables immediately before this send —
			# an address can have been opted out, suppressed, or
			# deactivated since the queue was built, or even since the
			# previous row in this same run.
			if is_contact_blocked(row.email):
				row.status = AuthorOutreach.STATUS_SKIPPED
				row.error_message = (
					"Recipient is opted out, suppressed, or deactivated as "
					"of send time; skipped without contacting Postmark."
				)
				row.save(update_fields=["status", "error_message"])
				self.stdout.write(
					self.style.WARNING(f"Skipped {row.email}: opted out/suppressed.")
				)
				continue

			# Then evaluate the circuit breakers — after the opt-out
			# recheck, per docs/author-outreach-spec.md "Safety limits", and
			# against a fresh copy of the campaign in case a previous
			# iteration of this same loop just halted it.
			campaign.refresh_from_db()
			if campaign.halted:
				self.stdout.write(
					self.style.ERROR(
						f"Campaign {slug!r} halted mid-run "
						f"({campaign.halted_reason!r}); stopping."
					)
				)
				break

			breaker_reason = evaluate_circuit_breakers(campaign)
			if breaker_reason:
				campaign.halted = True
				campaign.halted_at = timezone.now()
				campaign.halted_reason = breaker_reason
				campaign.save(update_fields=["halted", "halted_at", "halted_reason"])
				self.stdout.write(
					self.style.ERROR(
						f"Circuit breaker tripped for campaign {slug!r}: "
						f"{breaker_reason} Halting; nothing further sent."
					)
				)
				break

			try:
				subject, html_body, text_body = render_author_outreach_email(
					row, campaign, site, custom_settings
				)
			except ValueError as e:
				# Shouldn't happen — handle() checks this once up front —
				# but fail this row rather than crash the whole run if the
				# campaign was reconfigured mid-run.
				row.status = AuthorOutreach.STATUS_FAILED
				row.error_message = str(e)
				row.save(update_fields=["status", "error_message"])
				self.stdout.write(self.style.ERROR(f"Failed to render row {row.pk}: {e}"))
				continue

			msg_token = uuid.uuid4()
			row.status = AuthorOutreach.STATUS_SENDING
			row.save(update_fields=["status"])

			try:
				result = send_email(
					to=row.email,
					subject=subject,
					html=html_body,
					text=text_body,
					site=site,
					sender_name=sender_name,
					api_token=postmark_api_token,
					api_url=api_url,
					sender_prefix=custom_settings.sender_email_prefix,
					tag="author_outreach",
					metadata={
						"msg_token": str(msg_token),
						"campaign": slug,
					},
					reply_to=campaign.reply_to or None,
					track_opens=True,
					# HtmlOnly, not HtmlAndText: data-pm-no-track on the opt-out
				# link is HTML-only, so tracking the text body would rewrite
				# and track that URL anyway. See send_email.
				track_links="HtmlOnly",
				)
			except requests.RequestException as e:
				record_sent_message(
					None,
					recipient=row.email,
					subject=subject,
					tag="author_outreach",
					site=site,
					subscriber=None,
					msg_token=msg_token,
				)
				row.status = AuthorOutreach.STATUS_FAILED
				row.error_message = f"Connection error: {e}"
				row.email_message = EmailMessage.objects.filter(msg_token=msg_token).first()
				row.save(update_fields=["status", "error_message", "email_message"])
				self.stdout.write(self.style.ERROR(f"Failed to send to {row.email}: {e}"))
				self._throttle(campaign, index, len(rows))
				continue

			record_sent_message(
				result,
				recipient=row.email,
				subject=subject,
				tag="author_outreach",
				site=site,
				subscriber=None,
				msg_token=msg_token,
			)
			message = EmailMessage.objects.filter(msg_token=msg_token).first()

			delivered, error_code, detail = classify_postmark_response(result)
			if delivered:
				row.status = AuthorOutreach.STATUS_SENT
				row.sent_at = timezone.now()
				row.email_message = message
				row.save(update_fields=["status", "sent_at", "email_message"])
				sent_count += 1
				self.stdout.write(self.style.SUCCESS(f"Sent to {row.email}."))
			elif error_code == POSTMARK_INACTIVE_RECIPIENT:
				row.status = AuthorOutreach.STATUS_SKIPPED
				row.email_message = message
				row.error_message = detail
				row.save(update_fields=["status", "email_message", "error_message"])
				self.stdout.write(
					self.style.WARNING(f"Skipped {row.email}: Postmark 406 ({detail}).")
				)
			else:
				row.status = AuthorOutreach.STATUS_FAILED
				row.email_message = message
				row.error_message = detail
				row.save(update_fields=["status", "email_message", "error_message"])
				self.stdout.write(
					self.style.ERROR(f"Failed to send to {row.email}: {detail}.")
				)

			self._throttle(campaign, index, len(rows))

		self.stdout.write(
			self.style.SUCCESS(f"Sent {sent_count} email(s) for campaign {slug!r}.")
		)

	def _throttle(self, campaign, index, total):
		"""Sleep to stay within send_rate_per_minute, unless this was the
		last row this run (nothing left to throttle for)."""
		if index >= total - 1:
			return
		rate = campaign.send_rate_per_minute or 1
		time.sleep(60.0 / rate)
