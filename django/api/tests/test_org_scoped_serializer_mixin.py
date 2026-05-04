"""
Tests for api.serializers.mixins.OrgScopedSerializerMixin.

Run with:
    docker exec gregory python manage.py test api.tests.test_org_scoped_serializer_mixin
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import AnonymousUser
from rest_framework import serializers
from organizations.models import Organization
from gregory.models import (
	Articles, Team, Subject, TeamCategory, MLPredictions, OrganizationApiSettings,
)
from api.serializers.mixins import OrgScopedSerializerMixin


def _make_org(name, slug, public=False):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=public)
	return org


def _make_team(org, name):
	return Team.objects.create(organization=org, name=name, slug=name.lower().replace(' ', '-'))


def _make_subject(team, name):
	from django.utils.text import slugify
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


def _make_article(title='Test Article', link='https://example.com/1'):
	return Articles.objects.create(title=title, link=link)


def _make_team_category(team, name):
	from django.utils.text import slugify
	return TeamCategory.objects.create(team=team, category_name=name, category_slug=slugify(name))


def _request_with_visible(visible_ids):
	"""Build a fake request with a pre-set visible_org_ids attribute."""
	factory = RequestFactory()
	req = factory.get('/')
	req.user = AnonymousUser()
	req.visible_org_ids = visible_ids
	return req


# ---------------------------------------------------------------------------
# Minimal serializer that uses the mixin (mirrors ArticleSerializer shape)
# ---------------------------------------------------------------------------

class _TeamSerializer(serializers.ModelSerializer):
	class Meta:
		model = Team
		fields = ['id', 'name']


class _SubjectSerializer(serializers.ModelSerializer):
	class Meta:
		model = Subject
		fields = ['id', 'subject_name']


class _TeamCategorySerializer(serializers.ModelSerializer):
	class Meta:
		model = TeamCategory
		fields = ['id', 'category_name']


class _MLPredictionsSerializer(serializers.ModelSerializer):
	class Meta:
		model = MLPredictions
		fields = ['id', 'algorithm', 'probability_score']


class _TestArticleSerializer(OrgScopedSerializerMixin, serializers.ModelSerializer):
	teams = _TeamSerializer(many=True, read_only=True)
	subjects = _SubjectSerializer(many=True, read_only=True)
	team_categories = _TeamCategorySerializer(many=True, read_only=True)
	ml_predictions = _MLPredictionsSerializer(
		many=True, read_only=True, source='ml_predictions_detail'
	)

	class Meta:
		model = Articles
		fields = ['article_id', 'title', 'teams', 'subjects', 'team_categories', 'ml_predictions']


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class OrgScopedSerializerMixinTeamsTest(TestCase):
	def setUp(self):
		self.org_a = _make_org('Org A', 'org-a-m', public=False)
		self.org_b = _make_org('Org B', 'org-b-m', public=False)
		self.team_a = _make_team(self.org_a, 'Team A')
		self.team_b = _make_team(self.org_b, 'Team B')

		self.article = _make_article()
		self.article.teams.add(self.team_a, self.team_b)

	def test_teams_stripped_for_hidden_org(self):
		"""Only team_a should appear when org_b is not visible."""
		req = _request_with_visible({self.org_a.id})
		data = _TestArticleSerializer(self.article, context={'request': req}).data
		team_ids = [t['id'] for t in data['teams']]
		self.assertIn(self.team_a.id, team_ids)
		self.assertNotIn(self.team_b.id, team_ids)

	def test_both_teams_visible_when_both_orgs_visible(self):
		req = _request_with_visible({self.org_a.id, self.org_b.id})
		data = _TestArticleSerializer(self.article, context={'request': req}).data
		team_ids = [t['id'] for t in data['teams']]
		self.assertIn(self.team_a.id, team_ids)
		self.assertIn(self.team_b.id, team_ids)

	def test_no_middleware_returns_all_teams(self):
		"""When request has no visible_org_ids, the mixin is a no-op."""
		factory = RequestFactory()
		req = factory.get('/')
		req.user = AnonymousUser()
		# No visible_org_ids attribute set
		data = _TestArticleSerializer(self.article, context={'request': req}).data
		team_ids = [t['id'] for t in data['teams']]
		self.assertIn(self.team_a.id, team_ids)
		self.assertIn(self.team_b.id, team_ids)

	def test_no_request_in_context_is_no_op(self):
		"""Serializer without request context must not crash."""
		data = _TestArticleSerializer(self.article).data
		self.assertIn('teams', data)


class OrgScopedSerializerMixinSubjectsTest(TestCase):
	def setUp(self):
		self.org_a = _make_org('Org A', 'org-a-s', public=False)
		self.org_b = _make_org('Org B', 'org-b-s', public=False)
		self.team_a = _make_team(self.org_a, 'Team A S')
		self.team_b = _make_team(self.org_b, 'Team B S')
		self.subject_a = _make_subject(self.team_a, 'Subject A')
		self.subject_b = _make_subject(self.team_b, 'Subject B')

		self.article = _make_article(title='Subject Article', link='https://example.com/2')
		self.article.subjects.add(self.subject_a, self.subject_b)

	def test_hidden_subjects_stripped(self):
		req = _request_with_visible({self.org_a.id})
		data = _TestArticleSerializer(self.article, context={'request': req}).data
		subject_ids = [s['id'] for s in data['subjects']]
		self.assertIn(self.subject_a.id, subject_ids)
		self.assertNotIn(self.subject_b.id, subject_ids)


class OrgScopedSerializerMixinMLPredictionsTest(TestCase):
	def setUp(self):
		self.org_a = _make_org('Org A', 'org-a-ml', public=False)
		self.org_b = _make_org('Org B', 'org-b-ml', public=False)
		self.team_a = _make_team(self.org_a, 'Team A ML')
		self.team_b = _make_team(self.org_b, 'Team B ML')
		self.subject_a = _make_subject(self.team_a, 'Subj ML A')
		self.subject_b = _make_subject(self.team_b, 'Subj ML B')

		self.article = _make_article(title='ML Article', link='https://example.com/3')

		self.pred_a = MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm='lgbm_tfidf',
			probability_score=0.9,
			predicted_relevant=True,
			model_version='v1',
		)
		self.pred_b = MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_b,
			algorithm='lgbm_tfidf',
			probability_score=0.8,
			predicted_relevant=True,
			model_version='v1',
		)

	def test_hidden_ml_predictions_stripped(self):
		req = _request_with_visible({self.org_a.id})
		data = _TestArticleSerializer(self.article, context={'request': req}).data
		pred_ids = [p['id'] for p in data['ml_predictions']]
		self.assertIn(self.pred_a.id, pred_ids)
		self.assertNotIn(self.pred_b.id, pred_ids)
