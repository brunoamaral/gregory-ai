import logging
import requests
from datetime import timedelta
from django.utils.timezone import now
from django.core.management.base import BaseCommand
from django.template.loader import get_template
from subscriptions.management.commands.utils.send_email import send_email
from subscriptions.management.commands.utils.get_credentials import (
	build_unsubscribe_base_url,
	get_postmark_credentials,
	get_site_and_settings,
)
from subscriptions.models import (
	Lists,
	Subscribers,
	SentArticleNotification,
	SentTrialNotification,
	FailedNotification,
	SuppressionEvent,
)
from subscriptions.management.commands.utils.subscription import (
	get_latest_research_by_category,
	get_trials_for_list,
	rank_and_limit_articles,
	select_digest_articles,
)
from subscriptions.utils.email_limits import render_within_limit, resolve_limits
from subscriptions.utils.postmark import (
	POSTMARK_INACTIVE_RECIPIENT,
	classify_postmark_response,
)
from subscriptions.utils.suppression import deactivate_subscribers
from subscriptions.utils.utm import build_utm_params
from templates.emails.components.content_organizer import get_optimized_email_context

logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = """Sends a weekly digest email for all weekly digest lists.
	
	The ML prediction threshold is now configured per list in the admin interface,
	not via command line arguments. Each list can have its own threshold setting
	in the "Content Settings" section.
	
	Articles are excluded only if they are manually tagged as not relevant for ALL 
	subjects they are associated with in the specific digest list.
	
	Options:
	--days: Number of days to look back for articles. If omitted, each list uses its own `lookback_days` setting (default: 30).
	--debug: Enable detailed debugging output
	--dry-run: Simulate sending emails without actually sending them
	--all-articles: Include all unsent articles regardless of ML predictions or manual review status, ordered by most recent (but still excludes articles not relevant for all their subjects)
	
	Note: The system uses ML consensus settings configured per subject combined with
	the ML threshold configured for each list. Each subject can be configured to require
	'any', 'majority', or 'all' ML models to agree, and each model must have a 
	prediction score >= the list's ML threshold.
	"""

	def add_arguments(self, parser):
		parser.add_argument(
			"--days",
			type=int,
			default=None,
			help="Override lookback window for all lists (days). If omitted, each list uses its own lookback_days setting.",
		)
		parser.add_argument(
			"--debug", action="store_true", help="Enable detailed debugging output"
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Simulate sending emails without actually sending them or recording sent notifications",
		)
		parser.add_argument(
			"--all-articles",
			action="store_true",
			help="Include all unsent articles regardless of ML predictions or manual review status, ordered by most recent (but still excludes articles not relevant for all their subjects in the list)",
		)

	def handle(self, *args, **options):
		cli_days_override = options["days"]  # None if not passed by user
		debug = options["debug"]
		dry_run = options["dry_run"]
		all_articles = options["all_articles"]

		if debug:
			if cli_days_override is not None:
				self.stdout.write(
					self.style.NOTICE(
						f"Running with --days override: {cli_days_override}, all_articles: {all_articles}"
					)
				)
			else:
				self.stdout.write(
					self.style.NOTICE(
						f"Running with per-list lookback_days, all_articles: {all_articles}"
					)
				)
			if not all_articles:
				self.stdout.write(
					self.style.NOTICE(
						f"Sort order determined per-list; ML consensus logic used when sort_order='relevancy'"
					)
				)

		if dry_run:
			self.stdout.write(
				self.style.WARNING(
					"DRY RUN MODE: No emails will be sent and no records will be updated"
				)
			)

		if all_articles:
			self.stdout.write(
				self.style.WARNING(
					"ALL ARTICLES MODE: Including all unsent articles regardless of ML predictions or manual review (but excluding articles not relevant for all their subjects in the list)"
				)
			)

		# Step 1: Find all lists that are weekly digests
		weekly_digest_lists = Lists.objects.filter(
			weekly_digest=True, subjects__isnull=False
		).distinct()

		if not weekly_digest_lists.exists():
			self.stdout.write(
				self.style.WARNING(
					"No lists marked as weekly digest with subjects found."
				)
			)
			return

		for digest_list in weekly_digest_lists:
			# Get ML threshold, sort order, and lookback window from the list configuration
			threshold = digest_list.ml_threshold
			sort_order = digest_list.article_sort_order
			days_to_look_back = (
				cli_days_override
				if cli_days_override is not None
				else digest_list.lookback_days
			)

			# Fetch the team directly from the list
			team = digest_list.team  # Assumes Lists has a ForeignKey to Team
			email_subject = (
				digest_list.list_email_subject
				or f"Your Weekly Digest: {digest_list.list_name}"
			)

			if debug:
				self.stdout.write(
					self.style.NOTICE(
						f"Processing list '{digest_list.list_name}' - sort_order={sort_order}, ML threshold={threshold}, lookback={days_to_look_back}d"
					)
				)

			if not team:
				self.stdout.write(
					self.style.ERROR(
						f"No team associated with list '{digest_list.list_name}'. Skipping."
					)
				)
				continue
			organization = team.organization

			# Step 2: Resolve site and custom settings for this list (List.site → Org default → global)
			try:
				site, customsettings = get_site_and_settings(team, list_obj=digest_list)
			except Exception as e:
				self.stdout.write(
					self.style.ERROR(
						f"Could not resolve site/settings for team '{team.name}': {e}. Skipping list '{digest_list.list_name}'."
					)
				)
				continue

			# Resolve Postmark credentials (Site-level CustomSetting → Organization → Django settings)
			postmark_api_token, api_url = get_postmark_credentials(
				custom_settings=customsettings, organization=organization
			)
			if not postmark_api_token or not api_url:
				self.stdout.write(
					self.style.ERROR(
						f"No Postmark credentials found for site, organisation, or Django settings. Skipping list '{digest_list.list_name}'."
					)
				)
				continue

			# Step 3: Use utility functions to get articles and trials
			# Add verbose debugging to see how many articles are found
			self.stdout.write(
				self.style.NOTICE(
					f"Looking for articles for list '{digest_list.list_name}'..."
				)
			)

			mode_label = (
				"ALL ARTICLES MODE"
				if all_articles
				else "DATE SORT MODE"
				if sort_order == "date"
				else "RELEVANCY SORT MODE"
			)
			if debug:
				self.stdout.write(
					self.style.NOTICE(
						f"{mode_label}: threshold={threshold}, lookback={days_to_look_back}d"
					)
				)

			# Selection logic (candidate articles + relevancy-mode priority
			# scores) lives in select_digest_articles so send_weekly_summary
			# and the staff email preview can never drift on which articles a
			# list would actually select.
			articles, article_priority_scores = select_digest_articles(
				digest_list,
				days_to_look_back,
				all_articles=all_articles,
				threshold=threshold,
			)
			self.stdout.write(
				self.style.NOTICE(
					f"{mode_label}: Found {articles.count()} total articles (excluding articles manually tagged as not relevant for ALL their subjects in this list)"
				)
			)

			trials = get_trials_for_list(digest_list, days=days_to_look_back)
			self.stdout.write(self.style.NOTICE(f"Found {trials.count()} trials"))

			# Latest Research: new articles since the subscriber's last email,
			# grouped by category. This is the subscriber-independent candidate
			# pool (category membership + lookback window only); per-subscriber
			# sent-record exclusion happens below. Honours the list's
			# lookback_days (or --days override) rather than a fixed 30 days.
			latest_research_map_all = get_latest_research_by_category(
				digest_list, days=days_to_look_back
			)
			latest_research_categories_ordered = sorted(
				latest_research_map_all.keys(), key=lambda c: c.category_name
			)
			latest_research_pairs_all = [
				(category, article)
				for category in latest_research_categories_ordered
				for article in latest_research_map_all[category]
			]

			if (
				not articles.exists()
				and not trials.exists()
				and not latest_research_pairs_all
			):
				self.stdout.write(
					self.style.WARNING(
						f'No articles, trials, or Latest Research content found for the weekly digest list "{digest_list.list_name}". Skipping.'
					)
				)
				continue

			# article_priority_scores (relevancy mode only — None otherwise)
			# came back from select_digest_articles above, computed once per
			# list rather than per subscriber (audit P3, task 5).

			# Step 4: Find subscribers of the list (respect per-list opt-out)
			subscribers = Subscribers.objects.filter(
				active=True,
				list_subscriptions__list=digest_list,
				list_subscriptions__is_active=True,
			).distinct()

			if not subscribers.exists():
				self.stdout.write(
					self.style.WARNING(
						f'No active subscribers found for the weekly digest list "{digest_list.list_name}".'
					)
				)
				continue

			for subscriber in subscribers:
				# Step 5: Filter unsent articles and trials for the subscriber
				# The sent-record lookback must be at least as wide as the
				# content lookback window, or an article/trial sent between
				# 30 days ago and days_to_look_back ago would be treated as
				# unsent and resent every run (audit finding 11 — previously
				# masked because every list defaulted to lookback_days=30).
				threshold_date = now() - timedelta(days=max(30, days_to_look_back))
				# Not scoped to `article__in=articles`: the same sent-record set
				# also gates Latest Research below, whose candidate articles come
				# from category membership rather than the subject-matched
				# `articles` queryset.
				sent_article_ids = set(
					SentArticleNotification.objects.filter(
						list=digest_list,
						subscriber=subscriber,
						sent_at__gte=threshold_date,
					).values_list("article_id", flat=True)
				)
				unsent_articles = articles.exclude(pk__in=sent_article_ids)

				sent_trial_ids = SentTrialNotification.objects.filter(
					trial__in=trials,
					list=digest_list,
					subscriber=subscriber,
					sent_at__gte=threshold_date,
				).values_list("trial_id", flat=True)
				unsent_trials = trials.exclude(pk__in=sent_trial_ids)

				# Latest Research candidates for this subscriber: exclude
				# anything already recorded as sent (the delta definition — new
				# since the subscriber's last email). Dedup against the main
				# article pool happens in _render, once the main pool's final
				# (possibly shrunk) size is known.
				subscriber_latest_research_pairs = [
					(category, article)
					for category, article in latest_research_pairs_all
					if article.pk not in sent_article_ids
				]

				# Add debugging for the filtered unsent articles
				if debug:
					self.stdout.write(
						self.style.NOTICE(f"For subscriber {subscriber.email}:")
					)
					self.stdout.write(
						self.style.NOTICE(
							f"  - Found {len(sent_article_ids)} already sent articles"
						)
					)
					# Handle both QuerySet and list cases
					articles_count = (
						len(unsent_articles)
						if isinstance(unsent_articles, list)
						else unsent_articles.count()
					)
					trials_count = (
						len(unsent_trials)
						if isinstance(unsent_trials, list)
						else unsent_trials.count()
					)
					self.stdout.write(
						self.style.NOTICE(
							f"  - Will include {articles_count} new articles in the email"
						)
					)
					self.stdout.write(
						self.style.NOTICE(
							f"  - Will include {trials_count} new trials in the email"
						)
					)

				# Handle both QuerySet and list cases for existence check
				has_unsent_articles = (
					bool(unsent_articles)
					if isinstance(unsent_articles, list)
					else unsent_articles.exists()
				)
				has_unsent_trials = (
					bool(unsent_trials)
					if isinstance(unsent_trials, list)
					else unsent_trials.exists()
				)

				if (
					not has_unsent_articles
					and not has_unsent_trials
					and not subscriber_latest_research_pairs
				):
					self.stdout.write(
						self.style.WARNING(
							f'No new articles, trials, or Latest Research content for {subscriber.email} in list "{digest_list.list_name}".'
						)
					)
					continue

				# Step 6: Apply article limit if specified in the subscription list
				article_limit = (
					getattr(digest_list, "article_limit", 15) or 15
				)  # Default to 15 if not set or None
				# Handle both QuerySet and list cases
				articles_count = (
					len(unsent_articles)
					if isinstance(unsent_articles, list)
					else unsent_articles.count()
				)
				if articles_count > article_limit:
					# Ranking (date-order vs. relevancy priority score) lives in
					# rank_and_limit_articles, shared with the staff email
					# preview so article_limit is honoured identically there.
					unsent_articles = rank_and_limit_articles(
						unsent_articles,
						article_limit,
						sort_order,
						all_articles,
						article_priority_scores,
					)
					self.stdout.write(
						self.style.WARNING(
							f"WARNING: List '{digest_list.list_name}' had {articles_count} articles in the "
							f"{days_to_look_back}-day window; truncated to article_limit={article_limit}. "
							f"Consider shortening lookback_days or raising article_limit if this is unintended."
						)
					)
					if debug:
						self.stdout.write(
							self.style.NOTICE(
								f"Applied article limit: showing {article_limit} of {articles_count} available articles"
							)
						)

				# Cap trials the same way, so the rendered body can never exceed
				# Postmark's size limit (audit finding 1). Whatever doesn't fit
				# rolls over to the next run.
				_, trial_limit = resolve_limits(digest_list)
				trials_count = (
					len(unsent_trials)
					if isinstance(unsent_trials, list)
					else unsent_trials.count()
				)
				if trials_count > trial_limit:
					unsent_trials = list(
						unsent_trials.order_by("-discovery_date")[:trial_limit]
					)
					self.stdout.write(
						self.style.WARNING(
							f"WARNING: List '{digest_list.list_name}' had {trials_count} trials in the "
							f"{days_to_look_back}-day window; truncated to trial_limit={trial_limit}. "
							f"Consider raising trial_limit if this is unintended."
						)
					)

				# Step 7: Prepare and send the email using optimized Phase 5 rendering pipeline
				# CRITICAL FIX: Get the organized content BEFORE recording as sent
				# This ensures what we record matches what gets sent

				# Prepare UTM parameters for tracking. utm_content is a link
				# slot (article_card, trial_card, ...), never a subscriber
				# identifier — see gregory_tags.with_utm_content, which each
				# template uses to override this default per link.
				utm_params = build_utm_params("weekly_summary", digest_list, "article_card")

				_context_holder = {}

				def _render(
					articles,
					trials,
					latest_research_pairs,
					_digest_list=digest_list,
					_subscriber=subscriber,
					_organization=organization,
					_utm_params=utm_params,
				):
					# Dedup Latest Research against the main pool for *this*
					# attempt: organize_articles never drops an input article, so
					# `articles` here is exactly what ends up in
					# context["articles"] + context["additional_articles"].
					main_pks = {a.pk for a in articles}
					deduped_lr_pairs = [
						(category, article)
						for category, article in latest_research_pairs
						if article.pk not in main_pks
					]
					latest_research_category_map = {}
					for category, article in deduped_lr_pairs:
						latest_research_category_map.setdefault(category, []).append(
							article
						)

					summary_context = get_optimized_email_context(
						email_type="weekly_summary",
						articles=articles,
						trials=trials,
						subscriber=_subscriber,
						list_obj=_digest_list,
						site=site,
						custom_settings=customsettings,
						utm_params=_utm_params,
						organization=_organization,
						latest_research_category_map=latest_research_category_map,
					)

					# Inject unsubscribe context for the footer template
					summary_context["list_id"] = _digest_list.list_id
					summary_context["unsubscribe_base_url"] = (
						build_unsubscribe_base_url(site, customsettings)
					)
					summary_context["header_title"] = _digest_list.header_title or ""
					summary_context["header_tagline"] = (
						_digest_list.header_tagline or ""
					)
					summary_context["show_header_tagline"] = (
						_digest_list.show_header_tagline
					)

					html = get_template("emails/weekly_summary.html").render(
						summary_context
					)
					used_articles = list(summary_context.get("articles", [])) + list(
						summary_context.get("additional_articles", [])
					)
					used_trials = list(summary_context.get("trials", [])) + list(
						summary_context.get("additional_trials", [])
					)
					# Dedup by pk: the same article can appear under more than one
					# category if it matches more than one category's terms.
					used_latest_research = list(
						{
							article.pk: article for _, article in deduped_lr_pairs
						}.values()
					)
					_context_holder["context"] = summary_context
					return html, used_articles, used_trials, used_latest_research

				# Cap trials/articles so the rendered body can never exceed
				# Postmark's size limit (audit finding 1); shrinks further if
				# the counts above still produce an oversized body.
				try:
					(
						html_content,
						articles_to_be_sent,
						trials_to_be_sent,
						latest_research_to_be_sent,
					) = render_within_limit(
						_render,
						unsent_articles,
						unsent_trials,
						subscriber_latest_research_pairs,
					)
				except Exception as e:
					reason = (
						f"Error rendering weekly digest for list "
						f"'{digest_list.list_name}': {e}"
					)
					logger.error(reason)
					FailedNotification.objects.create(
						subscriber=subscriber, list=digest_list, reason=reason
					)
					continue

				if html_content is None:
					reason = (
						f"Rendered weekly digest for list '{digest_list.list_name}' "
						f"still exceeds the safe body size after shrinking to a "
						f"single article and a single trial."
					)
					logger.error(reason)
					FailedNotification.objects.create(
						subscriber=subscriber, list=digest_list, reason=reason
					)
					continue

				if (
					not articles_to_be_sent
					and not trials_to_be_sent
					and not latest_research_to_be_sent
				):
					reason = (
						f"Weekly digest for list '{digest_list.list_name}' organized to "
						f"zero articles, zero trials, and zero Latest Research items "
						f"for {subscriber.email}; skipping rather than sending an "
						f"empty digest."
					)
					logger.error(reason)
					FailedNotification.objects.create(
						subscriber=subscriber, list=digest_list, reason=reason
					)
					continue

				summary_context = _context_holder["context"]

				# Debug the final content that will appear in the email
				if debug:
					self.stdout.write(
						self.style.NOTICE(
							f"Final email content for subscriber {subscriber.email}:"
						)
					)
					self.stdout.write(
						self.style.NOTICE(
							f"  - Featured Articles: {len(summary_context.get('articles', []))}"
						)
					)
					self.stdout.write(
						self.style.NOTICE(
							f"  - Additional Articles: {len(summary_context.get('additional_articles', []))}"
						)
					)
					self.stdout.write(
						self.style.NOTICE(
							f"  - Featured Trials: {len(summary_context.get('trials', []))}"
						)
					)
					self.stdout.write(
						self.style.NOTICE(
							f"  - Additional Trials: {len(summary_context.get('additional_trials', []))}"
						)
					)
					self.stdout.write(
						self.style.NOTICE(
							f"  - Total articles to be sent: {len(articles_to_be_sent)}"
						)
					)
					self.stdout.write(
						self.style.NOTICE(
							f"  - Total trials to be sent: {len(trials_to_be_sent)}"
						)
					)

					# Print actual article titles
					if summary_context.get("articles"):
						self.stdout.write(self.style.NOTICE("Featured article titles:"))
						for i, article in enumerate(summary_context.get("articles")):
							self.stdout.write(
								self.style.NOTICE(
									f"    {i + 1}. {article.title[:50]}..."
								)
							)

					if summary_context.get("additional_articles"):
						self.stdout.write(
							self.style.NOTICE("Additional article titles:")
						)
						for i, article in enumerate(
							summary_context.get("additional_articles")
						):
							self.stdout.write(
								self.style.NOTICE(
									f"    {i + 1}. {article.title[:50]}..."
								)
							)

				# html_content was already rendered (possibly shrunk) by
				# render_within_limit above. Render a dedicated text template
				# from the same context rather than strip_tags(html_content),
				# which drags <style> block contents into the body and drops
				# every href.
				text_content = get_template("emails/weekly_summary.txt").render(
					summary_context
				)

				# VERIFICATION: Check that the rendered HTML actually contains the articles
				if debug:
					# Count article titles in the rendered HTML
					article_count_in_html = 0
					missing_articles = []

					for article in articles_to_be_sent:
						# Try multiple ways to find the article in HTML
						title_found = False

						# Method 1: Exact title match (first 50 chars)
						if article.title[:50] in html_content:
							title_found = True

						# Method 2: Check for title without HTML tags (escape <scp> tags)
						import html

						clean_title = html.escape(article.title[:50])
						if clean_title in html_content:
							title_found = True

						# Method 3: Check for title with HTML entities decoded
						from django.utils.html import strip_tags as strip_html_tags

						stripped_title = strip_html_tags(article.title[:50])
						if stripped_title in html_content:
							title_found = True

						# Method 4: Check for partial matches (removing problematic characters)
						safe_title = (
							article.title[:50]
							.replace("<scp>", "")
							.replace("</scp>", "")
							.replace("‐", "-")
						)
						if safe_title in html_content:
							title_found = True

						if title_found:
							article_count_in_html += 1
						else:
							missing_articles.append(article)

					self.stdout.write(
						self.style.NOTICE(
							f"VERIFICATION: {article_count_in_html} out of {len(articles_to_be_sent)} articles found in rendered HTML"
						)
					)

					# If there's a mismatch, show which articles are missing
					if missing_articles:
						self.stdout.write(
							self.style.WARNING(
								"MISMATCH DETECTED! Articles missing from HTML:"
							)
						)
						for article in missing_articles:
							self.stdout.write(
								self.style.WARNING(
									f"  - MISSING: {article.title[:50]}..."
								)
							)
							# Also show how the title appears in different formats
							import html

							self.stdout.write(
								self.style.WARNING(
									f"    * Original: {article.title[:50]}"
								)
							)
							self.stdout.write(
								self.style.WARNING(
									f"    * HTML escaped: {html.escape(article.title[:50])}"
								)
							)
							self.stdout.write(
								self.style.WARNING(
									f"    * Stripped: {strip_html_tags(article.title[:50])}"
								)
							)

					# Also check for the "No New Content This Week" message
					if "No New Content This Week" in html_content:
						self.stdout.write(
							self.style.ERROR(
								"WARNING: Email contains 'No New Content' message despite having articles!"
							)
						)

					# Save the HTML content to a file for inspection
					debug_file = (
						f"/tmp/weekly_summary_debug_{subscriber.subscriber_id}.html"
					)
					with open(debug_file, "w", encoding="utf-8") as f:
						f.write(html_content)
					self.stdout.write(
						self.style.NOTICE(f"HTML content saved to: {debug_file}")
					)

				if dry_run:
					# In dry-run mode, just log what would be sent without actually sending
					if all_articles:
						mode_info = "ALL ARTICLES mode"
					elif sort_order == "date":
						mode_info = "DATE SORT mode"
					else:
						mode_info = (
							f"RELEVANCY mode (ML consensus, threshold >= {threshold})"
						)
					self.stdout.write(
						self.style.SUCCESS(
							f'[DRY RUN] Would send weekly digest email to {subscriber.email} for list "{digest_list.list_name}" ({mode_info})'
						)
					)
					self.stdout.write(
						self.style.NOTICE(f"  - Subject: {email_subject}")
					)
					# Show the actual articles that would be sent based on content organizer
					self.stdout.write(
						self.style.NOTICE(
							f"  - Would include {len(articles_to_be_sent)} articles and {len(trials_to_be_sent)} trials"
						)
					)

					# Print more details if in debug mode
					if debug:
						self.stdout.write(self.style.NOTICE(f"  - Content summary:"))
						self.stdout.write(
							self.style.NOTICE(
								f"    * Featured Articles: {len(summary_context.get('articles', []))}"
							)
						)
						self.stdout.write(
							self.style.NOTICE(
								f"    * Additional Articles: {len(summary_context.get('additional_articles', []))}"
							)
						)
						self.stdout.write(
							self.style.NOTICE(
								f"    * Featured Trials: {len(summary_context.get('trials', []))}"
							)
						)
						self.stdout.write(
							self.style.NOTICE(
								f"    * Additional Trials: {len(summary_context.get('additional_trials', []))}"
							)
						)
					continue  # Skip to next subscriber without sending

				# If not in dry-run mode, proceed with actual sending
				try:
					result = send_email(
						to=subscriber.email,
						subject=email_subject,
						html=html_content,
						text=text_content,
						site=site,
						sender_name=customsettings.sender_name or customsettings.title,
						api_token=postmark_api_token,
						api_url=api_url,
						sender_prefix=customsettings.sender_email_prefix,
						tag="weekly_summary",
					)
				except requests.RequestException as e:
					self.stdout.write(
						self.style.ERROR(
							f"Failed to send weekly digest email to {subscriber.email} for list '{digest_list.list_name}'. Connection error: {e}"
						)
					)
					FailedNotification.objects.create(
						subscriber=subscriber,
						list=digest_list,
						reason=f"Connection error: {e}",
					)
					continue

				delivered, error_code, detail = classify_postmark_response(result)

				if delivered:
					self.stdout.write(
						self.style.SUCCESS(
							f'Weekly digest email sent to {subscriber.email} for list "{digest_list.list_name}".'
						)
					)
					# Record sent notifications for articles that were actually
					# rendered in the email — both the main section and Latest
					# Research share this table and key, so an article shown in
					# either is suppressed from both on the next run.
					recorded_articles = {
						article.pk: article
						for article in list(articles_to_be_sent)
						+ list(latest_research_to_be_sent)
					}
					new_sent_count = 0
					for article in recorded_articles.values():
						SentArticleNotification.objects.get_or_create(
							article=article, list=digest_list, subscriber=subscriber
						)
						new_sent_count += 1
					self.stdout.write(
						self.style.NOTICE(
							f"  - Recorded {new_sent_count} new sent article notifications (actually rendered in email)"
						)
					)

					new_trial_sent_count = 0
					for trial in trials_to_be_sent:
						SentTrialNotification.objects.get_or_create(
							trial=trial, list=digest_list, subscriber=subscriber
						)
						new_trial_sent_count += 1
					self.stdout.write(
						self.style.NOTICE(
							f"  - Recorded {new_trial_sent_count} new sent trial notifications"
						)
					)
				elif error_code == POSTMARK_INACTIVE_RECIPIENT:
					logger.error(
						"Subscriber %s is suppressed at Postmark (list '%s'); "
						"deactivating globally — no further emails will be sent. %s",
						subscriber.email,
						digest_list.list_name,
						detail,
					)
					deactivate_subscribers(
						[subscriber.subscriber_id],
						reason=detail,
						record_type=SuppressionEvent.RECORD_TYPE_REACTIVE_SEND_FAILURE,
					)
					FailedNotification.objects.create(
						subscriber=subscriber, list=digest_list, reason=detail
					)
				else:  # Failed delivery
					self.stdout.write(
						self.style.ERROR(
							f"Failed to send weekly digest email to {subscriber.email} for list '{digest_list.list_name}'. {detail}"
						)
					)
					FailedNotification.objects.create(
						subscriber=subscriber, list=digest_list, reason=detail
					)
