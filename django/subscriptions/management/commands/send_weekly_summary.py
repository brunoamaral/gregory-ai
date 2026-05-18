from datetime import timedelta
from django.utils.timezone import now
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import get_template
from django.utils.html import strip_tags
from subscriptions.management.commands.utils.send_email import send_email
from subscriptions.management.commands.utils.subscription import (
	get_articles_for_list,
	get_trials_for_list,
	get_latest_research_by_category,
)
from gregory.models import Articles, Authors, Trials, MLPredictions
from subscriptions.management.commands.utils.get_credentials import get_postmark_credentials, get_site_and_settings
from subscriptions.models import (
	Lists,
	Subscribers,
	SentArticleNotification,
	SentTrialNotification,
	FailedNotification,
)
from django.db.models import Q, Exists, OuterRef
from django.utils.timezone import now
from templates.emails.components.content_organizer import get_optimized_email_context

class Command(BaseCommand):

	@staticmethod
	def _filter_articles_excluding_all_irrelevant(base_qs, digest_list):
		"""
		Return a list of PKs from base_qs, excluding articles that are manually
		tagged as not-relevant for ALL of their subjects that appear in digest_list.
		Articles with no relevance records are always included.

		Uses prefetch_related to load subjects and relevance records in bulk,
		avoiding N+1 / N*M queries.
		"""
		list_subject_ids = set(digest_list.subjects.values_list('id', flat=True))
		# Prefetch both relations so the inner loop hits no extra queries.
		articles = base_qs.prefetch_related('subjects', 'article_subject_relevances')
		filtered_pks = []
		for article in articles:
			# In-memory filter: only subjects shared with this digest list.
			article_list_subjects = [s for s in article.subjects.all() if s.pk in list_subject_ids]
			explicit_irrelevant_count = 0
			total_relevance_records = 0
			for subject in article_list_subjects:
				# Use the prefetch cache — no extra query per subject.
				relevance = next(
					(r for r in article.article_subject_relevances.all() if r.subject_id == subject.pk),
					None,
				)
				if relevance is not None:
					total_relevance_records += 1
					if relevance.is_relevant is False:
						explicit_irrelevant_count += 1
			if total_relevance_records > 0 and explicit_irrelevant_count == total_relevance_records:
				continue
			filtered_pks.append(article.pk)
		return filtered_pks

	help = '''Sends a weekly digest email for all weekly digest lists.
	
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
	'''
	
	def add_arguments(self, parser):
		parser.add_argument(
			'--days',
			type=int,
			default=None,
			help='Override lookback window for all lists (days). If omitted, each list uses its own lookback_days setting.'
		)
		parser.add_argument(
			'--debug',
			action='store_true',
			help='Enable detailed debugging output'
		)
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Simulate sending emails without actually sending them or recording sent notifications'
		)
		parser.add_argument(
			'--all-articles',
			action='store_true',
			help='Include all unsent articles regardless of ML predictions or manual review status, ordered by most recent (but still excludes articles not relevant for all their subjects in the list)'
		)

	def handle(self, *args, **options):
		cli_days_override = options['days']  # None if not passed by user
		debug = options['debug']
		dry_run = options['dry_run']
		all_articles = options['all_articles']

		if debug:
			if cli_days_override is not None:
				self.stdout.write(self.style.NOTICE(f"Running with --days override: {cli_days_override}, all_articles: {all_articles}"))
			else:
				self.stdout.write(self.style.NOTICE(f"Running with per-list lookback_days, all_articles: {all_articles}"))
			if not all_articles:
				self.stdout.write(self.style.NOTICE(f"Sort order determined per-list; ML consensus logic used when sort_order='relevancy'"))
		
		if dry_run:
			self.stdout.write(self.style.WARNING("DRY RUN MODE: No emails will be sent and no records will be updated"))
		
		if all_articles:
			self.stdout.write(self.style.WARNING("ALL ARTICLES MODE: Including all unsent articles regardless of ML predictions or manual review (but excluding articles not relevant for all their subjects in the list)"))

		# Step 1: Find all lists that are weekly digests
		weekly_digest_lists = Lists.objects.filter(weekly_digest=True, subjects__isnull=False).distinct()

		if not weekly_digest_lists.exists():
			self.stdout.write(self.style.WARNING('No lists marked as weekly digest with subjects found.'))
			return

		for digest_list in weekly_digest_lists:
			# Get ML threshold, sort order, and lookback window from the list configuration
			threshold = digest_list.ml_threshold
			sort_order = digest_list.article_sort_order
			days_to_look_back = cli_days_override if cli_days_override is not None else digest_list.lookback_days
			
			# Fetch the team directly from the list
			team = digest_list.team  # Assumes Lists has a ForeignKey to Team
			email_subject = digest_list.list_email_subject or f'Your Weekly Digest: {digest_list.list_name}'
			
			if debug:
				self.stdout.write(
					self.style.NOTICE(
						f"Processing list '{digest_list.list_name}' - sort_order={sort_order}, ML threshold={threshold}, lookback={days_to_look_back}d"
					)
				)
			
			if not team:
				self.stdout.write(self.style.ERROR(f"No team associated with list '{digest_list.list_name}'. Skipping."))
				continue
			if not getattr(team, 'organization_id', None):
				raise CommandError(
					f"Team '{team.name}' (id={team.pk}) has no organization FK. "
					"Teams must always belong to an organization."
				)
			try:
				organization = team.organization
			except ObjectDoesNotExist as exc:
				raise CommandError(
					f"Team '{team.name}' (id={team.pk}) points to a missing organization. "
					"Fix orphan teams before sending summaries."
				) from exc

			# Step 2: Resolve site and custom settings for this list (List.site → Org default → global)
			try:
				site, customsettings = get_site_and_settings(team, list_obj=digest_list)
			except Exception as e:
				self.stdout.write(self.style.ERROR(f"Could not resolve site/settings for team '{team.name}': {e}. Skipping list '{digest_list.list_name}'."))
				continue

			# Resolve Postmark credentials (Site-level CustomSetting → Organization → Django settings)
			postmark_api_token, api_url = get_postmark_credentials(custom_settings=customsettings, organization=organization)
			if not postmark_api_token or not api_url:
				self.stdout.write(self.style.ERROR(f"No Postmark credentials found for site, organisation, or Django settings. Skipping list '{digest_list.list_name}'."))
				continue

			# Step 3: Use utility functions to get articles and trials
			# Add verbose debugging to see how many articles are found
			self.stdout.write(self.style.NOTICE(f"Looking for articles for list '{digest_list.list_name}'..."))
			
			if all_articles:
				# --all-articles CLI flag: include everything regardless of ML/manual, ordered newest first.
				base_articles = Articles.objects.filter(
					subjects__in=digest_list.subjects.all(),
					discovery_date__gte=now() - timedelta(days=days_to_look_back)
				).order_by('-discovery_date').distinct()
				filtered_pks = self._filter_articles_excluding_all_irrelevant(base_articles, digest_list)
				articles = Articles.objects.filter(pk__in=filtered_pks).order_by('-discovery_date')
				self.stdout.write(self.style.NOTICE(f"ALL ARTICLES MODE: Found {articles.count()} total articles (excluding articles manually tagged as not relevant for ALL their subjects in this list)"))
				
			elif sort_order == 'date':
				# DATE SORT MODE: include all subject-matched articles (no ML filtering), ordered newest first.
				if debug:
					self.stdout.write(self.style.NOTICE(f"DATE SORT MODE: Skipping ML relevance filtering for list '{digest_list.list_name}' but still excluding articles manually tagged as not relevant for ALL their subjects in this list"))
				base_articles = Articles.objects.filter(
					subjects__in=digest_list.subjects.all(),
					discovery_date__gte=now() - timedelta(days=days_to_look_back)
				).order_by('-discovery_date').distinct()
				filtered_pks = self._filter_articles_excluding_all_irrelevant(base_articles, digest_list)
				articles = Articles.objects.filter(pk__in=filtered_pks).order_by('-discovery_date')
				self.stdout.write(self.style.NOTICE(f"DATE SORT MODE: Found {articles.count()} total articles (excluding articles manually tagged as not relevant for ALL their subjects in this list)"))
				
			else:
				# RELEVANCY MODE (default): manually reviewed OR ML-relevant based on consensus settings.
				if debug:
					self.stdout.write(self.style.NOTICE(f"RELEVANCY SORT MODE: Using ML consensus logic with threshold={threshold}"))
				base_subject_articles = Articles.objects.filter(
					subjects__in=digest_list.subjects.all(),
					discovery_date__gte=now() - timedelta(days=days_to_look_back)
				).order_by('-discovery_date').distinct()
				filtered_article_ids = self._filter_articles_excluding_all_irrelevant(base_subject_articles, digest_list)
				subject_articles = Articles.objects.filter(pk__in=filtered_article_ids)
				self.stdout.write(self.style.NOTICE(f"RELEVANCY SORT MODE: Found {subject_articles.count()} articles by subject (after excluding articles manually tagged as not relevant for ALL their subjects in this list)"))
				
				# Then, get manually reviewed articles (only those tagged as relevant for at least one subject)
				manual_reviewed = Articles.objects.filter(
					subjects__in=digest_list.subjects.all(),
					article_subject_relevances__subject__in=digest_list.subjects.all(),
					article_subject_relevances__is_relevant=True,
					discovery_date__gte=now() - timedelta(days=days_to_look_back)
				).distinct()
				self.stdout.write(self.style.NOTICE(f"Found {manual_reviewed.count()} manually reviewed articles (tagged as relevant for at least one subject)"))
				
				# Get articles that are ML-relevant based on new consensus logic
				ml_relevant_articles = []
				for article in subject_articles:
					if article.is_ml_relevant_any_subject(threshold=threshold):
						ml_relevant_articles.append(article.article_id)
				
				ml_predicted = Articles.objects.filter(pk__in=ml_relevant_articles)
				self.stdout.write(self.style.NOTICE(f"Found {ml_predicted.count()} articles meeting ML consensus criteria (threshold >= {threshold})"))
				
				# Debugging: Check ML consensus for some articles
				if debug:
					sample_articles = subject_articles.order_by('-discovery_date')[:5]  # Get 5 most recent articles
					self.stdout.write(self.style.NOTICE(f"ML consensus evaluation for recent articles (threshold >= {threshold}):"))
					for article in sample_articles:
						self.stdout.write(self.style.NOTICE(f"  Article {article.article_id}: {article.title[:50]}..."))
						
						# Check relevance for each subject this article belongs to
						article_subjects = article.subjects.filter(auto_predict=True)
						if article_subjects.exists():
							for subject in article_subjects:
								is_relevant = article.is_ml_relevant_for_subject(subject, threshold=threshold)
								consensus_type = subject.ml_consensus_type
								
								# Get prediction details for this subject
								high_confidence_predictions = article.ml_predictions_detail.filter(
									subject=subject,
									predicted_relevant=True,
									probability_score__gte=threshold
								).values_list('algorithm', flat=True)
								high_confidence_count = len(set(high_confidence_predictions)) if high_confidence_predictions else 0
								
								# Also show all predictions (regardless of threshold) for context
								all_predictions = article.ml_predictions_detail.filter(
									subject=subject,
									predicted_relevant=True
								)
								all_scores = [(pred.algorithm, pred.probability_score) for pred in all_predictions]
								
								self.stdout.write(self.style.NOTICE(f"    - Subject: {subject.subject_name} (consensus: {consensus_type})"))
								self.stdout.write(self.style.NOTICE(f"      * {high_confidence_count}/3 models >= {threshold} threshold → {'INCLUDED' if is_relevant else 'EXCLUDED'}"))
								if all_scores:
									scores_text = ", ".join([f"{alg}: {score:.2f}" for alg, score in all_scores])
									self.stdout.write(self.style.NOTICE(f"      * All scores: {scores_text}"))
						else:
							self.stdout.write(self.style.NOTICE(f"    - No subjects with auto_predict=True"))
				
				# Combine manually reviewed OR ML-relevant based on consensus and threshold
				article_ids = list(manual_reviewed.values_list('pk', flat=True)) + ml_relevant_articles
				articles = Articles.objects.filter(pk__in=article_ids).distinct()
				self.stdout.write(self.style.NOTICE(f"Filtered by manual review or ML consensus (>= {threshold}): {articles.count()} articles"))
				
				self.stdout.write(self.style.NOTICE(f"Final combined query found {articles.count()} articles"))
			
			# Use the helper function to get trials, but pass the days_to_look_back parameter
			trials = Trials.objects.filter(
				subjects__in=digest_list.subjects.all(),
				discovery_date__gte=now() - timedelta(days=days_to_look_back)
			).distinct()
			self.stdout.write(self.style.NOTICE(f"Found {trials.count()} trials"))

			if not articles.exists() and not trials.exists():
				self.stdout.write(self.style.WARNING(f'No articles or trials found for the weekly digest list "{digest_list.list_name}". Skipping.'))
				continue

			# Step 4: Find subscribers of the list (respect per-list opt-out)
			subscribers = Subscribers.objects.filter(
				active=True,
				list_subscriptions__list=digest_list,
				list_subscriptions__is_active=True,
			).distinct()

			if not subscribers.exists():
				self.stdout.write(self.style.WARNING(f'No active subscribers found for the weekly digest list "{digest_list.list_name}".'))
				continue

			for subscriber in subscribers:
				# Step 5: Filter unsent articles and trials for the subscriber
				threshold_date = now() - timedelta(days=30)
				sent_article_ids = SentArticleNotification.objects.filter(
					article__in=articles,
					list=digest_list,
					subscriber=subscriber,
					sent_at__gte=threshold_date
				).values_list('article_id', flat=True)
				unsent_articles = articles.exclude(pk__in=sent_article_ids)

				sent_trial_ids = SentTrialNotification.objects.filter(
					trial__in=trials,
					list=digest_list,
					subscriber=subscriber,
					sent_at__gte=threshold_date
				).values_list('trial_id', flat=True)
				unsent_trials = trials.exclude(pk__in=sent_trial_ids)
				
				# Add debugging for the filtered unsent articles
				if debug:
					self.stdout.write(self.style.NOTICE(f"For subscriber {subscriber.email}:"))
					self.stdout.write(self.style.NOTICE(f"  - Found {len(sent_article_ids)} already sent articles"))
					# Handle both QuerySet and list cases
					articles_count = len(unsent_articles) if isinstance(unsent_articles, list) else unsent_articles.count()
					trials_count = len(unsent_trials) if isinstance(unsent_trials, list) else unsent_trials.count()
					self.stdout.write(self.style.NOTICE(f"  - Will include {articles_count} new articles in the email"))
					self.stdout.write(self.style.NOTICE(f"  - Will include {trials_count} new trials in the email"))

				# Handle both QuerySet and list cases for existence check
				has_unsent_articles = bool(unsent_articles) if isinstance(unsent_articles, list) else unsent_articles.exists()
				has_unsent_trials = bool(unsent_trials) if isinstance(unsent_trials, list) else unsent_trials.exists()
				
				if not has_unsent_articles and not has_unsent_trials:
					self.stdout.write(self.style.WARNING(f'No new articles or trials for {subscriber.email} in list "{digest_list.list_name}".'))
					continue

				# Step 6: Apply article limit if specified in the subscription list
				article_limit = getattr(digest_list, 'article_limit', 15) or 15  # Default to 15 if not set or None
				# Handle both QuerySet and list cases
				articles_count = len(unsent_articles) if isinstance(unsent_articles, list) else unsent_articles.count()
				if articles_count > article_limit:
					if all_articles or sort_order == 'date':
						# Date-ordered modes: slice newest first
						limited_articles = unsent_articles.order_by('-discovery_date')[:article_limit]
						self.stdout.write(self.style.WARNING(
							f"WARNING: List '{digest_list.list_name}' had {articles_count} articles in the "
							f"{days_to_look_back}-day window; truncated to article_limit={article_limit}. "
							f"Consider shortening lookback_days or raising article_limit if this is unintended."
						))
						if debug:
							mode_label = 'ALL ARTICLES' if all_articles else 'DATE SORT'
							self.stdout.write(self.style.NOTICE(f"Applied article limit in {mode_label} mode: showing {article_limit} most recent articles out of {articles_count} available"))
					else:
						# Relevancy mode: order by manual relevance first, then ML consensus count, then date
						manual_relevant_ids = set(Articles.objects.filter(
							pk__in=[a.pk for a in unsent_articles],
							article_subject_relevances__is_relevant=True
						).values_list('pk', flat=True))

						article_priorities = []
						for article in unsent_articles:
							priority_score = 0
							if article.pk in manual_relevant_ids:
								priority_score += 1000
							for subject in article.subjects.filter(auto_predict=True):
								predictions = article.ml_predictions_detail.filter(
									subject=subject,
									predicted_relevant=True,
									probability_score__gte=threshold
								).values_list('algorithm', flat=True)
								relevant_count = len(set(predictions)) if predictions else 0
								priority_score += relevant_count * 100
							article_priorities.append((article, priority_score))

						article_priorities.sort(key=lambda x: (-x[1], -x[0].discovery_date.timestamp()))
						limited_articles = [item[0] for item in article_priorities[:article_limit]]
						self.stdout.write(self.style.WARNING(
							f"WARNING: List '{digest_list.list_name}' had {articles_count} articles in the "
							f"{days_to_look_back}-day window; truncated to article_limit={article_limit}. "
							f"Consider shortening lookback_days or raising article_limit if this is unintended."
						))
						if debug:
							self.stdout.write(self.style.NOTICE(f"Applied article limit: showing {article_limit} highest-priority articles (manual + ML consensus >= {threshold}) out of {articles_count} available"))
							for i, (article, score) in enumerate(article_priorities[:min(5, article_limit)]):
								manual_flag = "✓" if article.pk in manual_relevant_ids else "✗"
								self.stdout.write(self.style.NOTICE(f"  {i+1}. Score {score}: Manual {manual_flag} | {article.title[:40]}..."))

					# Convert to list to avoid "Cannot filter a query once a slice has been taken" error
					unsent_articles = list(limited_articles)

				# Step 7: Prepare and send the email using optimized Phase 5 rendering pipeline
				# CRITICAL FIX: Get the organized content BEFORE recording as sent
				# This ensures what we record matches what gets sent
				
				# Prepare UTM parameters for tracking
				utm_params = {
					'utm_source': 'weekly_summary',
					'utm_medium': 'email',
					'utm_campaign': f'weekly_summary_{digest_list.list_name.lower().replace(" ", "_")}',
					'utm_content': f'subscriber_{subscriber.subscriber_id}'
				}
				
				summary_context = get_optimized_email_context(
					email_type='weekly_summary',
					articles=unsent_articles,
					trials=unsent_trials,
					subscriber=subscriber,
					list_obj=digest_list,
					site=site,
					custom_settings=customsettings,
					utm_params=utm_params,
					organization=organization,
				)

				# Inject unsubscribe context for the footer template
				# Always use site.domain (the domain the list is linked to) so that
				# all footer links are consistent with Lists.site.
				# Strip whitespace to guard against accidental spaces in Site.domain.
				_domain = site.domain.strip()
				_scheme = 'https' if _domain not in ('localhost', '127.0.0.1') else 'http'
				summary_context['list_id'] = digest_list.list_id
				summary_context['unsubscribe_base_url'] = f"{_scheme}://{_domain}"
				summary_context['header_title'] = digest_list.header_title or ''
				summary_context['header_tagline'] = digest_list.header_tagline or ''
				summary_context['show_header_tagline'] = digest_list.show_header_tagline
				
				# Extract the actual articles that will be rendered in the email
				articles_to_be_sent = list(summary_context.get('articles', [])) + list(summary_context.get('additional_articles', []))
				trials_to_be_sent = list(summary_context.get('trials', [])) + list(summary_context.get('additional_trials', []))
				
				# Debug the final content that will appear in the email
				if debug:
					self.stdout.write(self.style.NOTICE(f"Final email content for subscriber {subscriber.email}:"))
					self.stdout.write(self.style.NOTICE(f"  - Featured Articles: {len(summary_context.get('articles', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Additional Articles: {len(summary_context.get('additional_articles', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Featured Trials: {len(summary_context.get('trials', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Additional Trials: {len(summary_context.get('additional_trials', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Total articles to be sent: {len(articles_to_be_sent)}"))
					self.stdout.write(self.style.NOTICE(f"  - Total trials to be sent: {len(trials_to_be_sent)}"))
					
					# Print actual article titles
					if summary_context.get('articles'):
						self.stdout.write(self.style.NOTICE("Featured article titles:"))
						for i, article in enumerate(summary_context.get('articles')):
							self.stdout.write(self.style.NOTICE(f"    {i+1}. {article.title[:50]}..."))
					
					if summary_context.get('additional_articles'):
						self.stdout.write(self.style.NOTICE("Additional article titles:"))
						for i, article in enumerate(summary_context.get('additional_articles')):
							self.stdout.write(self.style.NOTICE(f"    {i+1}. {article.title[:50]}..."))

				html_content = get_template('emails/weekly_summary.html').render(summary_context)
				text_content = strip_tags(html_content)
				
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
						safe_title = article.title[:50].replace('<scp>', '').replace('</scp>', '').replace('‐', '-')
						if safe_title in html_content:
							title_found = True
						
						if title_found:
							article_count_in_html += 1
						else:
							missing_articles.append(article)
					
					self.stdout.write(self.style.NOTICE(f"VERIFICATION: {article_count_in_html} out of {len(articles_to_be_sent)} articles found in rendered HTML"))
					
					# If there's a mismatch, show which articles are missing
					if missing_articles:
						self.stdout.write(self.style.WARNING("MISMATCH DETECTED! Articles missing from HTML:"))
						for article in missing_articles:
							self.stdout.write(self.style.WARNING(f"  - MISSING: {article.title[:50]}..."))
							# Also show how the title appears in different formats
							import html
							self.stdout.write(self.style.WARNING(f"    * Original: {article.title[:50]}"))
							self.stdout.write(self.style.WARNING(f"    * HTML escaped: {html.escape(article.title[:50])}"))
							self.stdout.write(self.style.WARNING(f"    * Stripped: {strip_html_tags(article.title[:50])}"))
					
					# Also check for the "No New Content This Week" message
					if "No New Content This Week" in html_content:
						self.stdout.write(self.style.ERROR("WARNING: Email contains 'No New Content' message despite having articles!"))
					
					# Save the HTML content to a file for inspection
					import os
					debug_file = f"/tmp/weekly_summary_debug_{subscriber.subscriber_id}.html"
					with open(debug_file, 'w', encoding='utf-8') as f:
						f.write(html_content)
					self.stdout.write(self.style.NOTICE(f"HTML content saved to: {debug_file}"))

				if dry_run:
					# In dry-run mode, just log what would be sent without actually sending
					if all_articles:
						mode_info = "ALL ARTICLES mode"
					elif sort_order == 'date':
						mode_info = "DATE SORT mode"
					else:
						mode_info = f"RELEVANCY mode (ML consensus, threshold >= {threshold})"
					self.stdout.write(self.style.SUCCESS(f'[DRY RUN] Would send weekly digest email to {subscriber.email} for list "{digest_list.list_name}" ({mode_info})'))
					self.stdout.write(self.style.NOTICE(f'  - Subject: {email_subject}'))
					# Show the actual articles that would be sent based on content organizer
					self.stdout.write(self.style.NOTICE(f'  - Would include {len(articles_to_be_sent)} articles and {len(trials_to_be_sent)} trials'))
					
					# Print more details if in debug mode
					if debug:
						self.stdout.write(self.style.NOTICE(f'  - Content summary:'))
						self.stdout.write(self.style.NOTICE(f'    * Featured Articles: {len(summary_context.get("articles", []))}'))
						self.stdout.write(self.style.NOTICE(f'    * Additional Articles: {len(summary_context.get("additional_articles", []))}'))
						self.stdout.write(self.style.NOTICE(f'    * Featured Trials: {len(summary_context.get("trials", []))}'))
						self.stdout.write(self.style.NOTICE(f'    * Additional Trials: {len(summary_context.get("additional_trials", []))}'))
					continue  # Skip to next subscriber without sending

				# If not in dry-run mode, proceed with actual sending
				result = send_email(
					to=subscriber.email,
					subject=email_subject,
					html=html_content,
					text=text_content,
					site=site,
					sender_name=customsettings.title,
					api_token=postmark_api_token,
					api_url=api_url,
					sender_prefix=customsettings.sender_email_prefix,
				)

				if result and result.status_code == 200:
					response_data = result.json()
					error_code = response_data.get("ErrorCode", 0)
					message = response_data.get("Message", "Unknown error")

					if error_code == 0:  # Successful delivery
						self.stdout.write(self.style.SUCCESS(f'Weekly digest email sent to {subscriber.email} for list "{digest_list.list_name}".'))
						# Record sent notifications for articles that were actually sent in the email
						new_sent_count = 0
						for article in articles_to_be_sent:
							SentArticleNotification.objects.get_or_create(
								article=article,
								list=digest_list,
								subscriber=subscriber
							)
							new_sent_count += 1
						self.stdout.write(self.style.NOTICE(f'  - Recorded {new_sent_count} new sent article notifications (actually rendered in email)'))
						
						new_trial_sent_count = 0
						for trial in trials_to_be_sent:
							SentTrialNotification.objects.get_or_create(
								trial=trial,
								list=digest_list,
								subscriber=subscriber
							)
							new_trial_sent_count += 1
						self.stdout.write(self.style.NOTICE(f'  - Recorded {new_trial_sent_count} new sent trial notifications'))
					else:  # Failed delivery
						self.stdout.write(self.style.ERROR(f"Failed to send weekly digest email to {subscriber.email} for list '{digest_list.list_name}'. Reason: {message}"))
						FailedNotification.objects.create(
							subscriber=subscriber,
							list=digest_list,
							reason=message
						)
				else:
					# Enhanced error handling for non-200 status codes
					error_details = f"HTTP Status {result.status_code}"
					
					# For 422 errors, extract detailed Postmark error information
					if result.status_code == 422:
						try:
							error_response = result.json()
							error_code = error_response.get("ErrorCode", "Unknown")
							error_message = error_response.get("Message", "No details provided")
							error_details = f"422 Unprocessable Entity - ErrorCode: {error_code}, Message: {error_message}"
						except (ValueError, KeyError):
							error_details = f"422 Unprocessable Entity - Unable to parse error details"
					
					self.stdout.write(self.style.ERROR(f"Failed to send weekly digest email to {subscriber.email} for list '{digest_list.list_name}'. {error_details}"))
					FailedNotification.objects.create(
						subscriber=subscriber,
						list=digest_list,
						reason=error_details
					)
