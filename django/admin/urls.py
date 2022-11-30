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
from api.views import ArticleViewSet,ArticlesByAuthorList,ArticlesByCategory,ArticlesBySourceList,ArticlesByJournal,ArticlesBySubject,AuthorsViewSet,OpenAccessArticles,RelevantList,UnsentList,TrialsBySourceList,SourceViewSet,TrialViewSet,post_article,newsletterByWeek,lastXdays
from rss.views import *
from subscriptions.views import subscribe_view


router = routers.DefaultRouter()
router.register(r'articles', ArticleViewSet, RelevantList)
router.register(r'authors',AuthorsViewSet)
router.register(r'sources', SourceViewSet)
router.register(r'trials', TrialViewSet)


urlpatterns = [
	path('articles/relevant/', RelevantList.as_view()),
	path('articles/post/',post_article),
	path('admin/', admin.site.urls),
	path('api-auth/', include('rest_framework.urls')),
	# path('articles/all/', AllArticleViewSet.as_view()),
	# path('articles/prediction/none/', ArticlesPredictionNone.as_view()),
	# path('feed/articles/prediction/none/', ToPredictFeed()),
	# path('trials/all/', AllTrialViewSet.as_view()),
	# path('articles/related/', RelatedArticles.as_view({'get': 'list'})),
	path('feed/articles/category/<str:category>/', ArticlesByCategoryFeed()),
	path('feed/articles/subject/<str:subject>/', ArticlesBySubjectFeed()),
	path('feed/articles/open/',OpenAccessFeed()),
	path('feed/latest/articles/', LatestArticlesFeed()),
	path('feed/latest/trials/', LatestTrialsFeed()),
	path('feed/machine-learning/', MachineLearningFeed()),
	path('feed/twitter/', Twitter()),
	path('subscriptions/new/', subscribe_view),
	re_path('^articles/author/(?P<author>.+)/$', ArticlesByAuthorList.as_view()),
	re_path('^articles/category/(?P<category>.+)/$', ArticlesByCategory.as_view({'get':'list'})),
	re_path('^articles/source/(?P<source>.+)/$', ArticlesBySourceList.as_view()),
	re_path('^articles/subject/(?P<subject>.+)/$', ArticlesBySubject.as_view({'get':'list'})),
	re_path('^articles/journal/(?P<journal>.+)/$', ArticlesByJournal.as_view({'get':'list'})),
	re_path('^articles/open/$', OpenAccessArticles.as_view()),
	re_path('^articles/unsent/$', UnsentList.as_view()),
	path('articles/relevant/week/<int:year>/<int:week>/', newsletterByWeek.as_view({'get':'list'})),
	path('articles/relevant/last/<int:days>/', lastXdays.as_view({'get':'list'})),
	re_path('^trials/source/(?P<source>.+)/$', TrialsBySourceList.as_view()),
	path('', include(router.urls)),

] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
