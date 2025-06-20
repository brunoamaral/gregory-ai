from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.utils.html import format_html
from django.db.models import Q, Case, When, Value, BooleanField, Count
from .models import Articles, Subject, ArticleSubjectRelevance
import json

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
        queryset = queryset.filter(
            Q(title__icontains=search_query) | 
            Q(doi__icontains=search_query) |
            Q(summary__icontains=search_query)
        )
    
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
        
        articles_with_review_status.append({
            'article': article,
            'status_html': status_html,
            'edit_url': edit_url
        })
    
    context = {
        'title': 'Article Review Status',
        'subjects': subjects,
        'selected_subject_id': int(selected_subject_id) if selected_subject_id else None,
        'review_status': review_status,
        'search_query': search_query,
        'page_obj': page_obj,
        'articles_with_review_status': articles_with_review_status,
    }
    
    return render(request, 'admin/article_review_status.html', context)
