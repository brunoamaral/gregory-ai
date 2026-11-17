"""Looser author name matching: the key, save(), the shared matcher, the importers."""

import importlib

from django.apps import apps
from django.test import TestCase

from gregory.management.commands.feedreader_articles import Command as FeedreaderCommand
from gregory.models import Authors
from gregory.services.doi_import import resolve_author
from gregory.utils.author_names import name_key
from gregory.utils.authors import (
	AMBIGUOUS,
	CREATED,
	EXISTING,
	find_authors_by_name,
	get_or_create_author,
)


class NameKeyTests(TestCase):
	def test_accents_and_case(self):
		self.assertEqual(name_key("Belgin", "Koçer"), "belgin|kocer")
		self.assertEqual(name_key("Belgin", "Kocer"), "belgin|kocer")
		self.assertEqual(name_key("Belgin", "KOÇER"), "belgin|kocer")

	def test_dots_hyphens_apostrophes(self):
		self.assertEqual(name_key("J. Helen", "Cross"), name_key("J Helen", "Cross"))
		self.assertEqual(name_key("Jean-Luc", "Picard"), name_key("Jean Luc", "Picard"))
		self.assertEqual(name_key("Ann", "O’Brien"), name_key("Ann", "O'Brien"))
		self.assertEqual(name_key("Ann", "O'Brien"), "ann|o brien")

	def test_extra_spaces(self):
		self.assertEqual(name_key("  Jane   Q ", " Doe  "), "jane q|doe")

	def test_non_latin_letters_kept(self):
		self.assertEqual(name_key("Γιώργος", "Παπαδόπουλος"), "γιωργοσ|παπαδοπουλοσ")
		# Final sigma and plain sigma agree.
		self.assertEqual(name_key("Σοφία", "Παπας"), name_key("σοφία", "ΠΑΠΑΣ"))
		self.assertEqual(name_key("Иван", "Петров"), "иван|петров")
		self.assertEqual(name_key("太郎", "山田"), "太郎|山田")

	def test_empty_after_normalising(self):
		self.assertEqual(name_key("...", "Doe"), "")
		self.assertEqual(name_key("Jane", "123"), "")
		self.assertEqual(name_key("", ""), "")

	def test_initials_alone_stay_apart(self):
		self.assertNotEqual(name_key("J", "Cross"), name_key("Jane", "Cross"))


class SaveSetsKeyTests(TestCase):
	def test_save_sets_key(self):
		author = Authors.objects.create(given_name="Belgin", family_name="Koçer")
		author.refresh_from_db()
		self.assertEqual(author.name_key, "belgin|kocer")

	def test_name_change_updates_key(self):
		author = Authors.objects.create(given_name="A", family_name="B")
		author.given_name = "Anne"
		author.save(update_fields=["given_name"])
		author.refresh_from_db()
		self.assertEqual(author.name_key, "anne|b")


class FindAuthorsTests(TestCase):
	def test_exact_wins_over_key(self):
		exact = Authors.objects.create(given_name="Belgin", family_name="Kocer")
		Authors.objects.create(given_name="Belgin", family_name="Koçer")
		self.assertEqual(find_authors_by_name("Belgin", "Kocer"), [exact])

	def test_key_fallback(self):
		a = Authors.objects.create(given_name="Belgin", family_name="Koçer")
		self.assertEqual(find_authors_by_name("Belgin", "KOCER"), [a])

	def test_empty_key_never_matches(self):
		Authors.objects.create(given_name="...", family_name="Doe")
		Authors.objects.create(given_name="---", family_name="Doe")
		self.assertEqual(find_authors_by_name("???", "Doe"), [])

	def test_several_on_the_key_is_no_match(self):
		Authors.objects.create(given_name="Belgin", family_name="Koçer")
		Authors.objects.create(given_name="Belgin", family_name="Kocer")
		self.assertEqual(find_authors_by_name("Belgin", "KOÇER"), [])

	def test_initial_does_not_match_full_name(self):
		Authors.objects.create(given_name="Jane", family_name="Cross")
		self.assertEqual(find_authors_by_name("J", "Cross"), [])


class UtilsImporterTests(TestCase):
	def test_variant_found(self):
		a = Authors.objects.create(given_name="J Helen", family_name="Cross")
		author, status = get_or_create_author("J. Helen", "Cross")
		self.assertEqual((author, status), (a, EXISTING))
		self.assertEqual(Authors.objects.count(), 1)

	def test_ambiguous_exact(self):
		Authors.objects.create(given_name="Belgin", family_name="Kocer")
		Authors.objects.create(given_name="Belgin", family_name="Kocer")
		self.assertEqual(get_or_create_author("Belgin", "Kocer"), (None, AMBIGUOUS))

	def test_several_on_the_key_creates_rather_than_drops(self):
		Authors.objects.create(given_name="Belgin", family_name="Koçer")
		Authors.objects.create(given_name="Belgin", family_name="Kocer")
		author, status = get_or_create_author("Belgin", "KOÇER")
		self.assertEqual((author.family_name, status), ("KOÇER", CREATED))

	def test_created_when_nothing_matches(self):
		author, status = get_or_create_author("New", "Person")
		self.assertEqual(status, CREATED)
		self.assertEqual(author.name_key, "new|person")

	def test_orcid_path_unchanged(self):
		Authors.objects.create(given_name="Jane", family_name="Doe")
		author, status = get_or_create_author("Jane", "Doe", "0000-0002-1825-0097")
		self.assertEqual(status, CREATED)
		self.assertEqual(Authors.objects.count(), 2)
		self.assertEqual(Authors.objects.filter(ORCID__isnull=True).count(), 1)


class DoiImportTests(TestCase):
	def test_variant_found(self):
		a = Authors.objects.create(given_name="Belgin", family_name="Koçer")
		self.assertEqual(resolve_author({"given": "Belgin", "family": "Kocer"}), a)
		self.assertEqual(Authors.objects.count(), 1)

	def test_ambiguous_exact_skips(self):
		Authors.objects.create(given_name="Belgin", family_name="Kocer")
		Authors.objects.create(given_name="Belgin", family_name="Kocer")
		self.assertIsNone(resolve_author({"given": "Belgin", "family": "Kocer"}))
		self.assertEqual(Authors.objects.count(), 2)

	def test_several_on_the_key_creates_rather_than_drops(self):
		Authors.objects.create(given_name="Belgin", family_name="Koçer")
		Authors.objects.create(given_name="Belgin", family_name="Kocer")
		author = resolve_author({"given": "Belgin", "family": "KOÇER"})
		self.assertEqual(author.family_name, "KOÇER")
		self.assertEqual(Authors.objects.count(), 3)

	def test_creates_when_none(self):
		author = resolve_author({"given": "New", "family": "Person"})
		self.assertEqual(author.name_key, "new|person")


class FeedreaderTests(TestCase):
	def setUp(self):
		self.cmd = FeedreaderCommand()

	def test_variant_found(self):
		a = Authors.objects.create(given_name="Belgin", family_name="Koçer")
		self.assertEqual(self.cmd.get_or_create_author("belgin", "Kocer", None), a)
		self.assertEqual(Authors.objects.count(), 1)

	def test_several_exact_prefers_orcid_else_first(self):
		first = Authors.objects.create(given_name="Belgin", family_name="Kocer")
		with_orcid = Authors.objects.create(
			given_name="Belgin", family_name="Kocer", ORCID="0000-0002-1825-0097"
		)
		self.assertEqual(self.cmd.get_or_create_author("Belgin", "Kocer", None), with_orcid)
		with_orcid.delete()
		Authors.objects.create(given_name="Belgin", family_name="Kocer")
		self.assertEqual(self.cmd.get_or_create_author("Belgin", "Kocer", None), first)

	def test_several_on_the_key_creates_rather_than_picks(self):
		Authors.objects.create(given_name="Belgin", family_name="Koçer")
		Authors.objects.create(given_name="Belgin", family_name="Kocer")
		author = self.cmd.get_or_create_author("Belgin", "KOÇER", None)
		self.assertEqual(author.family_name, "KOÇER")
		self.assertEqual(Authors.objects.count(), 3)

	def test_orcid_path_unchanged(self):
		Authors.objects.create(given_name="Jane", family_name="Doe")
		author = self.cmd.get_or_create_author("Jane", "Doe", "0000-0002-1825-0097")
		self.assertEqual(author.ORCID, "0000-0002-1825-0097")
		self.assertEqual(Authors.objects.count(), 2)


class BackfillTests(TestCase):
	def test_backfill_function(self):
		migration = importlib.import_module("gregory.migrations.0138_authors_name_key")
		a = Authors.objects.create(given_name="Belgin", family_name="Koçer")
		b = Authors.objects.create(given_name="Jean-Luc", family_name="Picard")
		c = Authors.objects.create(given_name="...", family_name="Doe")
		Authors.objects.update(name_key="stale")
		migration.backfill_name_key(apps, None)
		self.assertEqual(Authors.objects.get(pk=a.pk).name_key, "belgin|kocer")
		self.assertEqual(Authors.objects.get(pk=b.pk).name_key, "jean luc|picard")
		self.assertEqual(Authors.objects.get(pk=c.pk).name_key, "")
