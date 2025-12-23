"""admin URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
	https://docs.djangoproject.com/en/4.0/topics/http/urls/
Examples:
Function views
	1. Add an import:  from my_app import views
	2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
	1. Add an import:  from other_app.views import Home
	2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
	1. Import the include() function: from django.urls import include, path
	2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from rest_framework import routers
from rest_framework.authtoken import views

from api.views import (
	ArticleViewSet, AuthorsViewSet, SourceViewSet, TrialViewSet, 
	post_article, CategoryViewSet, MonthlyCountsView, LoginView, ProtectedEndpointView,
	ArticlesByTeam, ArticlesBySubject, TeamsViewSet, SubjectsViewSet, SubjectsByTeam,
    ArticlesByCategoryAndTeam, ArticlesBySource, ArticleSearchView, TrialSearchView, AuthorSearchView, CategoriesByTeamAndSubject
)
from rss.views import	ArticlesByAuthorFeed, TrialsBySubjectFeed
from subscriptions.views import subscribe_view
from organizations.backends import invitation_backend

# Email template views (direct import to avoid module issues)
from templates.emails.views import (
	email_preview_dashboard,
	email_template_preview, 
	email_template_json_context,
)

# Initialize the router and register some endpoints
router = routers.DefaultRouter()
router.register(r'articles', ArticleViewSet)
router.register(r'authors', AuthorsViewSet, basename='authors')
router.register(r'categories', CategoryViewSet, basename='categories')
router.register(r'sources', SourceViewSet)
router.register(r'trials', TrialViewSet)
router.register(r'teams', TeamsViewSet)
router.register(r'subjects', SubjectsViewSet)

# Define URL patterns
urlpatterns = [
	# Admin routes
	path('admin/', admin.site.urls),

	# API auth route
	path('api-auth/', include('rest_framework.urls')),
	path('api/token/', LoginView.as_view(), name='token_obtain_pair'),
	path('api/token/get/', views.obtain_auth_token),

	# API routes
	path('articles/post/', post_article),

	# Feed routes (supports ORCID or numeric author_id)
	path('feed/author/<str:orcid>/', ArticlesByAuthorFeed(), name='articles_by_author_feed'),
	path('feed/trials/subject/<str:subject_slug>/', TrialsBySubjectFeed(), name='trials_by_subject_feed'),

	# Organization routes
	re_path(r'^accounts/', include('organizations.urls')),
	re_path(r'^invitations/', include(invitation_backend().get_urls())),

	# Protected endpoint route
	path('protected_endpoint/', ProtectedEndpointView.as_view(), name='protected_endpoint'),

	# Subscriptions route
	path('subscriptions/new/', subscribe_view),

	# Email template preview and testing routes
	path('emails/', email_preview_dashboard, name='email_preview_dashboard'),
	path('emails/preview/<str:template_name>/', email_template_preview, name='email_template_preview'),
	path('emails/context/<str:template_name>/', email_template_json_context, name='email_template_json_context'),


	# Team API
	## List Teams
	path('teams/', TeamsViewSet.as_view({'get':'list'})),
	## List articles
	path('teams/<int:team_id>/articles/', ArticlesByTeam.as_view({'get': 'list'}), name='articles-by-team'),
	## List article per ID
	## List articles per subject
	path('teams/<int:team_id>/articles/subject/<int:subject_id>/', ArticlesBySubject.as_view({'get': 'list'}), name='articles-by-subject'),
	## List articles per category: OK
	path('teams/<int:team_id>/articles/category/<str:category_slug>/', ArticlesByCategoryAndTeam.as_view({'get': 'list'}), name='articles-by-category-and-team'),
	## List articles per source
	path('teams/<int:team_id>/articles/source/<int:source_id>/', ArticlesBySource.as_view({'get': 'list'}), name='articles-by-category-and-team'),
	## List articles per journal
	## List categories by team and subject
	path('teams/<int:team_id>/subjects/<int:subject_id>/categories/', CategoriesByTeamAndSubject.as_view({'get': 'list'}), name='categories-by-team-and-subject'),
	## List subjects
	path('teams/<int:team_id>/subjects/', SubjectsByTeam.as_view({'get': 'list'}), name='subjects-by-team'),
	
	# Monthly counts for categories
	path('teams/<int:team_id>/categories/<str:category_slug>/monthly_counts/', MonthlyCountsView.as_view(), name='monthly-counts'),
	
	# Search endpoints
	path('articles/search/', ArticleSearchView.as_view(), name='article-search'),
	path('trials/search/', TrialSearchView.as_view(), name='trial-search'),
	path('authors/search/', AuthorSearchView.as_view(), name='author-search'),

	# Gregory app routes
	path('', include('gregory.urls')),

	# Include router routes
	path('', include(router.urls)),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)