"""
Model-level tests for AuthorContactOptOut — see AUTHOR-OUTREACH-PLAN.md
"PR 2 — Author do-not-contact" and AUTHOR-OUTREACH-SPEC.md "Legal basis and
consent" / "Bounce and complaint handling". Wiring into handle_email_event,
handle_subscription_change, and the opt-out view is covered separately in
test_author_contact_optout_wiring.py and test_author_optout_view.py.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase

from gregory.models import Authors
from subscriptions.models import AuthorContactOptOut


class AuthorContactOptOutModelTest(TestCase):
	def test_create_with_defaults(self):
		row = AuthorContactOptOut.objects.create(
			email="Researcher@Example.com",
			reason=AuthorContactOptOut.REASON_OPT_OUT,
		)
		row.refresh_from_db()
		self.assertEqual(row.reason, AuthorContactOptOut.REASON_OPT_OUT)
		self.assertEqual(row.note, "")
		self.assertIsNotNone(row.created_at)
		self.assertIsNone(row.author)

	def test_save_lowercases_email(self):
		row = AuthorContactOptOut.objects.create(
			email="Mixed.Case@Example.COM",
			reason=AuthorContactOptOut.REASON_ADMIN,
		)
		row.refresh_from_db()
		self.assertEqual(row.email, "mixed.case@example.com")

	def test_unique_constraint_is_case_insensitive(self):
		AuthorContactOptOut.objects.create(
			email="dup@example.com", reason=AuthorContactOptOut.REASON_HARD_BOUNCE
		)
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				AuthorContactOptOut.objects.create(
					email="DUP@EXAMPLE.COM",
					reason=AuthorContactOptOut.REASON_SPAM_COMPLAINT,
				)

	def test_author_is_nullable(self):
		# A bounce/complaint carries only an address; resolving it to an
		# Authors row is a best-effort lookup, never required.
		row = AuthorContactOptOut.objects.create(
			email="unresolved@example.com",
			reason=AuthorContactOptOut.REASON_HARD_BOUNCE,
		)
		self.assertIsNone(row.author)

	def test_author_set_deletion_sets_null_not_cascade(self):
		author = Authors.objects.create(
			given_name="Ada",
			family_name="Researcher",
			ORCID="0000-0000-0000-1234",
		)
		row = AuthorContactOptOut.objects.create(
			author=author, email="ada@example.com", reason=AuthorContactOptOut.REASON_OPT_OUT
		)
		author.delete()
		row.refresh_from_db()
		self.assertIsNone(row.author)

	def test_str_includes_email_and_reason_display(self):
		row = AuthorContactOptOut.objects.create(
			email="str@example.com", reason=AuthorContactOptOut.REASON_ADMIN
		)
		self.assertIn("str@example.com", str(row))
		self.assertIn("Added manually", str(row))

	def test_all_four_reasons_are_valid_choices(self):
		reasons = {
			AuthorContactOptOut.REASON_OPT_OUT,
			AuthorContactOptOut.REASON_SPAM_COMPLAINT,
			AuthorContactOptOut.REASON_HARD_BOUNCE,
			AuthorContactOptOut.REASON_ADMIN,
		}
		choice_keys = {choice[0] for choice in AuthorContactOptOut.REASON_CHOICES}
		self.assertEqual(reasons, choice_keys)
		for i, reason in enumerate(reasons):
			AuthorContactOptOut.objects.create(
				email=f"reason{i}@example.com", reason=reason
			)
		self.assertEqual(AuthorContactOptOut.objects.count(), len(reasons))
