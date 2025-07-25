#!/usr/bin/env python3
"""
Quick test script to verify search ordering fix.
This script can be run independently to test the search endpoints.
"""

import requests
import json
from datetime import datetime

def test_search_ordering():
    """Test that search endpoints properly order by discovery_date"""
    
    # Base URL - adjust if needed
    base_url = "http://localhost:8000"
    
    # Test data - you'll need to adjust these IDs based on your database
    test_params = {
        'team_id': 1,
        'subject_id': 1,
        'search': 'test',  # Generic search term
        'page_size': 5  # Small page for testing
    }
    
    print("Testing Article Search Ordering...")
    try:
        # Test ArticleSearchView
        response = requests.get(f"{base_url}/articles/search/", params=test_params)
        if response.status_code == 200:
            data = response.json()
            articles = data.get('results', [])
            
            print(f"Found {len(articles)} articles")
            if len(articles) >= 2:
                # Check if ordered by discovery_date (newest first)
                dates = [article.get('discovery_date') for article in articles if article.get('discovery_date')]
                if dates:
                    print("Article discovery dates:")
                    for i, date in enumerate(dates):
                        print(f"  {i+1}. {date}")
                    
                    # Verify ordering (newest first)
                    is_ordered = all(dates[i] >= dates[i+1] for i in range(len(dates)-1))
                    print(f"✅ Articles properly ordered by discovery_date: {is_ordered}")
                else:
                    print("⚠️  No discovery dates found in articles")
            else:
                print("⚠️  Not enough articles to test ordering")
        else:
            print(f"❌ Article search failed: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"❌ Article search error: {e}")
    
    print("\nTesting Trial Search Ordering...")
    try:
        # Test TrialSearchView
        response = requests.get(f"{base_url}/trials/search/", params=test_params)
        if response.status_code == 200:
            data = response.json()
            trials = data.get('results', [])
            
            print(f"Found {len(trials)} trials")
            if len(trials) >= 2:
                # Check if ordered by discovery_date (newest first)
                dates = [trial.get('discovery_date') for trial in trials if trial.get('discovery_date')]
                if dates:
                    print("Trial discovery dates:")
                    for i, date in enumerate(dates):
                        print(f"  {i+1}. {date}")
                    
                    # Verify ordering (newest first)
                    is_ordered = all(dates[i] >= dates[i+1] for i in range(len(dates)-1))
                    print(f"✅ Trials properly ordered by discovery_date: {is_ordered}")
                else:
                    print("⚠️  No discovery dates found in trials")
            else:
                print("⚠️  Not enough trials to test ordering")
        else:
            print(f"❌ Trial search failed: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"❌ Trial search error: {e}")
    
    print("\nTesting Custom Ordering Parameter...")
    try:
        # Test custom ordering parameter
        custom_params = test_params.copy()
        custom_params['ordering'] = '-published_date'
        
        response = requests.get(f"{base_url}/articles/search/", params=custom_params)
        if response.status_code == 200:
            data = response.json()
            articles = data.get('results', [])
            
            if articles:
                dates = [article.get('published_date') for article in articles if article.get('published_date')]
                if dates:
                    print("Article published dates (with custom ordering):")
                    for i, date in enumerate(dates):
                        print(f"  {i+1}. {date}")
                    print("✅ Custom ordering parameter working")
                else:
                    print("⚠️  No published dates found for custom ordering test")
            else:
                print("⚠️  No articles for custom ordering test")
        else:
            print(f"❌ Custom ordering test failed: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Custom ordering test error: {e}")

if __name__ == "__main__":
    test_search_ordering()
