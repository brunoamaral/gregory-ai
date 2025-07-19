"""
Optimized Category Views to Replace Complex COUNT Queries

This module contains optimized versions of the category-related views that are causing
database performance issues with complex COUNT(*) queries and multiple JOINs.

The original issue is queries like:
SELECT COUNT(*)
FROM (
    SELECT "team_categories"."id" AS "col1"
    FROM "team_categories"
    LEFT OUTER JOIN "articles_team_categories" ...
    LEFT OUTER JOIN "articles" ...
    LEFT OUTER JOIN "trials_team_categories" ...
    LEFT OUTER JOIN "articles_authors" ...
    WHERE "team_categories"."id" = 4
    GROUP BY 1
) subquery;

These queries are hanging the database due to their complexity.
"""

from django.db import models
from django.db.models import Count, Q, Prefetch, Exists, OuterRef, Subquery
from django.db.models.functions import TruncMonth
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from gregory.models import TeamCategory, Articles, Trials, Authors, MLPredictions
from api.serializers import CategorySerializer, CategoryTopAuthorSerializer
from api.views import CategoryViewSet as OriginalCategoryViewSet


class OptimizedCategoryViewSet(OriginalCategoryViewSet):
    """
    Optimized version of CategoryViewSet that avoids complex COUNT queries.
    
    Instead of using annotations with multiple JOINs, we:
    1. Use exists() subqueries for filtering
    2. Calculate counts in separate, simple queries
    3. Use select_related and prefetch_related for efficiency
    4. Cache results when possible
    """
    
    def get_queryset(self):
        """
        Optimized queryset that avoids complex COUNT annotations.
        
        Instead of doing complex JOINs in a single query, we:
        1. Get the base queryset first
        2. Use separate, efficient queries for counts when needed
        3. Use prefetch_related to minimize N+1 queries
        """
        queryset = TeamCategory.objects.all()
        
        # Apply basic filters without expensive counts
        team_id = self.request.query_params.get('team_id')
        subject_id = self.request.query_params.get('subject_id')
        category_id = self.request.query_params.get('category_id')
        
        if team_id:
            queryset = queryset.filter(team_id=team_id)
        
        if subject_id:
            queryset = queryset.filter(subjects__id=subject_id)
            
        if category_id:
            queryset = queryset.filter(id=category_id)
        
        # Use select_related and prefetch_related for efficiency
        queryset = queryset.select_related('team').prefetch_related(
            'subjects',
            # Prefetch articles with simple filtering
            Prefetch(
                'articles',
                queryset=Articles.objects.select_related().only(
                    'article_id', 'title', 'published_date', 'discovery_date'
                )
            ),
            # Prefetch trials with simple filtering  
            Prefetch(
                'trials',
                queryset=Trials.objects.select_related().only(
                    'trial_id', 'title', 'published_date', 'discovery_date'
                )
            )
        )
        
        return queryset.distinct()
    
    def get_serializer_context(self):
        """Enhanced context with optimization flags"""
        context = super().get_serializer_context()
        
        # Add flag to use optimized counting
        context['use_optimized_counts'] = True
        
        return context


class OptimizedCategorySerializer(CategorySerializer):
    """
    Optimized serializer that calculates counts efficiently.
    """
    
    def get_article_count_total(self, obj):
        """Optimized article count using simple query"""
        # Use the prefetched articles if available
        if hasattr(obj, '_prefetched_objects_cache') and 'articles' in obj._prefetched_objects_cache:
            return len(obj._prefetched_objects_cache['articles'])
        
        # Simple count query without JOINs
        return obj.articles.count()
    
    def get_trials_count_total(self, obj):
        """Optimized trials count using simple query"""
        # Use the prefetched trials if available
        if hasattr(obj, '_prefetched_objects_cache') and 'trials' in obj._prefetched_objects_cache:
            return len(obj._prefetched_objects_cache['trials'])
        
        # Simple count query without JOINs
        return obj.trials.count()
    
    def get_authors_count(self, obj):
        """Optimized authors count using exists subquery"""
        # Use a more efficient subquery approach
        articles_subquery = obj.articles.values_list('article_id', flat=True)
        
        return Authors.objects.filter(
            articles__article_id__in=Subquery(articles_subquery)
        ).distinct().count()
    
    def get_top_authors(self, obj):
        """Optimized top authors calculation"""
        context = self.context
        author_params = context.get('author_params', {})
        
        if not author_params.get('include_authors', False):
            return []
        
        max_authors = author_params.get('max_authors', 10)
        
        # Use a more efficient approach with subqueries
        articles_subquery = obj.articles.values_list('article_id', flat=True)
        
        # Get authors with counts using a simpler approach
        top_authors = Authors.objects.filter(
            articles__article_id__in=Subquery(articles_subquery)
        ).annotate(
            category_articles_count=Count('articles', distinct=True)
        ).order_by('-category_articles_count')[:max_authors]
        
        return CategoryTopAuthorSerializer(top_authors, many=True).data


class OptimizedMonthlyCountsViewSet(viewsets.ModelViewSet):
    """
    Optimized monthly counts that avoid complex ML prediction queries.
    """
    
    def get_monthly_counts(self, team_category, ml_threshold=0.5):
        """
        Optimized monthly counts calculation.
        
        Instead of complex nested queries, we:
        1. Get basic monthly counts first
        2. Calculate ML counts in separate, simpler queries
        3. Combine results efficiently
        """
        
        # Basic monthly article counts (simple and fast)
        articles = team_category.articles.all()
        monthly_articles = articles.annotate(
            month=TruncMonth('published_date')
        ).values('month').annotate(
            count=Count('article_id')
        ).order_by('month')
        
        # Basic monthly trial counts (simple and fast)
        trials = team_category.trials.all()
        monthly_trials = trials.annotate(
            month=TruncMonth('published_date')
        ).values('month').annotate(
            count=Count('trial_id')
        ).order_by('month')
        
        # Optimized ML counts - use simpler approach
        ml_counts_by_model = {}
        
        # Get distinct algorithms first (simple query)
        available_models = MLPredictions.objects.filter(
            article__team_categories=team_category
        ).values_list('algorithm', flat=True).distinct()
        
        for model in available_models:
            # For each model, get articles with predictions above threshold
            # Use a simpler query structure
            article_ids_with_ml = MLPredictions.objects.filter(
                article__team_categories=team_category,
                algorithm=model,
                probability_score__gte=ml_threshold
            ).values_list('article_id', flat=True).distinct()
            
            # Get monthly counts for these articles
            ml_articles = Articles.objects.filter(
                article_id__in=article_ids_with_ml,
                team_categories=team_category
            ).annotate(
                month=TruncMonth('published_date')
            ).values('month').annotate(
                count=Count('article_id', distinct=True)
            ).order_by('month')
            
            ml_counts_by_model[model] = list(ml_articles)
        
        return {
            'monthly_article_counts': list(monthly_articles),
            'monthly_trial_counts': list(monthly_trials),
            'monthly_ml_article_counts_by_model': ml_counts_by_model,
            'ml_threshold': ml_threshold,
            'available_models': list(available_models)
        }


def optimize_category_queries():
    """
    Database optimization suggestions for category-related tables.
    
    Run these SQL commands to add missing indexes that will improve
    the performance of category-related queries:
    """
    
    optimizations = [
        # Add indexes for many-to-many relationship tables
        "CREATE INDEX IF NOT EXISTS idx_articles_team_categories_category_id ON articles_team_categories (teamcategory_id);",
        "CREATE INDEX IF NOT EXISTS idx_articles_team_categories_article_id ON articles_team_categories (articles_id);",
        "CREATE INDEX IF NOT EXISTS idx_trials_team_categories_category_id ON trials_team_categories (teamcategory_id);", 
        "CREATE INDEX IF NOT EXISTS idx_trials_team_categories_trial_id ON trials_team_categories (trials_id);",
        "CREATE INDEX IF NOT EXISTS idx_articles_authors_article_id ON articles_authors (articles_id);",
        "CREATE INDEX IF NOT EXISTS idx_articles_authors_author_id ON articles_authors (authors_id);",
        
        # Add indexes for date filtering
        "CREATE INDEX IF NOT EXISTS idx_articles_published_date ON articles (published_date);",
        "CREATE INDEX IF NOT EXISTS idx_articles_discovery_date ON articles (discovery_date);",
        "CREATE INDEX IF NOT EXISTS idx_trials_published_date ON trials (published_date);",
        "CREATE INDEX IF NOT EXISTS idx_trials_discovery_date ON trials (discovery_date);",
        
        # Add composite indexes for common query patterns
        "CREATE INDEX IF NOT EXISTS idx_team_categories_team_subject ON team_categories (team_id, id);",
        "CREATE INDEX IF NOT EXISTS idx_ml_predictions_article_algorithm ON ml_predictions (article_id, algorithm);",
        "CREATE INDEX IF NOT EXISTS idx_ml_predictions_score_threshold ON ml_predictions (probability_score) WHERE probability_score >= 0.5;",
        
        # Add indexes for the team categories table
        "CREATE INDEX IF NOT EXISTS idx_team_categories_slug ON team_categories (category_slug);",
        "CREATE INDEX IF NOT EXISTS idx_team_categories_team_id ON team_categories (team_id);",
    ]
    
    return optimizations


# Example of how to replace the problematic queries
class CategoryOptimizationMixin:
    """
    Mixin that provides optimized methods for category-related operations.
    """
    
    def get_category_stats_optimized(self, category_id):
        """
        Get category statistics using optimized queries instead of complex JOINs.
        
        This replaces the complex COUNT(*) FROM (...) GROUP BY queries
        with simpler, more efficient approaches.
        """
        category = TeamCategory.objects.select_related('team').get(id=category_id)
        
        # Simple counts without complex JOINs
        article_count = category.articles.count()
        trial_count = category.trials.count()
        
        # Authors count using exists() - more efficient than JOINs
        author_count = Authors.objects.filter(
            Exists(
                Articles.objects.filter(
                    team_categories=category,
                    authors=OuterRef('pk')
                )
            )
        ).count()
        
        return {
            'category': category,
            'article_count': article_count,
            'trial_count': trial_count,
            'author_count': author_count
        }
    
    def get_categories_with_counts_optimized(self, team_id=None, subject_id=None):
        """
        Get categories with counts using optimized approach.
        
        Instead of complex annotations, we use separate queries
        and combine the results efficiently.
        """
        queryset = TeamCategory.objects.select_related('team')
        
        if team_id:
            queryset = queryset.filter(team_id=team_id)
        if subject_id:
            queryset = queryset.filter(subjects__id=subject_id)
        
        # Prefetch related data efficiently
        queryset = queryset.prefetch_related(
            Prefetch('articles', queryset=Articles.objects.only('article_id')),
            Prefetch('trials', queryset=Trials.objects.only('trial_id')),
            'subjects'
        )
        
        results = []
        for category in queryset:
            # Use prefetched data for counts
            article_count = len(category.articles.all())
            trial_count = len(category.trials.all())
            
            results.append({
                'category': category,
                'article_count': article_count,
                'trial_count': trial_count,
            })
        
        return results
