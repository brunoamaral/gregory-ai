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
from api import views

router = routers.DefaultRouter()
router.register(r'articles', views.ArticleViewSet, views.RelevantList)
router.register(r'trials', views.TrialViewSet)
router.register(r'sources', views.SourceViewSet)


urlpatterns = [
	path('admin/', admin.site.urls),
	path('articles/all/', views.AllArticleViewSet.as_view()),
	path('trials/all/', views.AllTrialViewSet.as_view()),
	re_path('^articles/relevant/$', views.RelevantList.as_view()),
	re_path('^articles/source/(?P<source>.+)/$', views.ArticlesBySourceList.as_view()),
	re_path('^trials/source/(?P<source>.+)/$', views.TrialsBySourceList.as_view()),
	# /articles/id/:id/relevant/0
	# re_path('^articles/id/(?P<article_id>.+)/relevant/<relevancy>$', views.TrialsBySourceList.as_view()),
	re_path('^articles/unsent/$', views.UnsentList.as_view()),


	path('', include(router.urls)),

	# path('api-articles/', include('rest_framework.urls', namespace='rest_framework'))
]
