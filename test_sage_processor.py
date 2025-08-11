#!/usr/bin/env python3
"""
Test script for SAGE Publications feed processor

This script demonstrates the new SAGE Publications feed processor
functionality without running the full management command.
"""

# Mock entry data based on the SAGE Publications RSS feed structure
sage_feed_entries = [
    {
        'title': 'Gender and Gender Inequalities: Elucidating the Role of Supervisor–Employee Gender Congruence Through United States Survey Results',
        'link': 'https://journals.sagepub.com/doi/abs/10.1177/21582440251334940?ai=2b4&mi=ehikzz&af=R',
        'content_encoded': 'SAGE Open, <a href="https://journals.sagepub.com/toc/sgoa/15/2">Volume 15, Issue 2</a>, April-June 2025. <br/>Gender inequalities in the workplace present a profound challenge, undermining not only the psychological well-being and performance of employees but also the fabric of organizational justice and efficiency. Such inequalities are detrimental to the ...',
        'description': 'SAGE Open, Volume 15, Issue 2, April-June 2025. <br/>Gender inequalities in the workplace present a profound challenge, undermining not only the psychological well-being and performance of employees but also the fabric of organizational justice and efficiency. Such inequalities are detrimental to the ...',
        'dc_title': 'Gender and Gender Inequalities: Elucidating the Role of Supervisor–Employee Gender Congruence Through United States Survey Results',
        'dc_identifier': 'doi:10.1177/21582440251334940',
        'dc_source': 'SAGE Open',
        'dc_date': '2025-05-07T11:07:52Z',
        'dc_creator': 'Kuk-Kyoung Moon, Jaeyoung Lim',
        'prism_publicationname': 'SAGE Open',
        'prism_volume': '15',
        'prism_number': '2',
        'prism_coverdate': '2025-04-01T07:00:00Z',
        'prism_coverdisplaydate': '2025-04-01T07:00:00Z',
        'prism_doi': '10.1177/21582440251334940',
        'prism_url': 'https://journals.sagepub.com/doi/abs/10.1177/21582440251334940?ai=2b4&mi=ehikzz&af=R',
    },
    {
        'title': 'Exploring Reflections on the Interviewing Process of Intimate Partner Violence in Mongolia: "I Felt it Was Like a Torch Light Showing the Way to the Future"',
        'link': 'https://journals.sagepub.com/doi/abs/10.1177/21582440251327547?ai=2b4&mi=ehikzz&af=R',
        'content_encoded': 'SAGE Open, <a href="https://journals.sagepub.com/toc/sgoa/15/2">Volume 15, Issue 2</a>, April-June 2025. <br/>The widespread occurrence of violence against women takes place across diverse backgrounds, highlighting the importance of research on the survivors\' experiences. However, a notable gap exists between this research and the analysis of the survivors\' ...',
        'description': 'SAGE Open, Volume 15, Issue 2, April-June 2025. <br/>The widespread occurrence of violence against women takes place across diverse backgrounds, highlighting the importance of research on the survivors\' experiences. However, a notable gap exists between this research and the analysis of the survivors\' ...',
        'dc_title': 'Exploring Reflections on the Interviewing Process of Intimate Partner Violence in Mongolia: "I Felt it Was Like a Torch Light Showing the Way to the Future"',
        'dc_identifier': 'doi:10.1177/21582440251327547',
        'dc_source': 'SAGE Open',
        'dc_date': '2025-05-07T11:00:12Z',
        'dc_creator': 'Khongorzul Amarsanaa, József Rácz, Mónika Kovács',
        'prism_publicationname': 'SAGE Open',
        'prism_volume': '15',
        'prism_number': '2',
        'prism_coverdate': '2025-04-01T07:00:00Z',
        'prism_coverdisplaydate': '2025-04-01T07:00:00Z',
        'prism_doi': '10.1177/21582440251327547',
        'prism_url': 'https://journals.sagepub.com/doi/abs/10.1177/21582440251327547?ai=2b4&mi=ehikzz&af=R',
    }
]

def test_sage_processor():
    """Test the SAGE Publications feed processor."""
    print("🧪 Testing SAGE Publications Feed Processor")
    print("=" * 50)
    
    # Import the processor - this would normally be done in Django context
    try:
        import sys
        sys.path.append('/Users/brunoamaral/Labs/gregory/django')
        
        # Mock command for testing
        class MockCommand:
            def __init__(self):
                self.verbosity = 2
        
        from gregory.management.commands.feedreader_articles import SagePublicationsFeedProcessor
        
        # Create processor instance
        mock_command = MockCommand()
        processor = SagePublicationsFeedProcessor(mock_command)
        
        print("✅ SagePublicationsFeedProcessor imported successfully")
        
        # Test URL detection
        test_urls = [
            'https://journals.sagepub.com/loi/sgoa?ai=2b4&mi=ehikzz&af=R',
            'https://journals.sagepub.com/rss/feed.xml',
            'https://pubmed.ncbi.nlm.nih.gov/rss/search/',  # Should be False
        ]
        
        print("\n🔍 Testing URL Detection:")
        for url in test_urls:
            can_process = processor.can_process(url)
            print(f"  {url}")
            print(f"    Can process: {can_process}")
        
        # Test DOI extraction
        print("\n🔑 Testing DOI Extraction:")
        for entry in sage_feed_entries:
            doi = processor.extract_doi(entry)
            print(f"  Title: {entry['title'][:50]}...")
            print(f"    DOI: {doi}")
        
        # Test summary extraction
        print("\n📄 Testing Summary Extraction:")
        for entry in sage_feed_entries:
            summary = processor.extract_summary(entry)
            print(f"  Title: {entry['title'][:50]}...")
            print(f"    Summary: {summary[:100]}...")
        
        # Test keyword filtering
        print("\n🎯 Testing Keyword Filtering:")
        
        # Mock source with keyword filter
        class MockSource:
            def __init__(self, keyword_filter):
                self.keyword_filter = keyword_filter
        
        # Test with gender-related keywords
        source_gender = MockSource("gender, workplace, inequality, violence")
        
        for entry in sage_feed_entries:
            should_include = processor.should_include_article(entry, source_gender)
            print(f"  Title: {entry['title'][:50]}...")
            print(f"    Should include (gender keywords): {should_include}")
        
        # Test with unrelated keywords
        source_medical = MockSource("cancer, diabetes, cardiovascular")
        
        for entry in sage_feed_entries:
            should_include = processor.should_include_article(entry, source_medical)
            print(f"  Title: {entry['title'][:50]}...")
            print(f"    Should include (medical keywords): {should_include}")
        
        print("\n✅ All tests completed successfully!")
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure you're in the Django environment and all dependencies are installed")
    except Exception as e:
        print(f"❌ Error during testing: {e}")

if __name__ == "__main__":
    test_sage_processor()
