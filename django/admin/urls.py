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
from django.urls import include, path, re_path
from rest_framework import routers
from api.views import *
from rss.views import *

router = routers.DefaultRouter()
router.register(r'articles', ArticleViewSet, RelevantList)
router.register(r'trials', TrialViewSet)
router.register(r'sources', SourceViewSet)


urlpatterns = [
	path('admin/', admin.site.urls),
	path('api-auth/', include('rest_framework.urls')),
	path('articles/all/', AllArticleViewSet.as_view()),
	path('trials/all/', AllTrialViewSet.as_view()),
	re_path('^articles/relevant/$', RelevantList.as_view()),
	path('articles/prediction/none/', ArticlesPredictionNone.as_view()),
	re_path('^articles/source/(?P<source>.+)/$', ArticlesBySourceList.as_view()),
	re_path('^trials/source/(?P<source>.+)/$', TrialsBySourceList.as_view()),
	re_path('^articles/unsent/$', UnsentList.as_view()),
	path('articles/related/', RelatedArticles.as_view({'get': 'list'})),
	path('articles/count/', ArticlesCount.as_view({'get': 'list'})),
	path('', include(router.urls)),
	path('feed/latest/articles/', LatestArticlesFeed()),
	path('feed/latest/trials/', LatestTrialsFeed()),
	path('feed/machine-learning/', MachineLearningFeed()),
	path('feed/articles/prediction/none/', ToPredictFeed()),

]
