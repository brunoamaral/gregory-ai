from django.shortcuts import render, redirect
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.utils.html import format_html
from django.db.models import Q, Case, When, Value, BooleanField, Count
from django.contrib import messages
from django.utils.dateparse import parse_date
from django.utils import timezone
from .models import Articles, Subject, ArticleSubjectRelevance, MLPredictions
import json
from datetime import datetime, timedelta

@staff_member_required
def article_review_status_view(request):
    """
    Custom admin view for reviewing articles by subject
    """
    # Get all subjects for the dropdown
    subjects = Subject.objects.all().order_by('subject_name')
    
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
            # Articles where the relevance for the selected subject has not been reviewed
            queryset = queryset.filter(
                article_subject_relevances__subject__id=selected_subject_id,
                article_subject_relevances__is_relevant__isnull=True
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
            status_html = format_html('<span style="color: #009900; font-weight: bold;">Relevant</span>')
        elif is_relevant is False:
            status_html = format_html('<span style="color: #cc0000; font-weight: bold;">Not Relevant</span>')
        else:
            status_html = format_html('<span style="color: #f90; font-weight: bold;">Not Reviewed</span>')
        
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
                status_html = '<span style="color: #009900; font-weight: bold;">Relevant</span>'
                
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
                status_html = '<span style="color: #cc0000; font-weight: bold;">Not Relevant</span>'
                
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
                status_html = '<span style="color: #f90; font-weight: bold;">Not Reviewed</span>'
            
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
