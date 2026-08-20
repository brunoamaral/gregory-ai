import uuid
from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.functions import Lower
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from django.contrib.sites.models import Site
from django.utils.text import slugify
from django_ckeditor_5.fields import CKEditor5Field
from gregory.models import Articles, Trials, Team
from simple_history.models import HistoricalRecords


class Lists(models.Model):
	list_id = models.AutoField(primary_key=True)
	list_name = models.CharField(max_length=150, null=False, blank=False)
	list_description = models.CharField(max_length=150, null=True, blank=True)
	list_email_subject = models.CharField(max_length=150, null=True, blank=True)
	subjects = models.ManyToManyField("gregory.Subject", blank=True)
	# Set purposes of the list
	admin_summary = models.BooleanField(default=False)
	weekly_digest = models.BooleanField(default=False)
	clinical_trials_notifications = models.BooleanField(default=False)
	# Article limit for weekly digest, admin summary, and trial notification emails
	article_limit = models.PositiveIntegerField(
		default=15,
		null=True,
		blank=True,
		help_text=(
			"Maximum number of articles to include in a single email (default: 15). "
			"Applies to weekly digest, admin summary, and trial notification emails. "
			"Articles that do not fit roll over to the next send."
		),
		verbose_name="Article limit per email",
	)
	trial_limit = models.PositiveIntegerField(
		default=15,
		null=True,
		blank=True,
		help_text="Maximum number of clinical trials to include in a single email (default: 15). Trials that do not fit roll over to the next send.",
		verbose_name="Trial limit per email",
	)
	trial_max_age_days = models.PositiveIntegerField(
		default=90,
		null=True,
		blank=True,
		validators=[MinValueValidator(1), MaxValueValidator(3650)],
		help_text=(
			"Skip trials whose own registration or publication date is older than this. "
			"Guards against bulk imports of historical trials flooding a newsletter, "
			"because discovery_date only records when GregoryAI first saw the row. "
			"Leave blank to disable the check."
		),
		verbose_name="Maximum trial age (days)",
	)
	article_max_age_days = models.PositiveIntegerField(
		default=90,
		null=True,
		blank=True,
		validators=[MinValueValidator(1), MaxValueValidator(3650)],
		help_text=(
			"Skip articles whose own published_date is older than this. Guards "
			"against bulk imports of historical articles flooding a newsletter, "
			"because discovery_date only records when GregoryAI first saw the "
			"row. Articles with no published_date are always kept. Leave blank "
			"to disable the check."
		),
		verbose_name="Maximum article age (days)",
	)

	# ML threshold for relevance filtering
	ml_threshold = models.FloatField(
		default=0.8,
		validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
		help_text="ML prediction confidence threshold (0.0-1.0). Only articles with ML predictions above this threshold will be considered relevant.",
		verbose_name="ML Threshold for Relevance",
	)
	# Lookback window for weekly digest, admin summary, and trial notification emails
	lookback_days = models.PositiveIntegerField(
		default=30,
		validators=[MinValueValidator(1), MaxValueValidator(365)],
		help_text=(
			"How many days back to look for articles and trials. Applies to "
			"weekly digest, admin summary, and trial notification emails. "
			"Common values: 8, 15, 30. Maximum 365."
		),
		verbose_name="Lookback Window (days)",
	)
	# Article sort order for weekly digest emails
	ARTICLE_SORT_CHOICES = [
		("relevancy", "Relevancy (manual + ML consensus, ranked)"),
		("date", "Date (all subject-matched articles, newest first)"),
	]
	article_sort_order = models.CharField(
		max_length=20,
		choices=ARTICLE_SORT_CHOICES,
		default="relevancy",
		help_text=(
			"How articles are selected and ordered in weekly digest emails. "
			"'relevancy' applies ML/manual filtering and priority ranking. "
			"'date' includes all subject-matched articles ordered by discovery date (newest first)."
		),
		verbose_name="Article Sort Order",
	)
	# Latest research categories
	latest_research_categories = models.ManyToManyField(
		"gregory.TeamCategory",
		blank=True,
		related_name="latest_research_lists",
		verbose_name="Latest Research Categories",
		help_text="Select team categories to include in the 'Latest Research' section of weekly digest emails.",
	)
	team = models.ForeignKey(
		Team,
		on_delete=models.CASCADE,
		related_name="lists",
		null=False,
		blank=False,
		help_text="The team this list belongs to.",
	)
	site = models.ForeignKey(
		Site,
		on_delete=models.PROTECT,
		related_name="lists",
		null=False,
		blank=True,
		help_text="The site this list sends emails from. Determines footer branding, links, and unsubscribe URLs. Auto-populated from the organisation default site if not set.",
	)
	# Email header customization
	header_title = models.CharField(
		max_length=200,
		null=True,
		blank=True,
		help_text='Optional override for the email header title (defaults to "Gregory AI").',
	)
	header_tagline = models.CharField(
		max_length=300,
		null=True,
		blank=True,
		help_text="Optional tagline to display in the email header.",
	)
	show_header_tagline = models.BooleanField(
		default=True, help_text="Show the tagline in the email header."
	)
	utm_campaign_slug = models.CharField(
		max_length=100,
		blank=True,
		validators=[
			RegexValidator(
				r"^[a-z0-9_-]+$",
				"Only lowercase letters, numbers, hyphens, and underscores are allowed.",
			)
		],
		help_text=(
			"Stable identifier used as utm_campaign in outbound email links. "
			"Set once from the list name when the list is created and never "
			"changed automatically afterwards, so renaming the list does not "
			"fork its analytics history in Umami. Changing this value "
			"manually forks the history just the same — only do it "
			"deliberately, and note the cutover date."
		),
		verbose_name="UTM campaign slug",
	)

	def save(self, *args, **kwargs):
		if not self.site_id:
			from gregory.models import OrganizationSite

			org_site = (
				OrganizationSite.objects.filter(organization=self.team.organization)
				.order_by("-is_default", "id")
				.select_related("site")
				.first()
			)
			if org_site:
				self.site = org_site.site
			else:
				self.site = Site.objects.get_current()
		if not self.utm_campaign_slug:
			# list_name allows up to 150 chars but utm_campaign_slug is
			# capped at 100 — truncate so a long name can't raise an
			# IntegrityError on save.
			self.utm_campaign_slug = slugify(self.list_name)[:100]
		super().save(*args, **kwargs)

	class Meta:
		managed = True
		verbose_name_plural = "lists"

	def __str__(self):
		return f"{self.list_name} (Team: {self.team.name})"


class Subscribers(models.Model):
	subscriber_id = models.AutoField(primary_key=True)
	first_name = models.CharField(max_length=150, null=False, blank=False)
	last_name = models.CharField(max_length=150, null=True, blank=True)
	email = models.EmailField(max_length=254, unique=True, null=False, blank=False)
	active = models.BooleanField(
		default=True,
		help_text=(
			"Global email switch for this subscriber. When unchecked, they receive NO "
			"emails from any list — a master opt-out that is independent of the individual "
			"list subscriptions below. It is set automatically when someone unsubscribes "
			"from everything, and manually via the 'Disable all emails' admin action. "
			"Per-list opt-outs do NOT change this flag. Note: the 'Total Active "
			"Subscribers' analytics chart and snapshot KPIs count someone as 'active' by "
			"whether they hold at least one active list subscription, not by this flag, so "
			"those numbers can differ from the active/inactive account counts. (Other "
			"analytics surfaces, such as the profile and recent-subscriber views, do still "
			"filter on this flag.)"
		),
	)
	is_admin = models.BooleanField(default=False)
	unsubscribe_token = models.UUIDField(
		default=uuid.uuid4,
		unique=True,
		editable=False,
		help_text="Unique token used in unsubscribe links. Never expose this outside of email context.",
	)
	subscriptions = models.ManyToManyField(
		Lists, through="ListSubscription", blank=True
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	history = HistoricalRecords()

	class Meta:
		managed = True
		verbose_name_plural = "subscribers"
		db_table = "subscribers"
		constraints = [UniqueConstraint(Lower("email"), name="unique_lower_email")]

	def __str__(self):
		return f"{self.first_name} {self.last_name} ({self.email})"

	def save(self, *args, **kwargs):
		self.email = self.email.lower()
		super(Subscribers, self).save(*args, **kwargs)


class SentArticleNotification(models.Model):
	article = models.ForeignKey(Articles, on_delete=models.CASCADE)
	list = models.ForeignKey(Lists, on_delete=models.CASCADE)
	subscriber = models.ForeignKey(Subscribers, on_delete=models.CASCADE)
	sent_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = ("article", "list", "subscriber")
		verbose_name_plural = "sent article notifications"

	def __str__(self):
		return f"Article {self.article_id} sent to {self.list_id}, subscriber {self.subscriber_id}"


class SentTrialNotification(models.Model):
	trial = models.ForeignKey(Trials, on_delete=models.CASCADE)
	list = models.ForeignKey(Lists, on_delete=models.CASCADE)
	subscriber = models.ForeignKey(Subscribers, on_delete=models.CASCADE)
	sent_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = ("trial", "list", "subscriber")
		verbose_name_plural = "sent trial notifications"

	def __str__(self):
		return f"Trial {self.trial_id} sent to {self.list_id}, subscriber {self.subscriber_id}"


class FailedNotification(models.Model):
	subscriber = models.ForeignKey(Subscribers, on_delete=models.CASCADE)
	list = models.ForeignKey(Lists, on_delete=models.CASCADE)
	reason = models.TextField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		verbose_name_plural = "Failed Notifications"

	def __str__(self):
		return f"Failed notification to {self.subscriber.email} for list {self.list.list_name}"


class ListSubscription(models.Model):
	"""Through-model for the Subscribers ↔ Lists M2M, storing consent and per-list opt-out."""

	CONSENT_METHOD_CHOICES = [
		("web_form", "Web Form"),
		("admin", "Admin"),
		("api", "API"),
		("import", "Import"),
	]

	subscriber = models.ForeignKey(
		Subscribers, on_delete=models.CASCADE, related_name="list_subscriptions"
	)
	list = models.ForeignKey(
		Lists, on_delete=models.CASCADE, related_name="list_subscriptions"
	)
	subscribed_at = models.DateTimeField(auto_now_add=True)
	# GDPR consent fields
	consent_ip = models.GenericIPAddressField(
		null=True,
		blank=True,
		help_text="IP address at the time of subscription (for GDPR audit trail).",
	)
	consent_source_site = models.ForeignKey(
		Site,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="list_subscriptions",
		help_text="The site the subscription form was submitted from.",
	)
	consent_method = models.CharField(
		max_length=20,
		choices=CONSENT_METHOD_CHOICES,
		default="web_form",
		help_text="How this subscription was obtained.",
	)
	# Per-list unsubscribe
	is_active = models.BooleanField(
		default=True,
		help_text="Uncheck to unsubscribe this subscriber from this specific list.",
	)
	unsubscribed_at = models.DateTimeField(
		null=True,
		blank=True,
		help_text="Timestamp when the subscriber opted out of this list.",
	)
	history = HistoricalRecords()

	class Meta:
		unique_together = ("subscriber", "list")
		verbose_name = "List Subscription"
		verbose_name_plural = "List Subscriptions"

	def __str__(self):
		status = "active" if self.is_active else "unsubscribed"
		return f"{self.subscriber.email} → {self.list.list_name} ({status})"


class SubscriberSiteProfile(models.Model):
	"""Per-site profile for a subscriber."""

	PROFILEOPTIONS = [
		("patient", "Patient"),
		("caregiver", "Caregiver"),
		("doctor", "Doctor"),
		("nurse", "Nurse"),
		("clinical centre", "Clinical Centre"),
		("researcher", "Researcher"),
		("other", "Other"),
	]

	subscriber = models.ForeignKey(
		Subscribers,
		on_delete=models.CASCADE,
		related_name="site_profiles",
	)
	site = models.ForeignKey(
		Site,
		on_delete=models.CASCADE,
		related_name="subscriber_profiles",
	)
	profile = models.CharField(
		choices=PROFILEOPTIONS,
		max_length=50,
		default="",
		help_text="The subscriber's role on this specific site.",
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		unique_together = ("subscriber", "site")
		verbose_name = "Subscriber Site Profile"
		verbose_name_plural = "Subscriber Site Profiles"

	def __str__(self):
		return f"{self.subscriber.email} @ {self.site.domain} ({self.profile})"


class Announcement(models.Model):
	STATUS_CHOICES = [
		("draft", "Draft"),
		("queued", "Queued"),
		("sending", "Sending"),
		("sent", "Sent"),
		("failed", "Failed"),
	]

	subject = models.CharField(
		max_length=200,
		help_text="Email subject line for this announcement.",
	)
	header_title = models.CharField(
		max_length=200,
		blank=True,
		default="",
		help_text="Replaces the Gregory AI title in the email header. Leave blank to use the default site title.",
		verbose_name="Header Title",
	)
	header_tagline = models.CharField(
		max_length=200,
		blank=True,
		default="",
		help_text="Optional subtitle/tagline shown below the site name in the email header. Leave blank for default.",
		verbose_name="Header Tagline",
	)
	show_header_tagline = models.BooleanField(
		default=True,
		help_text="Uncheck to hide the tagline from the email header entirely.",
		verbose_name="Show Header Tagline",
	)
	preheader_text = models.CharField(
		max_length=200,
		blank=True,
		default="",
		help_text="Short preview text shown in email clients before the email is opened. Leave blank for a default message.",
		verbose_name="Preheader Text",
	)
	body = CKEditor5Field(
		config_name="default",
		help_text="The content of the announcement email.",
	)
	organization = models.ForeignKey(
		"organizations.Organization",
		on_delete=models.PROTECT,
		related_name="announcements",
		help_text="The organization that owns this announcement. "
		"Determines who can see and edit it.",
	)
	lists = models.ManyToManyField(
		Lists,
		related_name="announcements",
		help_text="Select one or more lists to send this announcement to.",
	)
	created_by = models.ForeignKey(
		"auth.User",
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="announcements",
	)
	created_at = models.DateTimeField(auto_now_add=True)
	status = models.CharField(
		max_length=10,
		choices=STATUS_CHOICES,
		default="draft",
	)
	sent_at = models.DateTimeField(null=True, blank=True)
	recipients_count = models.PositiveIntegerField(default=0)
	failures_count = models.PositiveIntegerField(default=0)

	class Meta:
		ordering = ["-created_at"]
		verbose_name = "Announcement"
		verbose_name_plural = "Announcements"

	def __str__(self):
		return f"{self.subject} ({self.get_status_display()})"


class SuppressionEvent(models.Model):
	"""
	Audit trail for Postmark's suppress / unsuppress signal, and the local
	equivalents (a reactive 406 on send, an admin "Disable all emails" bulk
	action). One row per event.

	This is the prerequisite for reactivation: `deactivated_list_subscription_ids`
	is the exact set of `ListSubscription` rows a suppression turned off, so an
	unsuppress can restore precisely that set instead of guessing. See
	docs/subscriptions.md#suppression-and-reactivation-webhook for the policy this
	implements.
	"""

	RECORD_TYPE_SUBSCRIPTION_CHANGE = "SubscriptionChange"
	RECORD_TYPE_REACTIVE_SEND_FAILURE = "ReactiveSendFailure"
	RECORD_TYPE_ADMIN_MANUAL = "AdminManual"
	RECORD_TYPE_CHOICES = [
		(RECORD_TYPE_SUBSCRIPTION_CHANGE, "Subscription Change (Postmark webhook)"),
		(RECORD_TYPE_REACTIVE_SEND_FAILURE, "Reactive send failure (406)"),
		(RECORD_TYPE_ADMIN_MANUAL, "Manual admin action"),
	]

	ACTION_SUPPRESSED = "suppressed"
	ACTION_AUTO_RESTORED = "auto_restored"
	ACTION_RECORD_ONLY = "record_only"
	ACTION_CHOICES = [
		(ACTION_SUPPRESSED, "Suppressed"),
		(ACTION_AUTO_RESTORED, "Auto-restored"),
		(ACTION_RECORD_ONLY, "Recorded only (not acted on)"),
	]

	subscriber = models.ForeignKey(
		Subscribers,
		on_delete=models.CASCADE,
		null=True,
		blank=True,
		related_name="suppression_events",
		help_text="Null when the recipient doesn't match any Subscribers row.",
	)
	email = models.EmailField(
		help_text="Recipient email as reported by the triggering event; kept "
		"even when it doesn't match a Subscribers row.",
	)
	record_type = models.CharField(
		max_length=40,
		choices=RECORD_TYPE_CHOICES,
		default=RECORD_TYPE_ADMIN_MANUAL,
	)
	suppress_sending = models.BooleanField(
		default=True,
		help_text="True = a suppression event; False = an unsuppress/reactivation signal.",
	)
	suppression_reason = models.CharField(
		max_length=40,
		blank=True,
		default="",
		help_text="Postmark SuppressionReason (HardBounce, SpamComplaint, "
		"ManualSuppression) when known; blank otherwise.",
	)
	origin = models.CharField(
		max_length=20,
		blank=True,
		default="",
		help_text="Postmark Origin field (Recipient/Customer), recorded as data only "
		"— not used to gate reactivation (see docs/subscriptions.md).",
	)
	message_stream = models.CharField(max_length=40, blank=True, default="")
	message_id = models.CharField(
		max_length=64,
		blank=True,
		default="",
		help_text="Postmark MessageID. Often an all-zero placeholder for "
		"Subscription Change events — not used for idempotency.",
	)
	changed_at = models.DateTimeField(
		help_text="Event time (Postmark ChangedAt, or local event time for "
		"reactive/admin events). Used for ordering and the reactivation "
		"staleness cap.",
	)
	raw_payload = models.JSONField(null=True, blank=True)
	deactivated_list_subscription_ids = models.JSONField(
		default=list,
		blank=True,
		help_text="ListSubscription IDs this event deactivated. Reactivation "
		"restores exactly this set and nothing else.",
	)
	action_taken = models.CharField(
		max_length=20,
		choices=ACTION_CHOICES,
		default=ACTION_SUPPRESSED,
	)
	flag_reason = models.TextField(
		blank=True,
		default="",
		help_text="Why this event was recorded but not acted on automatically.",
	)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["record_type", "email", "changed_at"],
				name="unique_suppression_event_record_type_email_changed_at",
			)
		]
		indexes = [
			# postmark_webhook.py looks up the latest event for a recipient via
			# filter(email=...).order_by("-changed_at") on every incoming event
			# (ordering check, original-suppression lookup) — the unique
			# constraint above is keyed on record_type first, so it doesn't
			# serve an email-only query efficiently as this table grows.
			# Name kept under 30 characters: Django enforces that limit on index
			# names for cross-database portability (models.E034), and the check
			# only runs when a database is involved — `manage.py check` alone
			# passes, `migrate` does not.
			models.Index(
				fields=["email", "-changed_at"],
				name="idx_supp_event_email_changed",
			),
		]
		ordering = ["-changed_at"]
		verbose_name = "Suppression Event"
		verbose_name_plural = "Suppression Events"

	def __str__(self):
		direction = "suppress" if self.suppress_sending else "unsuppress"
		return f"{self.email} {direction} ({self.record_type}) @ {self.changed_at:%Y-%m-%d %H:%M}"


class EmailMessage(models.Model):
	"""
	One row per message handed to Postmark, written at send time by every
	sender (weekly digest, admin summary, trial notification, announcement,
	and — from PR 3 of AUTHOR-OUTREACH-PLAN.md onward — author outreach) via
	subscriptions.management.commands.utils.send_email.record_sent_message.
	See docs/subscriptions.md for the full webhook contract this feeds.

	Two independent keys let a later Postmark webhook call (see
	subscriptions.utils.email_events.handle_email_event) correlate an event
	back to this row: `msg_token`, an opaque UUID echoed in the Postmark
	`Metadata` field on sends that opt in (nobody yet in PR 1 — the four
	existing senders don't pass metadata, so correlation for them happens on
	`message_id` alone), and `message_id`, Postmark's own `MessageID` taken
	from the send response. Neither key, nor anything else on this row, is a
	person identifier beyond the recipient address already stored here.

	`delivered_at`, `first_opened_at`, `bounced_at`, `complained_at`, and
	`bounce_type` are aggregate outcome fields written by the webhook: the
	latest Delivery/Bounce/SpamComplaint event of that kind, or the first
	Open (Postmark is configured for first-open tracking only). The full,
	append-only per-event history lives in EmailEvent instead — these fields
	exist so a bounce-rate/complaint-rate check can read one row per message
	rather than aggregate the event log every time.

	Retention: prune_email_messages, default 730 days, which must never
	delete a row an AuthorOutreach (PR 3) references — see that command's
	docstring.
	"""

	msg_token = models.UUIDField(
		default=uuid.uuid4,
		unique=True,
		editable=False,
		help_text="Opaque per-message id, echoed back in Postmark's Metadata "
		"field for sends that opt in. Never a resolvable person identifier.",
	)
	message_id = models.CharField(
		max_length=64,
		blank=True,
		default="",
		db_index=True,
		help_text="Postmark's MessageID from the send response. Blank when "
		"the send failed before Postmark returned one.",
	)
	recipient = models.EmailField(db_index=True)
	subject = models.CharField(max_length=255, blank=True, default="")
	tag = models.CharField(
		max_length=40,
		blank=True,
		default="",
		db_index=True,
		help_text="Postmark Tag — the email type, e.g. weekly_summary, "
		"admin_summary, trial_notification, announcement.",
	)
	message_stream = models.CharField(max_length=40, blank=True, default="")
	site = models.ForeignKey(
		Site,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="email_messages",
	)
	subscriber = models.ForeignKey(
		Subscribers,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="email_messages",
	)
	sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
	accepted = models.BooleanField(
		default=False,
		help_text="True when Postmark accepted the message for delivery "
		"(HTTP 200, no ErrorCode).",
	)
	error_code = models.IntegerField(
		null=True, blank=True, help_text="Postmark ErrorCode when not accepted."
	)
	error_message = models.TextField(blank=True, default="")
	# Aggregate outcome, written by subscriptions.utils.email_events.handle_email_event.
	delivered_at = models.DateTimeField(null=True, blank=True)
	first_opened_at = models.DateTimeField(null=True, blank=True)
	bounced_at = models.DateTimeField(null=True, blank=True)
	complained_at = models.DateTimeField(null=True, blank=True)
	bounce_type = models.CharField(max_length=40, blank=True, default="")

	class Meta:
		verbose_name = "Email Message"
		verbose_name_plural = "Email Messages"
		ordering = ["-sent_at"]

	def __str__(self):
		return f"{self.recipient} [{self.tag or 'untagged'}] @ {self.sent_at:%Y-%m-%d %H:%M}"


class EmailEvent(models.Model):
	"""
	Append-only log of Postmark webhook events for messages this system
	sent — alongside, not instead of, SuppressionEvent, which continues to
	drive suppression/reactivation state on its own (see
	subscriptions.utils.postmark_webhook). Written by
	subscriptions.utils.email_events.handle_email_event.

	Deliberately NOT stored here, and why (mirrors the "Deliberately NOT
	implemented here, and why" block at the top of
	subscriptions/utils/postmark_webhook.py):

	- `Geo`, `IP`, `UserAgent`, `OS`, `Client`, `Platform`, `ReadSeconds`:
	  Open and Click payloads carry these for a named recipient — for
	  author outreach (AUTHOR-OUTREACH-PLAN.md, PR 3+) a researcher who
	  never consented to being tracked at all. This table exists to answer
	  "was this message delivered / opened / clicked / bounced", not "from
	  where, on what device, for how long". Storing them would be
	  collecting personal data with no purpose behind the collection.
	- Bounce `Content`: the full original message body, embedded by
	  Postmark in Bounce payloads. Never stored — it would duplicate
	  personal data already at rest elsewhere (the rendered email itself)
	  for no purpose this table needs to serve.
	- The raw payload, in full: only the named fields below are stored, so
	  a new field Postmark starts sending tomorrow can't leak PII into this
	  table by accident — storage here is an explicit decision per field,
	  never "whatever the payload happened to contain".

	record_type spans all six event types subscriptions.views.postmark_webhook
	dispatches on: five that reach only this model (Delivery, Bounce,
	SpamComplaint, Open, Click), plus SubscriptionChange — handled first,
	unchanged, by subscriptions.utils.postmark_webhook.handle_subscription_change
	(suppression/reactivation state), and additionally logged here so the
	complete inbound event history lives in one place.
	"""

	RECORD_TYPE_DELIVERY = "Delivery"
	RECORD_TYPE_BOUNCE = "Bounce"
	RECORD_TYPE_SPAM_COMPLAINT = "SpamComplaint"
	RECORD_TYPE_OPEN = "Open"
	RECORD_TYPE_CLICK = "Click"
	RECORD_TYPE_SUBSCRIPTION_CHANGE = "SubscriptionChange"
	RECORD_TYPE_CHOICES = [
		(RECORD_TYPE_DELIVERY, "Delivery"),
		(RECORD_TYPE_BOUNCE, "Bounce"),
		(RECORD_TYPE_SPAM_COMPLAINT, "Spam Complaint"),
		(RECORD_TYPE_OPEN, "Open"),
		(RECORD_TYPE_CLICK, "Click"),
		(RECORD_TYPE_SUBSCRIPTION_CHANGE, "Subscription Change"),
	]
	# Convenience set for callers validating a payload's RecordType before
	# doing any work — kept next to the choices so the two can't drift.
	RECORD_TYPES = frozenset(
		{
			RECORD_TYPE_DELIVERY,
			RECORD_TYPE_BOUNCE,
			RECORD_TYPE_SPAM_COMPLAINT,
			RECORD_TYPE_OPEN,
			RECORD_TYPE_CLICK,
			RECORD_TYPE_SUBSCRIPTION_CHANGE,
		}
	)

	# Named email_message, not message: a FK named `message` would collide
	# with the message_id CharField below (Django auto-derives a `message_id`
	# attribute for a FK field literally named `message`) — models.E006.
	# Matches the FK name AuthorOutreach uses for the same relationship
	# (AUTHOR-OUTREACH-PLAN.md, PR 3).
	email_message = models.ForeignKey(
		EmailMessage,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="events",
		help_text="Null when this event's msg_token/MessageID matched no "
		"EmailMessage row — the event is still recorded.",
	)
	record_type = models.CharField(max_length=20, choices=RECORD_TYPE_CHOICES)
	message_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
	recipient = models.EmailField(db_index=True)
	occurred_at = models.DateTimeField(
		db_index=True,
		help_text="Postmark's own event timestamp (DeliveredAt / BouncedAt / "
		"ReceivedAt / ChangedAt, depending on record_type).",
	)
	received_at = models.DateTimeField(
		auto_now_add=True, help_text="When this webhook call was processed locally."
	)
	tag = models.CharField(max_length=40, blank=True, default="")
	message_stream = models.CharField(max_length=40, blank=True, default="")
	# Bounce / SpamComplaint only.
	bounce_type = models.CharField(
		max_length=40, blank=True, default="", help_text="Postmark Type."
	)
	bounce_type_code = models.IntegerField(
		null=True, blank=True, help_text="Postmark TypeCode."
	)
	details = models.TextField(
		blank=True,
		default="",
		help_text="Postmark Details. Never the message Content, which "
		"Postmark also embeds in Bounce payloads.",
	)
	# Click only.
	link_url = models.URLField(
		max_length=1000,
		blank=True,
		default="",
		help_text="Postmark OriginalLink — our own URL, not a tracking link.",
	)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["record_type", "message_id", "occurred_at"],
				name="unique_email_event_type_msgid_occurred",
			)
		]
		ordering = ["-occurred_at"]
		verbose_name = "Email Event"
		verbose_name_plural = "Email Events"

	def __str__(self):
		return f"{self.record_type} — {self.recipient} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class AnnouncementRecipient(models.Model):
	announcement = models.ForeignKey(
		Announcement,
		on_delete=models.CASCADE,
		related_name="recipients",
	)
	subscriber = models.ForeignKey(
		Subscribers,
		on_delete=models.CASCADE,
		related_name="announcement_recipients",
	)
	list = models.ForeignKey(
		Lists,
		on_delete=models.CASCADE,
		related_name="announcement_recipients",
	)
	sent_at = models.DateTimeField(auto_now_add=True)
	success = models.BooleanField(default=True)
	suppressed = models.BooleanField(
		default=False,
		help_text=(
			"True when this recipient was skipped because Postmark reports them "
			"as suppressed (ErrorCode 406), not because of a delivery error. "
			"Kept separate from `success` so a suppressed recipient does not "
			"count toward the announcement's failures_count."
		),
	)
	error_message = models.TextField(blank=True, default="")

	class Meta:
		unique_together = ("announcement", "subscriber")
		verbose_name = "Announcement Recipient"
		verbose_name_plural = "Announcement Recipients"

	def __str__(self):
		status = "OK" if self.success else "FAILED"
		return f"{self.subscriber.email} [{status}]"
