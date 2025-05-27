"""
URL patterns for email template preview and testing functionality.
"""

from django.urls import path
from . import views

urlpatterns = [
    # Email preview dashboard
    path('emails/', views.email_preview_dashboard, name='email_preview_dashboard'),
    
    # Individual template previews
    path('emails/preview/<str:template_name>/', views.email_template_preview, name='email_template_preview'),
    
    # Template context as JSON for debugging
    path('emails/context/<str:template_name>/', views.email_template_json_context, name='email_template_json_context'),
    
    # Template variants comparison
    path('emails/variants/', views.email_template_variants, name='email_template_variants'),
]
