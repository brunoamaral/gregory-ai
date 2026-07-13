"""
Tests for the "DOI assigned" admin list filter (articles-missing-doi, phase 3).

Run:
	docker exec gregory python manage.py test gregory.tests.test_doi_assigned_filter
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase

from gregory.admin import ArticleAdmin, DoiAssignedFilter
from gregory.models import Articles


class DoiAssignedFilterTests(TestCase):
	def setUp(self):
		self.with_doi = Articles.objects.create(
			title="Has a DOI",
			link="https://example.com/with-doi",
			doi="10.1234/example",
			kind="science paper",
		)
		self.with_empty_doi = Articles.objects.create(
			title="Empty string DOI",
			link="https://example.com/empty-doi",
			doi="",
			kind="science paper",
		)
		self.without_doi = Articles.objects.create(
			title="No DOI",
			link="https://example.com/no-doi",
			doi=None,
			kind="science paper",
		)

	def test_lookups_are_yes_no(self):
		filter_instance = DoiAssignedFilter(
			request=None, params={}, model=Articles, model_admin=ArticleAdmin
		)
		self.assertEqual(
			filter_instance.lookups(None, None), (("yes", "Yes"), ("no", "No"))
		)

	def test_no_filters_to_null_or_empty_doi(self):
		# Django's SimpleListFilter takes params as lists and stores value[-1],
		# so a single-element list is the correct request shape here; assert
		# value() resolves to the scalar "no" to prove the branch is exercised
		# (rather than silently short-circuiting on an unexpected value).
		filter_instance = DoiAssignedFilter(
			request=None,
			params={"doi_assigned": ["no"]},
			model=Articles,
			model_admin=ArticleAdmin,
		)
		self.assertEqual(filter_instance.value(), "no")
		result = filter_instance.queryset(None, Articles.objects.all())
		self.assertIn(self.with_empty_doi, result)
		self.assertIn(self.without_doi, result)
		self.assertNotIn(self.with_doi, result)

	def test_yes_filters_to_articles_with_a_doi(self):
		filter_instance = DoiAssignedFilter(
			request=None,
			params={"doi_assigned": ["yes"]},
			model=Articles,
			model_admin=ArticleAdmin,
		)
		self.assertEqual(filter_instance.value(), "yes")
		result = filter_instance.queryset(None, Articles.objects.all())
		self.assertIn(self.with_doi, result)
		self.assertNotIn(self.with_empty_doi, result)
		self.assertNotIn(self.without_doi, result)

	def test_registered_on_article_admin(self):
		self.assertIn(DoiAssignedFilter, ArticleAdmin.list_filter)

	def test_no_value_returns_full_queryset(self):
		filter_instance = DoiAssignedFilter(
			request=None, params={}, model=Articles, model_admin=ArticleAdmin
		)
		result = filter_instance.queryset(None, Articles.objects.all())
		self.assertEqual(result.count(), Articles.objects.count())
