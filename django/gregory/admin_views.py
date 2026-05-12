from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.utils.html import format_html, mark_safe
from django.db.models import Q, Case, When, Value, BooleanField, Count
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.contrib import messages
from django.utils.dateparse import parse_date
from django.utils import timezone
from .models import Articles, Subject, ArticleSubjectRelevance, MLPredictions, Sources, Trials, Team
import json
from datetime import datetime, timedelta, date

@staff_member_required
def article_review_status_view(request):
    """
    Custom admin view for reviewing articles by subject
    """
    # Get subjects based on user's organization
    if request.user.is_superuser:
        subjects = Subject.objects.all().order_by('subject_name')
    else:
        # Get user's organizations
        user_orgs = request.user.organizations_organizationuser.values_list('organization__id', flat=True)
        # Filter subjects by user's organization's teams
        subjects = Subject.objects.filter(team__organization__id__in=user_orgs).order_by('subject_name').distinct()
    
    # Default to first subject if none selected
    selected_subject_id = request.GET.get('subject_id')
    if not selected_subject_id and subjects.exists():
        selected_subject_id = subjects.first().id
    
    # Handle batch actions
    if request.method == 'POST' and selected_subject_id:
        action = request.POST.get('action')
        selected_articles = request.POST.getlist('selected_articles')
        
        if selected_articles and action:
            subject = Subject.objects.get(id=selected_subject_id)
            
            if action == 'mark_relevant':
                # Set the articles as relevant for the selected subject
                mark_articles_as_relevant(selected_articles, subject, True)
                messages.success(request, f"{len(selected_articles)} articles marked as relevant for {subject.subject_name}")
                
            elif action == 'mark_not_relevant':
                # Set the articles as not relevant for the selected subject
                mark_articles_as_relevant(selected_articles, subject, False)
                messages.success(request, f"{len(selected_articles)} articles marked as not relevant for {subject.subject_name}")
            
            elif action == 'mark_for_review':
                # Unset the relevance status for the selected subject
                mark_articles_for_review(selected_articles, subject)
                messages.success(request, f"{len(selected_articles)} articles marked for review for {subject.subject_name}")
            
            # Redirect to the same page to avoid form resubmission
            redirect_url = f"{request.path}?subject_id={selected_subject_id}"
            if 'review_status' in request.GET:
                redirect_url += f"&review_status={request.GET.get('review_status')}"
            if 'q' in request.GET:
                redirect_url += f"&q={request.GET.get('q')}"
            if 'page' in request.GET:
                redirect_url += f"&page={request.GET.get('page')}"
            if 'date_option' in request.GET:
                redirect_url += f"&date_option={request.GET.get('date_option')}"
                if request.GET.get('date_option') == 'custom':
                    if 'date_from' in request.GET:
                        redirect_url += f"&date_from={request.GET.get('date_from')}"
                    if 'date_to' in request.GET:
                        redirect_url += f"&date_to={request.GET.get('date_to')}"
            return redirect(redirect_url)
    
    # Review status filter
    review_status = request.GET.get('review_status', 'all')
    
    # Base queryset - articles that have the selected subject
    if selected_subject_id:
        queryset = Articles.objects.filter(subjects__id=selected_subject_id)
        
        # Apply review status filter if needed
        if review_status == 'reviewed':
            # Articles where the relevance for the selected subject has been reviewed
            queryset = queryset.filter(
                article_subject_relevances__subject__id=selected_subject_id,
                article_subject_relevances__is_relevant__isnull=False
            )
        elif review_status == 'not_reviewed':
            # Articles where the relevance for the selected subject has not been reviewed:
            # includes articles with no ArticleSubjectRelevance record OR one with is_relevant=None
            queryset = queryset.exclude(
                article_subject_relevances__subject__id=selected_subject_id,
                article_subject_relevances__is_relevant__isnull=False
            )
        
        # Order by discovery date
        queryset = queryset.order_by('-discovery_date').distinct()
    else:
        queryset = Articles.objects.none()
    
    # Search functionality
    search_query = request.GET.get('q', '')
    if search_query:
        upper_search = search_query.upper()
        queryset = queryset.filter(
            Q(utitle__contains=upper_search) | 
            Q(doi__icontains=search_query) |
            Q(usummary__contains=upper_search)
        )
    
    # Date filtering
    date_option = request.GET.get('date_option', '')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    
    # Apply date filter based on simple options or custom date range
    if date_option:
        today = timezone.now().date()
        
        if date_option == 'today':
            # Filter for articles discovered today
            queryset = queryset.filter(discovery_date=today)
        elif date_option == 'last_7_days':
            # Filter for articles discovered in the last 7 days
            seven_days_ago = today - timedelta(days=7)
            queryset = queryset.filter(discovery_date__gte=seven_days_ago)
        elif date_option == 'this_month':
            # Filter for articles discovered this month
            first_day_of_month = today.replace(day=1)
            queryset = queryset.filter(discovery_date__gte=first_day_of_month)
        elif date_option == 'this_year':
            # Filter for articles discovered this year
            first_day_of_year = today.replace(month=1, day=1)
            queryset = queryset.filter(discovery_date__gte=first_day_of_year)
        elif date_option == 'custom' and date_from and date_to:
            # Filter by custom discovery date range
            queryset = queryset.filter(
                discovery_date__gte=parse_date(date_from),
                discovery_date__lte=parse_date(date_to)
            )
    
    # Get sort parameter
    sort_by = request.GET.get('sort_by', '-discovery_date')
    
    # If not sorting by ML score, apply sorting to queryset before pagination
    if sort_by not in ['ml_score', '-ml_score']:
        queryset = queryset.order_by(sort_by).distinct()
    
    # Pagination
    paginator = Paginator(queryset, 25)  # Show 25 articles per page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # For each article, get the review status for the selected subject
    articles_with_review_status = []
    for article in page_obj.object_list:
        review_data = ArticleSubjectRelevance.objects.filter(
            article=article,
            subject_id=selected_subject_id
        ).values('is_relevant').first()
        
        is_relevant = review_data['is_relevant'] if review_data else None
        
        if is_relevant is True:
            status_html = mark_safe('<span style="color: #009900; font-weight: bold;">Relevant</span>')
        elif is_relevant is False:
            status_html = mark_safe('<span style="color: #cc0000; font-weight: bold;">Not Relevant</span>')
        else:
            status_html = mark_safe('<span style="color: #f90; font-weight: bold;">Not Reviewed</span>')
        
        edit_url = f'/admin/gregory/articles/{article.article_id}/change/'
        
        # Get latest ML predictions for this article and subject
        ml_predictions = MLPredictions.objects.filter(
            article=article,
            subject_id=selected_subject_id
        ).order_by('algorithm', '-created_date').distinct('algorithm')
        
        # Build a dictionary of predictions by algorithm and calculate average
        predictions_dict = {}
        scores = []
        for pred in ml_predictions:
            if pred.probability_score is not None:
                predictions_dict[pred.algorithm] = {
                    'score': pred.probability_score,
                    'predicted_relevant': pred.predicted_relevant
                }
                scores.append(pred.probability_score)
        
        # Calculate average score (None if no predictions)
        avg_score = sum(scores) / len(scores) if scores else None
        
        articles_with_review_status.append({
            'article': article,
            'status_html': status_html,
            'is_relevant': is_relevant,
            'edit_url': edit_url,
            'ml_predictions': predictions_dict,
            'avg_ml_score': avg_score
        })
    
    # If sorting by ML score, sort the articles_with_review_status list
    if sort_by in ['ml_score', '-ml_score']:
        # Sort by average ML score
        # Articles with no predictions (None) go to the end
        articles_with_review_status.sort(
            key=lambda x: (x['avg_ml_score'] is None, x['avg_ml_score'] if x['avg_ml_score'] is not None else 0),
            reverse=(sort_by == '-ml_score')
        )
    
    context = {
        'title': 'Article Review Status',
        'subjects': subjects,
        'selected_subject_id': int(selected_subject_id) if selected_subject_id else None,
        'review_status': review_status,
        'search_query': search_query,
        'page_obj': page_obj,
        'articles_with_review_status': articles_with_review_status,
        'date_option': date_option,
        'date_from': date_from,
        'date_to': date_to,
        'sort_by': sort_by,
    }
    
    return render(request, 'admin/article_review_status.html', context)


def mark_articles_as_relevant(article_ids, subject, is_relevant):
    """
    Mark multiple articles as relevant or not relevant for a specific subject
    """
    for article_id in article_ids:
        article = Articles.objects.get(article_id=article_id)
        
        # Get or create the relevance object
        relevance, created = ArticleSubjectRelevance.objects.get_or_create(
            article=article,
            subject=subject,
            defaults={'is_relevant': is_relevant}
        )
        
        # If the relevance object already exists, update its status
        if not created:
            relevance.is_relevant = is_relevant
            relevance.save()

def mark_articles_for_review(article_ids, subject):
    """
    Mark articles for review by setting is_relevant to NULL for a specific subject
    """
    for article_id in article_ids:
        article = Articles.objects.get(article_id=article_id)
        
        # Check if the relevance object exists
        try:
            relevance = ArticleSubjectRelevance.objects.get(
                article=article,
                subject=subject
            )
            # Set is_relevant to NULL
            relevance.is_relevant = None
            relevance.save()
        except ArticleSubjectRelevance.DoesNotExist:
            # No need to do anything if the relevance object doesn't exist
            # since no relevance means it's already marked for review
            pass

@staff_member_required
def update_article_relevance_ajax(request):
    """
    AJAX endpoint to update a single article's relevance status
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            article_id = data.get('article_id')
            subject_id = data.get('subject_id')
            action = data.get('action')  # 'mark_relevant', 'mark_not_relevant', or 'mark_for_review'
            
            article = Articles.objects.get(article_id=article_id)
            subject = Subject.objects.get(id=subject_id)
            
            if action == 'mark_relevant':
                relevance, created = ArticleSubjectRelevance.objects.get_or_create(
                    article=article,
                    subject=subject,
                    defaults={'is_relevant': True}
                )
                if not created:
                    relevance.is_relevant = True
                    relevance.save()
                status = 'relevant'
                status_html = mark_safe('<span style="color: #009900; font-weight: bold;">Relevant</span>')
                
            elif action == 'mark_not_relevant':
                relevance, created = ArticleSubjectRelevance.objects.get_or_create(
                    article=article,
                    subject=subject,
                    defaults={'is_relevant': False}
                )
                if not created:
                    relevance.is_relevant = False
                    relevance.save()
                status = 'not_relevant'
                status_html = mark_safe('<span style="color: #cc0000; font-weight: bold;">Not Relevant</span>')
                
            elif action == 'mark_for_review':
                try:
                    relevance = ArticleSubjectRelevance.objects.get(
                        article=article,
                        subject=subject
                    )
                    relevance.is_relevant = None
                    relevance.save()
                except ArticleSubjectRelevance.DoesNotExist:
                    pass
                status = 'not_reviewed'
                status_html = mark_safe('<span style="color: #f90; font-weight: bold;">Not Reviewed</span>')
            
            return JsonResponse({
                'success': True,
                'status': status,
                'status_html': status_html,
                'message': f'Article marked as {status.replace("_", " ")}'
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=400)
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)


# ── Source detail views ────────────────────────────────────────────────────

def _get_source_for_user(request, source_id):
    """Return the Source or raise 404/403 based on org scoping."""
    source = get_object_or_404(Sources, pk=source_id)
    if not request.user.is_superuser:
        user_orgs = request.user.organizations_organizationuser.values_list(
            'organization__id', flat=True
        )
        if not Sources.objects.filter(
            pk=source_id, team__organization__id__in=user_orgs
        ).exists():
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
    return source


@staff_member_required
def source_detail_view(request, source_id):
    source = _get_source_for_user(request, source_id)

    if source.source_for == 'trials':
        total_count = source.trials_set.count()
        last_date_val = (
            source.trials_set.order_by('-discovery_date')
            .values_list('discovery_date', flat=True).first()
        )
        last_date = last_date_val.date() if last_date_val else None
        recent_items = list(
            source.trials_set.order_by('-discovery_date')
            .values('trial_id', 'title', 'discovery_date')[:10]
        )
        item_type = 'trials'
    else:
        total_count = source.articles_set.count()
        last_date_val = (
            source.articles_set.order_by('-discovery_date')
            .values_list('discovery_date', flat=True).first()
        )
        last_date = last_date_val.date() if last_date_val else None
        recent_items = list(
            source.articles_set.order_by('-discovery_date')
            .values('article_id', 'title', 'discovery_date')[:10]
        )
        item_type = 'articles'

    days_since = (timezone.now().date() - last_date).days if last_date else None

    health_status = source.get_health_status()
    status_config = {
        'healthy':    {'label': 'Healthy',    'color': '#16a34a'},
        'warning':    {'label': 'Warning',    'color': '#f59e0b'},
        'error':      {'label': 'Error',      'color': '#dc2626'},
        'inactive':   {'label': 'Inactive',   'color': '#6b7280'},
        'no_content': {'label': 'No Content', 'color': '#2563eb'},
    }
    status_info = status_config.get(health_status, status_config['no_content'])

    context = {
        'source': source,
        'total_count': total_count,
        'last_date': last_date,
        'days_since': days_since,
        'recent_items': recent_items,
        'item_type': item_type,
        'health_status': health_status,
        'status_info': status_info,
        'title': source.name or f'Source {source_id}',
        'has_permission': True,
    }
    return render(request, 'admin/source_detail.html', context)


@staff_member_required
def source_activity_json(request, source_id):
    from django.db.models import Count
    from django.db.models.functions import TruncDate

    source = _get_source_for_user(request, source_id)

    today = timezone.now().date()
    start = today - timedelta(days=29)
    date_range = [start + timedelta(days=i) for i in range(30)]

    if source.source_for == 'trials':
        rows = (
            source.trials_set
            .filter(discovery_date__date__gte=start, discovery_date__date__lte=today)
            .annotate(day=TruncDate('discovery_date'))
            .values('day')
            .annotate(count=Count('pk'))
            .values_list('day', 'count')
        )
    else:
        rows = (
            source.articles_set
            .filter(discovery_date__date__gte=start, discovery_date__date__lte=today)
            .annotate(day=TruncDate('discovery_date'))
            .values('day')
            .annotate(count=Count('pk'))
            .values_list('day', 'count')
        )

    counts_map = dict(rows)

    labels = [d.strftime('%b %-d') for d in date_range]
    counts = [counts_map.get(d, 0) for d in date_range]

    return JsonResponse({'labels': labels, 'counts': counts})


# ── Sources overview view ──────────────────────────────────────────────────

STATUS_CONFIG = {
    'healthy':    {'label': 'Healthy',    'color': '#16a34a'},
    'warning':    {'label': 'Warning',    'color': '#f59e0b'},
    'error':      {'label': 'Error',      'color': '#dc2626'},
    'inactive':   {'label': 'Inactive',   'color': '#6b7280'},
    'no_content': {'label': 'No Content', 'color': '#2563eb'},
}


def _annotate_sources(qs):
    """Annotate a Sources queryset with last_content_date and content_count."""
    from django.db.models import Max, Count
    return qs.annotate(
        last_article_date_ann=Max('articles__published_date'),
        article_count_ann=Count('articles', distinct=True),
        last_trial_date_ann=Max('trials__last_updated'),
        trial_count_ann=Count('trials', distinct=True),
    )


def _health_from_source(source):
    """Compute health status for an annotated source row without extra queries."""
    if not source.active:
        return 'inactive'
    now = timezone.now()
    if source.source_for == 'trials':
        last_date = source.last_trial_date_ann
    else:
        last_date = source.last_article_date_ann
    if not last_date:
        return 'no_content'
    days = (now - last_date).days
    if days > 60:
        return 'error'
    elif days > 30:
        return 'warning'
    return 'healthy'


@staff_member_required
def sources_overview_view(request):
    # ── Base queryset (org-scoped) ────────────────────────────────────────
    if request.user.is_superuser:
        qs = Sources.objects.all()
    else:
        user_orgs = request.user.organizations_organizationuser.values_list(
            'organization__id', flat=True
        )
        qs = Sources.objects.filter(team__organization__id__in=user_orgs)

    # ── Bulk-action POST ──────────────────────────────────────────────────
    if request.method == 'POST':
        action = request.POST.get('bulk_action')
        # The shared intermediate template posts 'action=reassign_to_team_action' on confirmation
        if not action and request.POST.get('action') == 'reassign_to_team_action':
            action = 'reassign_to_team'
        selected_ids = request.POST.getlist('selected_sources')

        if selected_ids and action in ('activate', 'deactivate', 'reassign_to_team'):
            selected_qs = qs.filter(pk__in=selected_ids)

            if action == 'activate':
                count = selected_qs.update(active=True)
                messages.success(request, f'{count} source(s) activated.')

            elif action == 'deactivate':
                count = selected_qs.update(active=False)
                messages.success(request, f'{count} source(s) deactivated.')

            elif action == 'reassign_to_team':
                # Gather org of selected sources (must be single org)
                team_ids = selected_qs.values_list('team_id', flat=True).distinct()
                from .admin import ReassignToTeamForm
                org_ids = list(
                    Team.objects.filter(pk__in=team_ids)
                    .values_list('organization_id', flat=True).distinct()
                )
                if len(org_ids) != 1:
                    messages.error(request, 'Selected sources span multiple organisations. Please select sources from a single organisation.')
                else:
                    target_qs = Team.objects.filter(organization_id=org_ids[0])
                    if 'apply' not in request.POST:
                        form = ReassignToTeamForm()
                        form.fields['target_team'].queryset = target_qs
                        return render(request, 'admin/gregory/reassign_to_team_intermediate.html', {
                            'title': 'Reassign to team',
                            'objects': selected_qs,
                            'form': form,
                            'action_checkbox_name': 'selected_sources',
                            'model_name': 'sources',
                            'extra_post': {'bulk_action': 'reassign_to_team', 'selected_sources': selected_ids},
                        })
                    form = ReassignToTeamForm(request.POST)
                    form.fields['target_team'].queryset = target_qs
                    if form.is_valid():
                        to_team = form.cleaned_data['target_team']
                        if to_team.organization_id == org_ids[0]:
                            count = selected_qs.update(team=to_team)
                            messages.success(request, f'{count} source(s) reassigned to "{to_team}".')
                        else:
                            messages.error(request, 'Target team must belong to the same organisation.')
                    else:
                        messages.error(request, 'Invalid form — please try again.')

        from django.shortcuts import redirect as _redirect
        return _redirect(request.path + ('?' + request.META.get('QUERY_STRING', '') if request.META.get('QUERY_STRING') else ''))

    # ── GET filters ───────────────────────────────────────────────────────
    f_active     = request.GET.get('active', '')
    f_source_for = request.GET.get('source_for', '')
    f_method     = request.GET.get('method', '')
    f_health     = request.GET.get('health', '')
    f_team       = request.GET.get('team', '')
    f_subject    = request.GET.get('subject', '')
    f_q          = request.GET.get('q', '').strip()

    if f_active == 'true':
        qs = qs.filter(active=True)
    elif f_active == 'false':
        qs = qs.filter(active=False)

    if f_source_for:
        qs = qs.filter(source_for=f_source_for)
    if f_method:
        qs = qs.filter(method=f_method)
    if f_team:
        qs = qs.filter(team_id=f_team)
    if f_subject:
        qs = qs.filter(subject_id=f_subject)
    if f_q:
        qs = qs.filter(
            Q(name__icontains=f_q) | Q(link__icontains=f_q) |
            Q(description__icontains=f_q) | Q(keyword_filter__icontains=f_q)
        )

    qs = _annotate_sources(qs).select_related('team', 'subject').order_by('name')

    # Health filter is applied in-Python after annotation (same logic as SourceHealthFilter)
    if f_health:
        qs = [s for s in qs if _health_from_source(s) == f_health]
    else:
        qs = list(qs)

    # Attach computed health to each source row
    for s in qs:
        h = _health_from_source(s)
        s.health = h
        s.health_info = STATUS_CONFIG.get(h, STATUS_CONFIG['no_content'])
        if s.source_for == 'trials':
            s.last_content = s.last_trial_date_ann
            s.content_count = s.trial_count_ann
        else:
            s.last_content = s.last_article_date_ann
            s.content_count = s.article_count_ann

    # ── Pagination ─────────────────────────────────────────────────────────
    paginator = Paginator(qs, 50)
    page_num  = request.GET.get('page', 1)
    page_obj  = paginator.get_page(page_num)

    # Build URL-safe filter params string for pagination links (excludes 'page')
    _filter_params = request.GET.copy()
    _filter_params.pop('page', None)
    filter_params = _filter_params.urlencode()

    # ── Filter option lists ────────────────────────────────────────────────
    if request.user.is_superuser:
        teams    = Team.objects.order_by('name')
        subjects = Subject.objects.order_by('subject_name')
    else:
        user_orgs = request.user.organizations_organizationuser.values_list('organization__id', flat=True)
        teams    = Team.objects.filter(organization__id__in=user_orgs).order_by('name')
        subjects = Subject.objects.filter(team__organization__id__in=user_orgs).distinct().order_by('subject_name')

    now = timezone.now()

    context = {
        'page_obj': page_obj,
        'paginator': paginator,
        'teams': teams,
        'subjects': subjects,
        'filters': {
            'active': f_active,
            'source_for': f_source_for,
            'method': f_method,
            'health': f_health,
            'team': f_team,
            'subject': f_subject,
            'q': f_q,
        },
        'source_for_choices': Sources.TABLES,
        'method_choices': Sources.METHODS,
        'health_choices': [
            ('healthy', 'Healthy'),
            ('warning', 'Warning'),
            ('error', 'Error'),
            ('no_content', 'No Content'),
            ('inactive', 'Inactive'),
        ],
        'title': 'Sources Overview',
        'has_permission': True,
        'now': now,
        'filter_params': filter_params,
    }
    return render(request, 'admin/sources_overview.html', context)


# ── Subject analytics views ────────────────────────────────────────────────

def _get_scoped_org_ids_for_user(request):
    """Return list of org IDs the request user may see, or None for all."""
    if request.user.is_superuser:
        org_param = request.GET.get('org')
        if org_param:
            try:
                return [int(org_param)]
            except (ValueError, TypeError):
                pass
        return None
    return list(
        request.user.organizations_organizationuser.values_list(
            'organization__id', flat=True
        )
    )


@staff_member_required
def subject_analytics_view(request):
    """Landing page for subject content analytics."""
    from organizations.models import Organization
    if request.user.is_superuser:
        orgs = Organization.objects.order_by('name')
    else:
        user_org_ids = list(
            request.user.organizations_organizationuser.values_list(
                'organization__id', flat=True
            )
        )
        orgs = Organization.objects.filter(id__in=user_org_ids).order_by('name')

    context = {
        'title': 'Subject Content Analytics',
        'orgs': orgs,
        'is_superuser': request.user.is_superuser,
    }
    return render(request, 'admin/gregory/subject/analytics.html', context)


@staff_member_required
def subject_analytics_orgs(request):
    """JSON: organisations visible to the current user."""
    from organizations.models import Organization
    org_ids = _get_scoped_org_ids_for_user(request)
    qs = Organization.objects.all()
    if org_ids is not None:
        qs = qs.filter(id__in=org_ids)
    return JsonResponse({'organisations': [{'id': o.id, 'name': o.name} for o in qs.order_by('name')]})


@staff_member_required
def subject_analytics_teams(request):
    """JSON: teams scoped to the effective org."""
    org_ids = _get_scoped_org_ids_for_user(request)
    qs = Team.objects.filter(is_active=True)
    if org_ids is not None:
        qs = qs.filter(organization__id__in=org_ids)
    return JsonResponse({'teams': [{'id': t.id, 'name': str(t)} for t in qs.order_by('name')]})


@staff_member_required
def subject_analytics_subjects(request):
    """JSON: subjects scoped to the effective org and/or team."""
    org_ids = _get_scoped_org_ids_for_user(request)
    team_param = request.GET.get('team')
    qs = Subject.objects.all()
    if org_ids is not None:
        qs = qs.filter(team__organization__id__in=org_ids)
    if team_param:
        try:
            qs = qs.filter(team_id=int(team_param))
        except (ValueError, TypeError):
            pass
    return JsonResponse({'subjects': [{'id': s.id, 'name': s.subject_name} for s in qs.order_by('subject_name')]})


@staff_member_required
def subject_analytics_data(request):
    """
    JSON: articles and trials counts over time for a subject (and optional org/team scope).
    Query params: range (7d|30d|90d|365d|custom), start, end, org, team, subject
    """
    range_param = request.GET.get('range', '30d')
    subject_param = request.GET.get('subject', '')
    team_param = request.GET.get('team', '')

    RANGES = {
        '7d':   (7,   TruncDate,  '%Y-%m-%d'),
        '30d':  (30,  TruncDate,  '%Y-%m-%d'),
        '90d':  (90,  TruncWeek,  '%Y-%m-%d'),
        '365d': (365, TruncMonth, '%Y-%m'),
    }

    if range_param == 'custom':
        start_str = request.GET.get('start', '')
        end_str   = request.GET.get('end', '')
        try:
            start_date = date.fromisoformat(start_str)
            end_date   = date.fromisoformat(end_str)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid custom date range'}, status=400)
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        span = (end_date - start_date).days
        if span <= 60:
            trunc_fn, date_fmt = TruncDate, '%Y-%m-%d'
        elif span <= 180:
            trunc_fn, date_fmt = TruncWeek, '%Y-%m-%d'
        else:
            trunc_fn, date_fmt = TruncMonth, '%Y-%m'
    else:
        if range_param not in RANGES:
            range_param = '30d'
        days, trunc_fn, date_fmt = RANGES[range_param]
        end_date   = timezone.now().date()
        start_date = end_date - timedelta(days=days - 1)

    # Build full period sequence for zero-filling
    if trunc_fn is TruncDate:
        period_range = []
        d = start_date
        while d <= end_date:
            period_range.append(d)
            d += timedelta(days=1)
    elif trunc_fn is TruncWeek:
        period_range = []
        d = start_date - timedelta(days=start_date.weekday())
        while d <= end_date:
            period_range.append(d)
            d += timedelta(weeks=1)
    else:  # TruncMonth
        period_range = []
        d = start_date.replace(day=1)
        while d <= end_date:
            period_range.append(d)
            if d.month == 12:
                d = date(d.year + 1, 1, 1)
            else:
                d = date(d.year, d.month + 1, 1)

    def _build_series(qs, date_field):
        counts = (
            qs
            .annotate(period=trunc_fn(date_field))
            .values('period')
            .annotate(count=Count('pk', distinct=True))
            .order_by('period')
        )
        lookup = {}
        for item in counts:
            p = item['period']
            if not p:
                continue
            if hasattr(p, 'date'):
                p = p.date()
            lookup[p] = item['count']
        return [lookup.get(p, 0) for p in period_range]

    # Scope: org → team → subject
    org_ids = _get_scoped_org_ids_for_user(request)

    articles_qs = Articles.objects.filter(
        published_date__date__gte=start_date,
        published_date__date__lte=end_date,
    )
    trials_qs = Trials.objects.filter(
        discovery_date__date__gte=start_date,
        discovery_date__date__lte=end_date,
    )

    if org_ids is not None:
        articles_qs = articles_qs.filter(teams__organization__id__in=org_ids)
        trials_qs   = trials_qs.filter(teams__organization__id__in=org_ids)

    if team_param:
        try:
            tid = int(team_param)
            articles_qs = articles_qs.filter(teams__id=tid)
            trials_qs   = trials_qs.filter(teams__id=tid)
        except (ValueError, TypeError):
            pass

    if subject_param:
        try:
            sid = int(subject_param)
            articles_qs = articles_qs.filter(subjects__id=sid)
            trials_qs   = trials_qs.filter(subjects__id=sid)
        except (ValueError, TypeError):
            pass

    articles_qs = articles_qs.distinct()
    trials_qs   = trials_qs.distinct()

    articles_data = _build_series(articles_qs, 'published_date')
    trials_data   = _build_series(trials_qs, 'discovery_date')

    labels = [p.strftime(date_fmt) for p in period_range]

    return JsonResponse({
        'labels':   labels,
        'articles': articles_data,
        'trials':   trials_data,
        'totals': {
            'articles': sum(articles_data),
            'trials':   sum(trials_data),
        },
    })
