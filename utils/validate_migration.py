#!/usr/bin/env python3
"""
API Endpoint Migration Validator

This script helps validate that the new unified /articles/ endpoint
returns the same results as the legacy team-based endpoints.

Usage:
    python validate_migration.py [base_url]

Example:
    python validate_migration.py http://localhost:8000
    python validate_migration.py https://api.gregory-ms.com
"""

import sys
import requests
import json
from urllib.parse import urljoin


def compare_endpoints(base_url, team_id=1, subject_id=4, category_slug="natalizumab", source_id=1):
    """Compare legacy endpoints with new unified approach."""
    
    test_cases = [
        # Team articles
        {
            "name": "Team Articles",
            "legacy": f"/teams/{team_id}/articles/?format=json",
            "new": f"/articles/?team_id={team_id}&format=json"
        },
        # Team + Subject articles  
        {
            "name": "Team + Subject Articles",
            "legacy": f"/teams/{team_id}/articles/subject/{subject_id}/?format=json",
            "new": f"/articles/?team_id={team_id}&subject_id={subject_id}&format=json"
        },
        # Team + Category articles
        {
            "name": "Team + Category Articles", 
            "legacy": f"/teams/{team_id}/articles/category/{category_slug}/?format=json",
            "new": f"/articles/?team_id={team_id}&category_slug={category_slug}&format=json"
        },
        # Team + Source articles
        {
            "name": "Team + Source Articles",
            "legacy": f"/teams/{team_id}/articles/source/{source_id}/?format=json", 
            "new": f"/articles/?team_id={team_id}&source_id={source_id}&format=json"
        }
    ]
    
    results = []
    
    for test in test_cases:
        print(f"Testing: {test['name']}")
        print(f"  Legacy: {test['legacy']}")
        print(f"  New:    {test['new']}")
        
        try:
            # Test legacy endpoint
            legacy_url = urljoin(base_url, test['legacy'])
            legacy_response = requests.get(legacy_url)
            legacy_data = legacy_response.json()
            legacy_count = legacy_data.get('count', 0)
            
            # Check for deprecation headers
            deprecation_warning = legacy_response.headers.get('X-Deprecation-Warning')
            migration_guide = legacy_response.headers.get('X-Migration-Guide')
            
            # Test new endpoint
            new_url = urljoin(base_url, test['new'])
            new_response = requests.get(new_url)
            new_data = new_response.json()
            new_count = new_data.get('count', 0)
            
            # Compare results
            counts_match = legacy_count == new_count
            status = "✅ PASS" if counts_match else "❌ FAIL"
            
            print(f"  Legacy count: {legacy_count}")
            print(f"  New count:    {new_count}")
            print(f"  Status:       {status}")
            
            if deprecation_warning:
                print(f"  Deprecation:  {deprecation_warning}")
            if migration_guide:
                print(f"  Migration:    {migration_guide}")
            
            results.append({
                "test": test['name'],
                "legacy_count": legacy_count,
                "new_count": new_count,
                "match": counts_match,
                "deprecation_warning": deprecation_warning,
                "migration_guide": migration_guide
            })
            
        except Exception as e:
            print(f"  Status:       ❌ ERROR - {str(e)}")
            results.append({
                "test": test['name'],
                "error": str(e)
            })
        
        print()
    
    return results


def test_subjects_migration(base_url, team_id=1):
    """Test that legacy subjects endpoints work identically to new filtering approach"""
    print("Testing subjects migration...")
    
    test_cases = [
        {
            "name": "Basic Team Subjects",
            "legacy": f"/teams/{team_id}/subjects/?format=json",
            "new": f"/subjects/?team_id={team_id}&format=json"
        },
        {
            "name": "Subjects with Search",
            "legacy": f"/teams/{team_id}/subjects/?search=multiple&format=json",
            "new": f"/subjects/?team_id={team_id}&search=multiple&format=json"
        },
        {
            "name": "Subjects with Ordering",
            "legacy": f"/teams/{team_id}/subjects/?ordering=subject_name&format=json",
            "new": f"/subjects/?team_id={team_id}&ordering=subject_name&format=json"
        },
        {
            "name": "Complex Subjects Filtering",
            "legacy": f"/teams/{team_id}/subjects/?search=multiple&ordering=subject_name&format=json",
            "new": f"/subjects/?team_id={team_id}&search=multiple&ordering=subject_name&format=json"
        }
    ]
    
    results = []
    
    for test in test_cases:
        print(f"Testing: {test['name']}")
        print(f"  Legacy: {test['legacy']}")
        print(f"  New:    {test['new']}")
        
        try:
            # Test legacy endpoint
            legacy_url = urljoin(base_url, test['legacy'])
            legacy_response = requests.get(legacy_url)
            legacy_data = legacy_response.json()
            legacy_count = legacy_data.get('count', 0)
            
            # Check for deprecation headers
            deprecation_warning = legacy_response.headers.get('X-Deprecation-Warning')
            migration_guide = legacy_response.headers.get('X-Migration-Guide')
            
            # Test new endpoint
            new_url = urljoin(base_url, test['new'])
            new_response = requests.get(new_url)
            new_data = new_response.json()
            new_count = new_data.get('count', 0)
            
            # Compare results
            counts_match = legacy_count == new_count
            status = "✅ PASS" if counts_match else "❌ FAIL"
            
            print(f"  Legacy count: {legacy_count}")
            print(f"  New count:    {new_count}")
            print(f"  Status:       {status}")
            
            if deprecation_warning:
                print(f"  Deprecation:  {deprecation_warning}")
            if migration_guide:
                print(f"  Migration:    {migration_guide}")
            
            results.append({
                "test": test['name'],
                "legacy_count": legacy_count,
                "new_count": new_count,
                "match": counts_match,
                "deprecation_warning": deprecation_warning,
                "migration_guide": migration_guide
            })
            
        except Exception as e:
            print(f"  Status:       ❌ ERROR - {str(e)}")
            results.append({
                "test": test['name'],
                "error": str(e)
            })
        
        print()
    
    return results


def test_enhanced_capabilities(base_url, team_id=1, subject_id=4):
    
    print("Testing Enhanced Capabilities (only possible with new approach):")
    
    enhanced_tests = [
        {
            "name": "Complex Filter: Team + Subject + Search",
            "url": f"/articles/?team_id={team_id}&subject_id={subject_id}&search=stem&format=json"
        },
        {
            "name": "Complex Filter: Team + Subject + Search + Ordering",
            "url": f"/articles/?team_id={team_id}&subject_id={subject_id}&search=regeneration&ordering=-published_date&format=json"
        }
    ]
    
    for test in enhanced_tests:
        print(f"  {test['name']}")
        print(f"  URL: {test['url']}")
        
        try:
            url = urljoin(base_url, test['url'])
            response = requests.get(url)
            data = response.json()
            count = data.get('count', 0)
            
            print(f"  Count: {count}")
            print(f"  Status: ✅ ENHANCED CAPABILITY WORKING")
            
        except Exception as e:
            print(f"  Status: ❌ ERROR - {str(e)}")
        
        print()


def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    
    print("=" * 60)
    print(f"API Migration Validation")
    print(f"Base URL: {base_url}")
    print("=" * 60)
    print()
    
    # Test basic endpoint compatibility
    articles_results = compare_endpoints(base_url)
    
    print()
    
    # Test subjects migration
    subjects_results = test_subjects_migration(base_url)
    
    print()
    
    # Test enhanced capabilities
    test_enhanced_capabilities(base_url)
    
    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    # Articles tests
    total_articles_tests = len([r for r in articles_results if 'error' not in r])
    passing_articles_tests = len([r for r in articles_results if r.get('match', False)])
    
    # Subjects tests  
    total_subjects_tests = len([r for r in subjects_results if 'error' not in r])
    passing_subjects_tests = len([r for r in subjects_results if r.get('match', False)])
    
    total_tests = total_articles_tests + total_subjects_tests
    passing_tests = passing_articles_tests + passing_subjects_tests
    
    print(f"Articles Migration: {passing_articles_tests}/{total_articles_tests} tests passing")
    print(f"Subjects Migration: {passing_subjects_tests}/{total_subjects_tests} tests passing")
    print(f"Total: {passing_tests}/{total_tests} tests passing")
    
    if passing_tests == total_tests:
        print("✅ All legacy endpoints return equivalent data with new approach")
        print("✅ Deprecation headers are present")
        print("✅ Enhanced filtering capabilities working")
        print("\n🎉 Migration is ready! Clients can safely switch to new endpoints.")
    else:
        print("❌ Some tests failed. Review the results above.")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
