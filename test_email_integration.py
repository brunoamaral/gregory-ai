#!/usr/bin/env python3
"""
Test script for validating email template Django integration.
This script tests the new template system and management command integration.
"""

import os
import sys
import django
from pathlib import Path

# Add the Django project to Python path
project_root = Path(__file__).resolve().parent.parent
django_dir = project_root / 'django'
sys.path.insert(0, str(django_dir))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'admin.settings')
django.setup()

from django.template.loader import get_template
from django.contrib.sites.models import Site
from sitesettings.models import CustomSetting
from gregory.models import Articles, Trials
from subscriptions.models import Lists, Subscribers
from templates.emails.views import prepare_email_context


def test_template_rendering():
    """Test that our new templates render without errors."""
    print("🧪 Testing template rendering...")
    
    try:
        # Get or create test data
        site = Site.objects.get_current()
        try:
            customsettings = CustomSetting.objects.get(site=site)
        except CustomSetting.DoesNotExist:
            print("⚠️  CustomSetting not found, using mock data")
            customsettings = type('MockCustomSetting', (), {
                'title': 'Gregory AI - Test',
                'email_footer': 'Test Footer'
            })()
        
        # Mock subscriber
        mock_subscriber = type('MockSubscriber', (), {
            'email': 'test@example.com',
            'first_name': 'Test',
            'last_name': 'User'
        })()
        
        # Get some test articles and trials (limit to avoid performance issues)
        articles = Articles.objects.all()[:5]
        trials = Trials.objects.all()[:3]
        
        print(f"📊 Found {articles.count()} articles and {trials.count()} trials for testing")
        
        # Test each template type
        templates_to_test = [
            ('weekly_summary', 'emails/weekly_summary_new.html'),
            ('admin_summary', 'emails/admin_summary_new.html'),  
            ('trial_notification', 'emails/trial_notification_new.html')
        ]
        
        for email_type, template_path in templates_to_test:
            print(f"\n🔄 Testing {email_type} template...")
            
            try:
                # Prepare context using our new function
                context = prepare_email_context(
                    email_type=email_type,
                    articles=articles if email_type != 'trial_notification' else None,
                    trials=trials,
                    subscriber=mock_subscriber,
                    site=site,
                    custom_settings=customsettings
                )
                
                # Render template
                template = get_template(template_path)
                rendered_html = template.render(context)
                
                print(f"✅ {email_type} template rendered successfully ({len(rendered_html)} chars)")
                
                # Basic validation checks
                if '<html>' not in rendered_html.lower():
                    print(f"⚠️  Warning: {email_type} template missing HTML tags")
                
                if len(rendered_html) < 1000:
                    print(f"⚠️  Warning: {email_type} template seems unusually short")
                    
            except Exception as e:
                print(f"❌ Error rendering {email_type} template: {str(e)}")
                return False
                
        print("\n✅ All templates rendered successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Template rendering test failed: {str(e)}")
        return False


def test_management_command_integration():
    """Test that management commands can import and use our new functions."""
    print("\n🧪 Testing management command integration...")
    
    try:
        # Test imports
        from subscriptions.management.commands.send_weekly_summary import Command as WeeklyCommand
        from subscriptions.management.commands.send_admin_summary import Command as AdminCommand  
        from subscriptions.management.commands.send_trials_notification import Command as TrialsCommand
        
        print("✅ Management command imports successful")
        
        # Test that the prepare_email_context function is accessible
        from templates.emails.views import prepare_email_context
        print("✅ prepare_email_context function import successful")
        
        return True
        
    except ImportError as e:
        print(f"❌ Import error in management commands: {str(e)}")
        return False
    except Exception as e:
        print(f"❌ Management command integration test failed: {str(e)}")
        return False


def test_context_helpers():
    """Test that our context helper functions work correctly."""
    print("\n🧪 Testing context helper functions...")
    
    try:
        from templates.emails.components.context_helpers import (
            prepare_weekly_summary_context,
            prepare_admin_summary_context,
            prepare_trial_notification_context,
            sort_articles_by_ml_score
        )
        
        print("✅ Context helper imports successful")
        
        # Test with minimal data
        articles = Articles.objects.all()[:3]
        trials = Trials.objects.all()[:2]
        
        mock_subscriber = type('MockSubscriber', (), {
            'email': 'test@example.com',
            'first_name': 'Test'
        })()
        
        site = Site.objects.get_current()
        
        # Test each context helper
        weekly_context = prepare_weekly_summary_context(
            articles=articles,
            trials=trials,
            subscriber=mock_subscriber,
            site=site,
            customsettings=None
        )
        
        print(f"✅ Weekly context prepared with {len(weekly_context.get('articles_with_context', []))} articles")
        
        admin_context = prepare_admin_summary_context(
            articles=articles,
            trials=trials,
            subscriber=mock_subscriber,
            site=site,
            customsettings=None
        )
        
        print(f"✅ Admin context prepared with {len(admin_context.get('articles_with_context', []))} articles")
        
        trial_context = prepare_trial_notification_context(
            trials=trials,
            subscriber=mock_subscriber,
            site=site,
            customsettings=None
        )
        
        print(f"✅ Trial context prepared with {len(trial_context.get('trials_with_context', []))} trials")
        
        return True
        
    except Exception as e:
        print(f"❌ Context helpers test failed: {str(e)}")
        return False


def main():
    """Run all tests."""
    print("🚀 Starting Django email template integration tests...\n")
    
    tests = [
        test_management_command_integration,
        test_context_helpers,
        test_template_rendering,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()  # Add spacing between tests
    
    print(f"📊 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Django integration is working correctly.")
        return True
    else:
        print("❌ Some tests failed. Please check the output above.")
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
