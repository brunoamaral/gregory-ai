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
from django.views.decorators.cache import cache_page
from rest_framework import routers
from drf_spectacular.views import (
	SpectacularAPIView,
	SpectacularSwaggerView,
	SpectacularRedocView,
)

from api.views import (
	ArticleViewSet,
	AuthorsViewSet,
	SourceViewSet,
	TrialViewSet,
	SponsorViewSet,
	PatentViewSet,
	MedicineViewSet,
	MedicineDrugViewSet,
	MedicineEventViewSet,
	post_article,
	edit_article,
	edit_trial,
	CategoryViewSet,
	LoginView,
	ProtectedEndpointView,
	TeamsViewSet,
	SubjectsViewSet,
	ArticleSearchView,
	TrialSearchView,
	AuthorSearchView,
	CategoriesByTeamAndSubject,
	StatsView,
	PublicSitesView,
	McpTenantsView,
)
from api.editor_views import (
	EDITOR_READ_ROUTES,
	EditorArticleCategoryView,
	EditorArticleEditorialView,
	EditorArticleHistoryView,
	EditorArticleRelevanceView,
	EditorArticleResolveView,
	EditorArticleTrialDetailView,
	EditorArticleTrialsView,
	EditorCategoryMedicineView,
	EditorPatentCategoryView,
	EditorPatentEditorialView,
	EditorPatentHistoryView,
	EditorPatentResolveView,
	EditorPatentTrialDetailView,
	EditorSourcePreviewView,
	EditorSourcesView,
	EditorStatsView,
	EditorTenantsView,
)
from rss.views import (
	FEED_CACHE_SECONDS,
	SiteArticlesByAuthorFeed,
	SiteArticlesByCategoryFeed,
	SiteArticlesBySubjectFeed,
	SiteRecruitingTrialsBySubjectFeed,
	SiteTrialsBySubjectFeed,
	redirect_articles_by_author_feed,
	redirect_trials_by_subject_feed,
)
from rss.medicine_feeds import (
	SiteMedicineEventsByCategoryFeed,
	SiteMedicineEventsBySubjectFeed,
	SiteMedicineEventsFeed,
)
from rss.patent_feeds import (
	SitePatentsByCategoryFeed,
	SitePatentsBySubjectFeed,
	SitePatentsSearchFeed,
)
from rss.search_feeds import SiteArticlesSearchFeed, SiteTrialsSearchFeed, robots_txt
from rss.sitemaps import sitemap_index, sitemap_section
from subscriptions.views import (
	subscribe_view,
	unsubscribe_list,
	unsubscribe_site,
	unsubscribe_all,
	author_optout,
	ckeditor_upload,
	postmark_webhook,
)
from organizations.backends import invitation_backend

from admin.health import healthz
from gregory.autocomplete import GregoryAutocompleteJsonView

# Email template views (direct import to avoid module issues)
from templates.emails.views import (
	email_preview_dashboard,
	email_template_preview,
	email_template_json_context,
	email_preview_lists,
	email_preview_subscribers,
)

# Initialize the router and register some endpoints
router = routers.DefaultRouter()
router.register(r"articles", ArticleViewSet)
router.register(r"authors", AuthorsViewSet, basename="authors")
router.register(r"categories", CategoryViewSet, basename="categories")
router.register(r"sources", SourceViewSet)
router.register(r"trials", TrialViewSet)
router.register(r"subjects", SubjectsViewSet)
router.register(r"sponsors", SponsorViewSet, basename="sponsors")
router.register(r"patents", PatentViewSet, basename="patents")
# `medicines/drugs` and `medicines/events` before `medicines`: the router tries routes in
# registration order, so the other way round `events` would be read as a medicine id (the
# medicine detail route also takes digits only, as a second guard). The drugs detail route
# takes any key (spaces, `+`, dots) but only under `medicines/drugs/`, so it swallows
# neither of the others.
router.register(r"medicines/drugs", MedicineDrugViewSet, basename="medicine-drugs")
router.register(r"medicines/events", MedicineEventViewSet, basename="medicine-events")
router.register(r"medicines", MedicineViewSet, basename="medicines")

# Teams router (excluded from API root listing)
teams_router = routers.SimpleRouter()
teams_router.register(r"teams", TeamsViewSet)

# Editor routes: the read viewsets again, behind editor authentication.
editor_router = routers.SimpleRouter()
for _prefix, _viewset, _basename in EDITOR_READ_ROUTES:
	editor_router.register(_prefix, _viewset, basename=_basename)

# Define URL patterns
urlpatterns = (
	[
		# Admin routes. The autocomplete endpoint comes first so trials get a readable
		# label (see gregory/autocomplete.py); it is the admin's own view otherwise.
		path(
			"admin/autocomplete/",
			admin.site.admin_view(GregoryAutocompleteJsonView.as_view(admin_site=admin.site)),
		),
		path("admin/", admin.site.urls),
		# API auth route
		path("api-auth/", include("rest_framework.urls")),
		path("api/token/", LoginView.as_view(), name="token_obtain_pair"),
		# OpenAPI schema (see docs/03-api-and-rss-feeds.md)
		path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
		path(
			"api/schema/swagger-ui/",
			SpectacularSwaggerView.as_view(url_name="schema"),
			name="swagger-ui",
		),
		path(
			"api/schema/redoc/",
			SpectacularRedocView.as_view(url_name="schema"),
			name="redoc",
		),
		# API routes
		path("articles/post/", post_article),
		path("articles/edit/", edit_article),
		path("trials/edit/", edit_trial),
		# Old feed routes. These now permanently redirect (301) to the
		# site-scoped routes below, on brain-regeneration.com (site id 3)
		# -- see rss/views.py. DO NOT DELETE these patterns in a future
		# cleanup or Django upgrade: removing them turns a redirect into a
		# 404, and a feed reader never surfaces that as an error -- it
		# just goes silent, quietly dropping every subscriber still on the
		# old URL. See docs/03-api-and-rss-feeds.md#old-feed-urls--permanent-redirect.
		path(
			"feed/author/<str:orcid>/",
			redirect_articles_by_author_feed,
			name="articles_by_author_feed",
		),
		path(
			"feed/trials/subject/<str:subject_slug>/",
			redirect_trials_by_subject_feed,
			name="trials_by_subject_feed",
		),
		# Site-scoped feed routes (see rss/views.py). Scoped to the
		# REQUESTED site's CustomSetting.scope_subjects, not the caller's --
		# crawler/reader-facing like the sitemaps below, not request-scoped
		# like the rest of the API. Every one is cached for an hour for the
		# same reason (the response doesn't vary by caller); the 301
		# redirects above are not, they cost nothing.
		path(
			"feed/sites/<int:site_id>/author/<str:orcid>/",
			cache_page(FEED_CACHE_SECONDS)(SiteArticlesByAuthorFeed()),
			name="site_articles_by_author_feed",
		),
		path(
			"feed/sites/<int:site_id>/articles/subject/<str:subject_slug>/",
			cache_page(FEED_CACHE_SECONDS)(SiteArticlesBySubjectFeed()),
			name="site_articles_by_subject_feed",
		),
		path(
			"feed/sites/<int:site_id>/articles/subject/<str:subject_slug>/relevant/",
			cache_page(FEED_CACHE_SECONDS)(SiteArticlesBySubjectFeed(relevant_only=True)),
			name="site_relevant_articles_by_subject_feed",
		),
		path(
			"feed/sites/<int:site_id>/articles/category/<str:category_slug>/",
			cache_page(FEED_CACHE_SECONDS)(SiteArticlesByCategoryFeed()),
			name="site_articles_by_category_feed",
		),
		path(
			"feed/sites/<int:site_id>/articles/category/<str:category_slug>/relevant/",
			cache_page(FEED_CACHE_SECONDS)(SiteArticlesByCategoryFeed(relevant_only=True)),
			name="site_relevant_articles_by_category_feed",
		),
		path(
			"feed/sites/<int:site_id>/trials/subject/<str:subject_slug>/",
			cache_page(FEED_CACHE_SECONDS)(SiteTrialsBySubjectFeed()),
			name="site_trials_by_subject_feed",
		),
		path(
			"feed/sites/<int:site_id>/trials/subject/<str:subject_slug>/recruiting/",
			cache_page(FEED_CACHE_SECONDS)(SiteRecruitingTrialsBySubjectFeed()),
			name="site_recruiting_trials_by_subject_feed",
		),
		# Patent feeds (rss/patent_feeds.py). Need rss_enabled AND
		# patents_enabled on the site; 404 otherwise.
		path(
			"feed/sites/<int:site_id>/patents/subject/<str:subject_slug>/",
			cache_page(FEED_CACHE_SECONDS)(SitePatentsBySubjectFeed()),
			name="site_patents_by_subject_feed",
		),
		path(
			"feed/sites/<int:site_id>/patents/category/<str:category_slug>/",
			cache_page(FEED_CACHE_SECONDS)(SitePatentsByCategoryFeed()),
			name="site_patents_by_category_feed",
		),
		# EMA medicine event feeds (rss/medicine_feeds.py). Need rss_enabled AND
		# medicines_enabled on the site; 404 otherwise.
		path(
			"feed/sites/<int:site_id>/medicines/category/<str:category_slug>/",
			cache_page(FEED_CACHE_SECONDS)(SiteMedicineEventsByCategoryFeed()),
			name="site_medicines_by_category_feed",
		),
		path(
			"feed/sites/<int:site_id>/medicines/subject/<str:subject_slug>/",
			cache_page(FEED_CACHE_SECONDS)(SiteMedicineEventsBySubjectFeed()),
			name="site_medicines_by_subject_feed",
		),
		path(
			"feed/sites/<int:site_id>/medicines/",
			cache_page(FEED_CACHE_SECONDS)(SiteMedicineEventsFeed()),
			name="site_medicines_feed",
		),
		# Search feeds (rss/search_feeds.py, and patents/search/ in
		# rss/patent_feeds.py). NOT wrapped in cache_page: they cache
		# themselves, keyed on the canonical query string, so ?b&a and
		# ?a&b -- and utm_* noise -- share one entry.
		path(
			"feed/sites/<int:site_id>/articles/search/",
			SiteArticlesSearchFeed(),
			name="site_articles_search_feed",
		),
		path(
			"feed/sites/<int:site_id>/trials/search/",
			SiteTrialsSearchFeed(),
			name="site_trials_search_feed",
		),
		path(
			"feed/sites/<int:site_id>/patents/search/",
			SitePatentsSearchFeed(),
			name="site_patents_search_feed",
		),
		# robots.txt for the API host: Disallow the search feeds. nginx has to
		# proxy /robots.txt to Django (nginx-example-configuration/nginx.conf).
		path("robots.txt", robots_txt, name="robots_txt"),
		# Docker healthcheck probe (admin/health.py, django/healthcheck.py).
		path("healthz/", healthz, name="healthz"),
		# Site-scoped sitemaps (see rss/sitemaps.py)
		path(
			"sitemap/sites/<int:site_id>/index.xml",
			sitemap_index,
			name="site-sitemap-index",
		),
		path(
			"sitemap/sites/<int:site_id>/<slug:section>.xml",
			sitemap_section,
			name="site-sitemap-section",
		),
		# Organization routes
		re_path(r"^accounts/", include("organizations.urls")),
		re_path(r"^invitations/", include(invitation_backend().get_urls())),
		# Protected endpoint route
		path(
			"protected_endpoint/",
			ProtectedEndpointView.as_view(),
			name="protected_endpoint",
		),
		# Subscriptions route
		path("subscriptions/new/", subscribe_view),
		path(
			"subscriptions/unsubscribe/<uuid:token>/list/<int:list_id>/",
			unsubscribe_list,
			name="unsubscribe_list",
		),
		path(
			"subscriptions/unsubscribe/<uuid:token>/site/<int:site_id>/",
			unsubscribe_site,
			name="unsubscribe_site",
		),
		path(
			"subscriptions/unsubscribe/<uuid:token>/all/",
			unsubscribe_all,
			name="unsubscribe_all",
		),
		# Author outreach opt-out (docs/author-outreach.md, "Approval
		# workflow"). token is AuthorOutreach.opt_out_token, not the
		# Subscribers.unsubscribe_token the three routes above use — these
		# are two independent systems (see AuthorContactOptOut's model
		# docstring).
		path(
			"subscriptions/author-optout/<uuid:token>/",
			author_optout,
			name="author_optout",
		),
		# Postmark webhook (suppression / reactivation) — provider-agnostic
		# path, not /webhooks/postmark/, since it's the only webhook consumer
		# today; dispatch is on RecordType (see subscriptions/views.py).
		path("webhooks/", postmark_webhook, name="postmark_webhook"),
		# Email template preview and testing routes
		path("emails/", email_preview_dashboard, name="email_preview_dashboard"),
		path(
			"emails/preview/<str:template_name>/",
			email_template_preview,
			name="email_template_preview",
		),
		path(
			"emails/context/<str:template_name>/",
			email_template_json_context,
			name="email_template_json_context",
		),
		path("emails/lists/", email_preview_lists, name="email_preview_lists"),
		path(
			"emails/subscribers/",
			email_preview_subscribers,
			name="email_preview_subscribers",
		),
		# Team API (excluded from API root, requires authentication)
		path("", include(teams_router.urls)),
		## List categories by team and subject
		path(
			"teams/<int:team_id>/subjects/<int:subject_id>/categories/",
			CategoriesByTeamAndSubject.as_view({"get": "list"}),
			name="categories-by-team-and-subject",
		),
		# Search endpoints
		path("articles/search/", ArticleSearchView.as_view(), name="article-search"),
		path("trials/search/", TrialSearchView.as_view(), name="trial-search"),
		path("authors/search/", AuthorSearchView.as_view(), name="author-search"),
		# Stats endpoint
		path("stats/", StatsView.as_view(), name="stats"),
		# Discovery entry point for site-scoped visibility: unscoped by design,
		# because a caller needs a site_id and nothing else would tell it which
		# exist. See SITE-API-VISIBILITY-SPEC.md (local planning doc).
		path("sites/", PublicSitesView.as_view(), name="public_sites"),
		# Separate from sites/ on purpose (see PublicSiteSerializer's
		# docstring): this one carries MCP configuration and, for a
		# site-bound key, that caller's own private tenant -- sites/ must
		# stay unscoped public discovery only.
		path("tenants/", McpTenantsView.as_view(), name="mcp_tenants"),
		# MCP editor routes (api/editor_views.py): the service credential plus a
		# verified editor and site. Specific paths first -- the read viewsets
		# below would otherwise take "resolve" for an article id.
		path("editor/articles/resolve/", EditorArticleResolveView.as_view(), name="editor-article-resolve"),
		path(
			"editor/articles/<int:article_id>/editorial/",
			EditorArticleEditorialView.as_view(),
			name="editor-article-editorial",
		),
		path(
			"editor/articles/<int:article_id>/relevance/<int:subject_id>/",
			EditorArticleRelevanceView.as_view(),
			name="editor-article-relevance",
		),
		path(
			"editor/articles/<int:article_id>/trials/",
			EditorArticleTrialsView.as_view(),
			name="editor-article-trials",
		),
		path(
			"editor/articles/<int:article_id>/trials/<int:trial_id>/",
			EditorArticleTrialDetailView.as_view(),
			name="editor-article-trial",
		),
		path(
			"editor/articles/<int:article_id>/categories/<int:category_id>/",
			EditorArticleCategoryView.as_view(),
			name="editor-article-category",
		),
		path(
			"editor/articles/<int:article_id>/history/",
			EditorArticleHistoryView.as_view(),
			name="editor-article-history",
		),
		path("editor/patents/resolve/", EditorPatentResolveView.as_view(), name="editor-patent-resolve"),
		path(
			"editor/patents/<int:patent_id>/editorial/",
			EditorPatentEditorialView.as_view(),
			name="editor-patent-editorial",
		),
		path(
			"editor/patents/<int:patent_id>/categories/<int:category_id>/",
			EditorPatentCategoryView.as_view(),
			name="editor-patent-category",
		),
		path(
			"editor/patents/<int:patent_id>/trials/<int:trial_id>/",
			EditorPatentTrialDetailView.as_view(),
			name="editor-patent-trial",
		),
		path(
			"editor/patents/<int:patent_id>/history/",
			EditorPatentHistoryView.as_view(),
			name="editor-patent-history",
		),
		path(
			"editor/categories/<int:category_id>/medicines/<int:medicine_id>/",
			EditorCategoryMedicineView.as_view(),
			name="editor-category-medicine",
		),
		path("editor/sources/preview/", EditorSourcePreviewView.as_view(), name="editor-source-preview"),
		path("editor/sources/", EditorSourcesView.as_view(), name="editor-sources"),
		path("editor/tenants/", EditorTenantsView.as_view(), name="editor-tenants"),
		path("editor/stats/", EditorStatsView.as_view(), name="editor-stats"),
		path("editor/", include(editor_router.urls)),
		# OAuth 2.1 authorization server for MCP editor access (MCP-AUTH-PLAN.md):
		# /o/authorize/, /o/token/, /o/revoke/, /o/introspect/, /o/register/ and
		# the RFC 8414 metadata at /.well-known/oauth-authorization-server.
		path("", include("mcpauth.urls")),
		# Gregory app routes
		path("", include("gregory.urls")),
		# Include router routes
		path("", include(router.urls)),
		# CKEditor 5 routes — hardened upload view must come BEFORE the package include
		# so Django's URL resolver picks it up first.
		# CK_EDITOR_5_UPLOAD_FILE_VIEW_NAME in settings.py points to this name so
		# the widget's upload_url is built from our view, not the package's.
		path(
			"ckeditor5/image_upload/",
			ckeditor_upload,
			name="subscriptions_ckeditor_upload",
		),
		path("ckeditor5/", include("django_ckeditor_5.urls")),
	]
	+ static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
	+ (
		static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
		if settings.DEBUG
		else []
	)
)
