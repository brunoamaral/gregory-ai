"""
Tests for subscriptions.utils.email_events.handle_email_event — the append-
only EmailEvent log fed by five Postmark webhook record types (Delivery,
Bounce, SpamComplaint, Open, Click) plus a second, additional record of
SubscriptionChange (whose suppression/reactivation handling is untouched —
see test_postmark_webhook.py for that).

Payload shapes below are taken from Postmark's own documented examples
(developer.postmarkapp.com/developer/webhooks/...) rather than assumed —
Bounce/SpamComplaint use "Email" for the recipient address, not "Recipient"
like every other event type, which is easy to get backwards.
"""

from django.contrib.sites.models import Site
from django.test import TestCase

from subscriptions.models import EmailEvent, EmailMessage
from subscriptions.utils.email_events import handle_email_event


def _delivery_payload(**overrides):
	payload = {
		"RecordType": "Delivery",
		"MessageStream": "broadcast",
		"MessageID": "883953f4-6105-42a2-a16a-77a8eac79483",
		"Recipient": "john@example.com",
		"Tag": "weekly_summary",
		"DeliveredAt": "2026-08-01T12:00:00Z",
		"Details": "Test delivery webhook details",
		"Metadata": {},
	}
	payload.update(overrides)
	return payload


def _bounce_payload(**overrides):
	payload = {
		"RecordType": "Bounce",
		"MessageStream": "broadcast",
		"ID": 4323372036854775807,
		"Type": "HardBounce",
		"TypeCode": 1,
		"Name": "Hard bounce",
		"Tag": "weekly_summary",
		"MessageID": "883953f4-6105-42a2-a16a-77a8eac79483",
		"ServerID": 23,
		"Description": "The server was unable to deliver your message.",
		"Details": "Test bounce details",
		"Email": "john@example.com",
		"From": "gregory@example.com",
		"BouncedAt": "2026-08-01T12:00:00Z",
		"DumpAvailable": True,
		"Inactive": True,
		"CanActivate": True,
		"Subject": "Test subject",
		"Content": "<Full dump of original message, never stored>",
		"Metadata": {},
	}
	payload.update(overrides)
	return payload


def _spam_complaint_payload(**overrides):
	payload = {
		"RecordType": "SpamComplaint",
		"MessageStream": "broadcast",
		"ID": 42,
		"Type": "SpamComplaint",
		"TypeCode": 512,
		"Name": "Spam complaint",
		"Tag": "weekly_summary",
		"MessageID": "883953f4-6105-42a2-a16a-77a8eac79483",
		"ServerID": 1234,
		"Description": "",
		"Details": "Test spam complaint details",
		"Email": "john@example.com",
		"From": "gregory@example.com",
		"BouncedAt": "2026-08-01T12:00:00Z",
		"DumpAvailable": True,
		"Inactive": True,
		"CanActivate": False,
		"Subject": "Test subject",
		"Content": "<Abuse report dump, never stored>",
		"Metadata": {},
	}
	payload.update(overrides)
	return payload


def _open_payload(**overrides):
	"""
	Full Open payload per Postmark's documented example, plus the extra
	fields (ReadSeconds) their tracking docs also describe. Used verbatim by
	the privacy regression test below — every one of these device/location
	fields must NOT survive into the stored EmailEvent row.
	"""
	payload = {
		"RecordType": "Open",
		"MessageStream": "broadcast",
		"FirstOpen": True,
		"Client": {
			"Name": "Chrome 35.0.1916.153",
			"Company": "Google",
			"Family": "Chrome",
		},
		"OS": {
			"Name": "OS X 10.7 Lion",
			"Company": "Apple Computer, Inc.",
			"Family": "OS X 10",
		},
		"Platform": "WebMail",
		"UserAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_7_5) AppleWebKit/537.36",
		"ReadSeconds": 12,
		"Geo": {
			"CountryISOCode": "RS",
			"Country": "Serbia",
			"RegionISOCode": "VO",
			"Region": "Autonomna Pokrajina Vojvodina",
			"City": "Novi Sad",
			"Zip": "21000",
			"Coords": "45.2517,19.8369",
			"IP": "188.2.95.4",
		},
		"MessageID": "883953f4-6105-42a2-a16a-77a8eac79483",
		"Metadata": {},
		"ReceivedAt": "2026-08-01T12:00:00Z",
		"Tag": "author_outreach",
		"Recipient": "researcher@example.com",
	}
	payload.update(overrides)
	return payload


def _click_payload(**overrides):
	payload = {
		"RecordType": "Click",
		"MessageStream": "broadcast",
		"ClickLocation": "HTML",
		"Client": {"Name": "Chrome", "Company": "Google", "Family": "Chrome"},
		"OS": {"Name": "OS X", "Company": "Apple", "Family": "OS X"},
		"Platform": "Desktop",
		"UserAgent": "Mozilla/5.0",
		"OriginalLink": "https://example.com/articles/123?utm_source=author_outreach",
		"Geo": {
			"CountryISOCode": "RS",
			"Country": "Serbia",
			"IP": "8.8.8.8",
		},
		"MessageID": "883953f4-6105-42a2-a16a-77a8eac79483",
		"Metadata": {},
		"ReceivedAt": "2026-08-01T12:00:00Z",
		"Tag": "weekly_summary",
		"Recipient": "john@example.com",
	}
	payload.update(overrides)
	return payload


def _subscription_change_payload(**overrides):
	payload = {
		"RecordType": "SubscriptionChange",
		"MessageStream": "broadcast",
		"Recipient": "john@example.com",
		"SuppressSending": True,
		"SuppressionReason": "HardBounce",
		"Origin": "Recipient",
		"MessageID": "00000000-0000-0000-0000-000000000000",
		"ChangedAt": "2026-08-01T12:00:00Z",
	}
	payload.update(overrides)
	return payload


class EmailEventPerRecordTypeTest(TestCase):
	"""Each of the six record types produces exactly one EmailEvent."""

	def test_delivery_creates_one_event_with_expected_fields(self):
		event = handle_email_event(_delivery_payload())

		self.assertIsNotNone(event)
		self.assertEqual(EmailEvent.objects.count(), 1)
		self.assertEqual(event.record_type, EmailEvent.RECORD_TYPE_DELIVERY)
		self.assertEqual(event.recipient, "john@example.com")
		self.assertEqual(event.tag, "weekly_summary")
		self.assertEqual(event.message_stream, "broadcast")
		self.assertEqual(
			event.message_id, "883953f4-6105-42a2-a16a-77a8eac79483"
		)

	def test_bounce_reads_recipient_from_email_field_not_recipient(self):
		"""
		Regression guard for the "Email" vs "Recipient" quirk: Bounce (and
		SpamComplaint) payloads carry the recipient address in "Email", not
		"Recipient" like every other event type.
		"""
		event = handle_email_event(_bounce_payload())

		self.assertIsNotNone(event)
		self.assertEqual(event.record_type, EmailEvent.RECORD_TYPE_BOUNCE)
		self.assertEqual(event.recipient, "john@example.com")
		self.assertEqual(event.bounce_type, "HardBounce")
		self.assertEqual(event.bounce_type_code, 1)
		self.assertEqual(event.details, "Test bounce details")

	def test_spam_complaint_reads_recipient_from_email_field(self):
		event = handle_email_event(_spam_complaint_payload())

		self.assertIsNotNone(event)
		self.assertEqual(event.record_type, EmailEvent.RECORD_TYPE_SPAM_COMPLAINT)
		self.assertEqual(event.recipient, "john@example.com")
		self.assertEqual(event.bounce_type, "SpamComplaint")
		self.assertEqual(event.bounce_type_code, 512)

	def test_open_creates_one_event(self):
		event = handle_email_event(_open_payload())

		self.assertIsNotNone(event)
		self.assertEqual(event.record_type, EmailEvent.RECORD_TYPE_OPEN)
		self.assertEqual(event.recipient, "researcher@example.com")
		self.assertEqual(event.tag, "author_outreach")

	def test_click_creates_one_event_with_link_url(self):
		event = handle_email_event(_click_payload())

		self.assertIsNotNone(event)
		self.assertEqual(event.record_type, EmailEvent.RECORD_TYPE_CLICK)
		self.assertEqual(
			event.link_url,
			"https://example.com/articles/123?utm_source=author_outreach",
		)

	def test_subscription_change_also_creates_an_event(self):
		event = handle_email_event(_subscription_change_payload())

		self.assertIsNotNone(event)
		self.assertEqual(event.record_type, EmailEvent.RECORD_TYPE_SUBSCRIPTION_CHANGE)
		self.assertEqual(event.recipient, "john@example.com")

	def test_unrecognised_record_type_is_a_no_op(self):
		event = handle_email_event({"RecordType": "SomethingNew"})
		self.assertIsNone(event)
		self.assertEqual(EmailEvent.objects.count(), 0)


class ReplayIsANoOpTest(TestCase):
	def test_replayed_payload_does_not_duplicate(self):
		payload = _delivery_payload()

		first = handle_email_event(payload)
		second = handle_email_event(dict(payload))

		self.assertIsNotNone(first)
		self.assertIsNone(second)
		self.assertEqual(EmailEvent.objects.count(), 1)

	def test_replay_across_all_six_record_types(self):
		payloads = [
			_delivery_payload(),
			_bounce_payload(),
			_spam_complaint_payload(),
			_open_payload(),
			_click_payload(),
			_subscription_change_payload(),
		]
		for payload in payloads:
			handle_email_event(dict(payload))
		self.assertEqual(EmailEvent.objects.count(), 6)

		for payload in payloads:
			handle_email_event(dict(payload))
		self.assertEqual(
			EmailEvent.objects.count(),
			6,
			"replaying the same six payloads must not create new rows",
		)


class PrivacyRegressionGuardTest(TestCase):
	"""
	Constraint from AUTHOR-OUTREACH-SPEC.md: EmailEvent must never store
	Geo, IP, UserAgent, OS, Client, Platform, ReadSeconds, or any raw
	payload. Feed a full Open payload (every one of those fields present,
	as Postmark actually sends them) and assert none of it survives.
	"""

	def test_open_payload_leaves_no_ip_coordinates_or_user_agent(self):
		payload = _open_payload()
		event = handle_email_event(payload)

		self.assertIsNotNone(event)

		# Nothing on the model has a field for any of these at all.
		forbidden_field_names = {
			"geo",
			"ip",
			"ip_address",
			"user_agent",
			"os",
			"client",
			"platform",
			"read_seconds",
			"raw_payload",
			"payload",
		}
		actual_field_names = {f.name.lower() for f in EmailEvent._meta.get_fields()}
		self.assertEqual(
			forbidden_field_names & actual_field_names,
			set(),
			"EmailEvent must not define any of the omitted tracking fields",
		)

		# Belt and braces: serialise every stored value and confirm none of
		# the sensitive payload content leaked into a field that does exist
		# (e.g. into `details`, which is legitimately used by Bounce/
		# SpamComplaint but must stay empty here).
		stored_values = " ".join(
			str(getattr(event, f.name))
			for f in EmailEvent._meta.get_fields()
			if hasattr(f, "attname")
		)
		# ReadSeconds (an int) is deliberately excluded from this list: it's
		# too small/common a value to substring-match safely against a blob
		# that also contains timestamps and row ids (e.g. "12" trivially
		# collides with "...12:00:00..."). Its absence is already proven
		# above — no field named read_seconds exists at all — so
		# hasattr(event, "read_seconds") backs that up directly instead.
		self.assertFalse(hasattr(event, "read_seconds"))

		sensitive_fragments = [
			payload["Geo"]["IP"],
			payload["Geo"]["Coords"],
			payload["Geo"]["City"],
			payload["UserAgent"],
			payload["Platform"],
			payload["OS"]["Name"],
			payload["Client"]["Name"],
		]
		for fragment in sensitive_fragments:
			self.assertNotIn(
				fragment,
				stored_values,
				f"{fragment!r} from the Open payload must not be stored anywhere on EmailEvent",
			)

	def test_bounce_content_dump_is_never_stored(self):
		payload = _bounce_payload()
		event = handle_email_event(payload)

		self.assertIsNotNone(event)
		self.assertNotIn("Content", [f.name for f in EmailEvent._meta.get_fields()])
		self.assertNotIn(payload["Content"], event.details)


class UnmatchedMessageIdTest(TestCase):
	def test_event_with_no_matching_email_message_still_records_with_null_message(self):
		# No EmailMessage exists at all -- simulates a message sent before
		# this feature shipped, or a MessageID this system never sent.
		event = handle_email_event(_delivery_payload())

		self.assertIsNotNone(event)
		self.assertIsNone(event.email_message)
		self.assertEqual(
			event.message_id, "883953f4-6105-42a2-a16a-77a8eac79483"
		)


class CorrelationAndAggregateUpdateTest(TestCase):
	def setUp(self):
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]

	def test_delivery_matches_by_message_id_and_sets_delivered_at(self):
		message = EmailMessage.objects.create(
			message_id="883953f4-6105-42a2-a16a-77a8eac79483",
			recipient="john@example.com",
			tag="weekly_summary",
			message_stream="broadcast",
			site=self.site,
			accepted=True,
		)

		event = handle_email_event(_delivery_payload())

		message.refresh_from_db()
		self.assertEqual(event.email_message_id, message.pk)
		self.assertIsNotNone(message.delivered_at)

	def test_matches_by_msg_token_in_metadata_even_without_message_id_match(self):
		message = EmailMessage.objects.create(
			message_id="a-different-message-id",
			recipient="john@example.com",
			tag="weekly_summary",
			message_stream="broadcast",
			site=self.site,
			accepted=True,
		)

		payload = _delivery_payload(
			MessageID="yet-another-id",
			Metadata={"msg_token": str(message.msg_token), "campaign": "test"},
		)
		event = handle_email_event(payload)

		self.assertEqual(event.email_message_id, message.pk)

	def test_open_sets_first_opened_at_only_on_the_first_open(self):
		message = EmailMessage.objects.create(
			message_id="883953f4-6105-42a2-a16a-77a8eac79483",
			recipient="researcher@example.com",
			tag="author_outreach",
			message_stream="broadcast",
			site=self.site,
			accepted=True,
		)

		first_payload = _open_payload(ReceivedAt="2026-08-01T12:00:00Z")
		handle_email_event(first_payload)
		message.refresh_from_db()
		first_opened_at = message.first_opened_at
		self.assertIsNotNone(first_opened_at)

		second_payload = _open_payload(ReceivedAt="2026-08-02T12:00:00Z")
		handle_email_event(second_payload)
		message.refresh_from_db()
		self.assertEqual(message.first_opened_at, first_opened_at)
		self.assertEqual(
			EmailEvent.objects.filter(record_type=EmailEvent.RECORD_TYPE_OPEN).count(),
			2,
			"a second open still gets its own EmailEvent row",
		)

	def test_bounce_sets_bounced_at_and_bounce_type_on_the_message(self):
		message = EmailMessage.objects.create(
			message_id="883953f4-6105-42a2-a16a-77a8eac79483",
			recipient="john@example.com",
			tag="weekly_summary",
			message_stream="broadcast",
			site=self.site,
			accepted=True,
		)

		handle_email_event(_bounce_payload())

		message.refresh_from_db()
		self.assertIsNotNone(message.bounced_at)
		self.assertEqual(message.bounce_type, "HardBounce")

	def test_spam_complaint_sets_complained_at_on_the_message(self):
		message = EmailMessage.objects.create(
			message_id="883953f4-6105-42a2-a16a-77a8eac79483",
			recipient="john@example.com",
			tag="weekly_summary",
			message_stream="broadcast",
			site=self.site,
			accepted=True,
		)

		handle_email_event(_spam_complaint_payload())

		message.refresh_from_db()
		self.assertIsNotNone(message.complained_at)

	def test_click_does_not_touch_any_email_message_aggregate_field(self):
		message = EmailMessage.objects.create(
			message_id="883953f4-6105-42a2-a16a-77a8eac79483",
			recipient="john@example.com",
			tag="weekly_summary",
			message_stream="broadcast",
			site=self.site,
			accepted=True,
		)

		handle_email_event(_click_payload())

		message.refresh_from_db()
		self.assertIsNone(message.delivered_at)
		self.assertIsNone(message.first_opened_at)
		self.assertIsNone(message.bounced_at)
		self.assertIsNone(message.complained_at)
