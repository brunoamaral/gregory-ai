from unittest.mock import patch, MagicMock

from django.contrib.admin import site as admin_site
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.contrib.sites.models import Site
from django.db import IntegrityError
from django.test import TestCase, Client
from django.urls import reverse

from organizations.models import Organization
from gregory.models import Team
from subscriptions.admin import AnnouncementAdmin
from subscriptions.models import (
	Announcement,
	AnnouncementRecipient,
	Lists,
	Subscribers,
	ListSubscription,
)


class AnnouncementStrTest(TestCase):
	def test_str_draft(self):
		ann = Announcement(subject='Hello world', status='draft')
		self.assertEqual(str(ann), 'Hello world (Draft)')

	def test_str_sent(self):
		ann = Announcement(subject='Hello world', status='sent')
		self.assertEqual(str(ann), 'Hello world (Sent)')

	def test_status_choices(self):
		values = [v for v, _ in Announcement.STATUS_CHOICES]
		self.assertIn('draft', values)
		self.assertIn('sending', values)
		self.assertIn('sent', values)
		self.assertIn('failed', values)


class AnnouncementRecipientUniqueTogetherTest(TestCase):
	def setUp(self):
		org = Organization.objects.create(name='Test Org')
		team = Team.objects.create(organization=org, name='Team A', slug='team-a')
		self.lst = Lists.objects.create(list_name='Weekly', team=team)
		self.ann = Announcement.objects.create(subject='Notice', body='<p>Hi</p>')
		self.sub = Subscribers.objects.create(
			first_name='Alice', last_name='Smith', email='alice@example.com'
		)

	def test_duplicate_raises_integrity_error(self):
		AnnouncementRecipient.objects.create(
			announcement=self.ann,
			subscriber=self.sub,
			list=self.lst,
		)
		with self.assertRaises(IntegrityError):
			AnnouncementRecipient.objects.create(
				announcement=self.ann,
				subscriber=self.sub,
				list=self.lst,
			)

	def test_update_or_create_does_not_raise(self):
		"""Retrying via update_or_create must not raise IntegrityError."""
		AnnouncementRecipient.objects.create(
			announcement=self.ann,
			subscriber=self.sub,
			list=self.lst,
			success=False,
			error_message='timeout',
		)
		obj, created = AnnouncementRecipient.objects.update_or_create(
			announcement=self.ann,
			subscriber=self.sub,
			defaults={'list': self.lst, 'success': True, 'error_message': ''},
		)
		self.assertFalse(created)
		self.assertTrue(obj.success)


class RenderAnnouncementEmailContextTest(TestCase):
	def setUp(self):
		org = Organization.objects.create(name='Ctx Org')
		self.team = Team.objects.create(organization=org, name='Ctx Team', slug='ctx-team')
		self.lst = Lists.objects.create(list_name='Digest', team=self.team)
		self.ann = Announcement.objects.create(
			subject='Test Email',
			body='<p>Hello</p>',
			header_title='My Title',
			header_tagline='My Tagline',
		)
		self.site = Site.objects.get_or_create(id=1, defaults={'domain': 'example.com', 'name': 'Example'})[0]
		self.site.domain = 'example.com'
		self.site.save()
		self.admin = AnnouncementAdmin(Announcement, admin_site)

	def _render_and_capture_context(self, **kwargs):
		"""Call _render_announcement_email and capture the context passed to render_to_string."""
		captured = {}

		def fake_render(template_name, context):
			captured.update(context)
			return '<html></html>'

		with patch('subscriptions.admin.render_to_string', side_effect=fake_render):
			self.admin._render_announcement_email(self.ann, **kwargs)

		return captured

	def test_current_date_always_present(self):
		ctx = self._render_and_capture_context()
		self.assertIn('current_date', ctx)
		self.assertIsNotNone(ctx['current_date'])

	def test_unsubscribe_base_url_is_site_root(self):
		sub = Subscribers.objects.create(
			first_name='Bob', last_name='Jones', email='bob@example.com'
		)
		ctx = self._render_and_capture_context(subscriber=sub, site=self.site, list_id=self.lst.list_id)
		self.assertIn('unsubscribe_base_url', ctx)
		url = ctx['unsubscribe_base_url']
		# Must be the site root — not a token-specific path
		self.assertEqual(url, 'https://example.com')
		self.assertNotIn('unsubscribe', url)
		self.assertNotIn('token', url)

	def test_list_id_passed_into_context(self):
		ctx = self._render_and_capture_context(list_id=self.lst.list_id)
		self.assertIn('list_id', ctx)
		self.assertEqual(ctx['list_id'], self.lst.list_id)

	def test_list_id_absent_when_not_provided(self):
		ctx = self._render_and_capture_context()
		self.assertNotIn('list_id', ctx)


class SendViewRedirectTest(TestCase):
	def setUp(self):
		self.superuser = User.objects.create_superuser(
			username='admin', password='password', email='admin@example.com'
		)
		org = Organization.objects.create(name='Send Org')
		self.team = Team.objects.create(organization=org, name='Send Team', slug='send-team')
		self.lst = Lists.objects.create(list_name='News', team=self.team)
		self.client = Client()
		self.client.force_login(self.superuser)

	def _make_announcement(self, status):
		ann = Announcement.objects.create(subject='Already sent', body='<p>content</p>', status=status)
		ann.lists.add(self.lst)
		return ann

	def _send_url(self, pk):
		return reverse('admin:subscriptions_announcement_send', args=[pk])

	def test_redirects_with_warning_when_already_sent(self):
		ann = self._make_announcement('sent')
		response = self.client.post(self._send_url(ann.pk))
		self.assertRedirects(
			response,
			reverse('admin:subscriptions_announcement_change', args=[ann.pk]),
			fetch_redirect_response=False,
		)
		msgs = list(get_messages(response.wsgi_request))
		self.assertTrue(any('already been sent' in str(m) for m in msgs))

	def test_redirects_with_warning_when_sending(self):
		ann = self._make_announcement('sending')
		response = self.client.post(self._send_url(ann.pk))
		self.assertRedirects(
			response,
			reverse('admin:subscriptions_announcement_change', args=[ann.pk]),
			fetch_redirect_response=False,
		)
		msgs = list(get_messages(response.wsgi_request))
		self.assertTrue(any('already been sent' in str(m) for m in msgs))


class SendViewSubjectNormalisationTest(TestCase):
	"""send_view must strip any leading [TEST] prefix from the subject on live sends."""

	def setUp(self):
		self.superuser = User.objects.create_superuser(
			username='admin2', password='password', email='admin2@example.com'
		)
		org = Organization.objects.create(name='Norm Org')
		self.team = Team.objects.create(organization=org, name='Norm Team', slug='norm-team')
		self.lst = Lists.objects.create(list_name='Norm List', team=self.team)
		self.sub = Subscribers.objects.create(
			first_name='Carol', last_name='Danvers', email='carol@example.com'
		)
		ListSubscription.objects.create(subscriber=self.sub, list=self.lst, is_active=True)
		self.sub.active = True
		self.sub.save()
		self.client = Client()
		self.client.force_login(self.superuser)

	def _send_url(self, pk):
		return reverse('admin:subscriptions_announcement_send', args=[pk])

	def _make_announcement(self, subject):
		ann = Announcement.objects.create(subject=subject, body='<p>Body</p>', status='draft')
		ann.lists.add(self.lst)
		return ann

	def _post_send(self, ann):
		mock_response = MagicMock()
		mock_response.status_code = 200
		with patch('subscriptions.management.commands.utils.send_email.send_email', return_value=mock_response) as mock_send:
			self.client.post(self._send_url(ann.pk))
		return mock_send

	def test_test_prefix_stripped_on_live_send(self):
		ann = self._make_announcement('[TEST] My Announcement')
		mock_send = self._post_send(ann)
		mock_send.assert_called_once()
		_, kwargs = mock_send.call_args
		self.assertEqual(kwargs['subject'], 'My Announcement')

	def test_clean_subject_unchanged_on_live_send(self):
		ann = self._make_announcement('My Announcement')
		mock_send = self._post_send(ann)
		mock_send.assert_called_once()
		_, kwargs = mock_send.call_args
		self.assertEqual(kwargs['subject'], 'My Announcement')


class RenderAnnouncementTaglineAndPreheaderTest(TestCase):
	"""Tests for show_header_tagline and preheader_text rendering."""

	def setUp(self):
		org = Organization.objects.create(name='Tag Org')
		team = Team.objects.create(organization=org, name='Tag Team', slug='tag-team')
		self.lst = Lists.objects.create(list_name='Tag List', team=team)
		self.admin = AnnouncementAdmin(Announcement, admin_site)

	def _render(self, **ann_kwargs):
		ann = Announcement(subject='Test', body='<p>Body</p>', **ann_kwargs)
		# Bypass DB so we can test rendering without saving
		with patch('subscriptions.admin.render_to_string', wraps=lambda tpl, ctx: __import__('django.template.loader', fromlist=['render_to_string']).render_to_string(tpl, ctx)):
			pass
		return self.admin._render_announcement_email(ann)

	def test_tagline_present_by_default(self):
		html = self._render()
		# Default tagline text should appear
		self.assertIn('Scientific Research Intelligence', html)

	def test_show_header_tagline_false_hides_tagline(self):
		# Supply a custom preheader_text so the fallback "Scientific Research Intelligence"
		# doesn't appear in the preheader and pollute this assertion.
		html = self._render(show_header_tagline=False, preheader_text='Custom preview text')
		self.assertNotIn('Scientific Research Intelligence', html)

	def test_custom_tagline_shown_when_show_tagline_true(self):
		html = self._render(header_tagline='My Custom Tagline', show_header_tagline=True)
		self.assertIn('My Custom Tagline', html)

	def test_custom_tagline_hidden_when_show_tagline_false(self):
		html = self._render(header_tagline='My Custom Tagline', show_header_tagline=False)
		self.assertNotIn('My Custom Tagline', html)

	def test_preheader_text_used_when_set(self):
		html = self._render(preheader_text='Read the latest news')
		self.assertIn('Read the latest news', html)

	def test_preheader_fallback_when_empty(self):
		html = self._render(preheader_text='')
		self.assertIn('Latest updates powered by Gregory AI Scientific Research Intelligence', html)

	def test_preheader_fallback_not_shown_when_custom_set(self):
		html = self._render(preheader_text='Custom preview')
		self.assertNotIn('Latest updates powered by Gregory AI Scientific Research Intelligence', html)
