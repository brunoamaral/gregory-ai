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
    ArticleViewSet, ArticlesByAuthorList, ArticlesByCategory, ArticlesBySourceList,
    ArticlesByJournal, ArticlesBySubject, AuthorsViewSet, OpenAccessArticles, 
    RelevantList, UnsentList, TrialsBySourceList, SourceViewSet, TrialViewSet, 
    post_article, newsletterByWeek, lastXdays, CategoryViewSet, TrialsByCategory, MonthlyCountsView, LoginView, ProtectedEndpointView
)
from rss.views import (
    ArticlesByAuthorFeed, ArticlesByCategoryFeed, ArticlesBySubjectFeed, OpenAccessFeed,
    LatestArticlesFeed, LatestTrialsFeed, MachineLearningFeed, Twitter
)
from subscriptions.views import subscribe_view

# Initialize the router and register some endpoints
router = routers.DefaultRouter()
router.register(r'authors', AuthorsViewSet)
router.register(r'categories', CategoryViewSet)  # Register CategoryViewSet
router.register(r'sources', SourceViewSet)
router.register(r'trials', TrialViewSet)

# Define URL patterns
urlpatterns = [
    # Admin routes
    path('admin/', admin.site.urls),

    # API auth route
    path('api-auth/', include('rest_framework.urls')),

    # Article routes
    path('articles/relevant/', RelevantList.as_view()),
    path('articles/post/', post_article),
    path('articles/<int:pk>/', ArticleViewSet.as_view({'get': 'retrieve', 'put': 'update'}), name='article-detail'),

    # Feed routes
    path('feed/articles/author/<int:author_id>/', ArticlesByAuthorFeed(), name='articles_by_author_feed'),
    path('feed/articles/category/<str:category>/', ArticlesByCategoryFeed()),
    path('feed/articles/subject/<str:subject>/', ArticlesBySubjectFeed()),
    path('feed/articles/open/',OpenAccessFeed()),
    path('feed/latest/articles/', LatestArticlesFeed()),
    path('feed/latest/trials/', LatestTrialsFeed()),
    path('feed/machine-learning/', MachineLearningFeed()),
    path('feed/twitter/', Twitter()),

    # Subscriptions route
    path('subscriptions/new/', subscribe_view),

    # More articles routes
    re_path('^articles/author/(?P<author>.+)/$', ArticlesByAuthorList.as_view()),
    re_path('^articles/category/(?P<category_slug>[-\w]+)/$', ArticlesByCategory.as_view({'get':'list'})),
    re_path('^articles/source/(?P<source>.+)/$', ArticlesBySourceList.as_view()),
    re_path('^articles/subject/(?P<subject>.+)/$', ArticlesBySubject.as_view({'get':'list'})),
    re_path('^articles/journal/(?P<journal>.+)/$', ArticlesByJournal.as_view({'get':'list'})),
    re_path('^articles/open/$', OpenAccessArticles.as_view()),
    re_path('^articles/unsent/$', UnsentList.as_view()),

    # Relevant articles routes
    path('articles/relevant/week/<int:year>/<int:week>/', newsletterByWeek.as_view({'get':'list'})),
    path('articles/relevant/last/<int:days>/', lastXdays.as_view({'get':'list'})),

    # Category routes
    path('categories/', CategoryViewSet.as_view({'get':'list'})),
    path('categories/<str:category_slug>/monthly-counts/', MonthlyCountsView.as_view()),

    # Trial routes
    re_path('^trials/category/(?P<category_slug>[-\w]+)/$', TrialsByCategory.as_view({'get':'list'})),
    re_path('^trials/source/(?P<source>.+)/$', TrialsBySourceList.as_view()),

    # Token routes
    path('api/token/', LoginView.as_view(), name='token_obtain_pair'),
    path('api/token/get/', views.obtain_auth_token),

    # Protected endpoint route
    path('protected_endpoint/', ProtectedEndpointView.as_view(), name='protected_endpoint'),

    # Include router routes
    path('', include(router.urls)),

] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
