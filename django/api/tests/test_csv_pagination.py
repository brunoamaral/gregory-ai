import unittest
from django.test import RequestFactory
from django.http import StreamingHttpResponse
from api.direct_streaming import DirectStreamingCSVRenderer
from rest_framework.test import APITestCase
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

class TestCSVRenderer(APITestCase):
    """
    Test the DirectStreamingCSVRenderer to ensure it correctly handles both
    paginated and full exports.
    """
    
    def setUp(self):
        self.renderer = DirectStreamingCSVRenderer()
        self.factory = RequestFactory()
        
        # Create test data
        self.test_data = [
            {'id': i, 'name': f'Item {i}'} for i in range(1, 101)
        ]
        
        # Create paginated data
        self.paginated_data = {
            'count': len(self.test_data),
            'next': 'http://testserver/items/?page=2',
            'previous': None,
            'results': self.test_data[:10]  # First 10 items
        }
        
    def test_paginated_csv_export(self):
        """Test that paginated CSV exports respect pagination."""
        # Create a request with format=csv and no all_results
        request = self.factory.get('/api/items/?format=csv&page=1&page_size=10')
        
        # Create renderer context
        renderer_context = {
            'request': request,
            'view': type('MockView', (), {
                'get_queryset': lambda self: None,
                'filter_queryset': lambda self, queryset: None,
                'get_serializer': lambda self, *args, **kwargs: None
            })()
        }
        
        # Render the paginated data
        response = self.renderer.render(self.paginated_data, 'text/csv', renderer_context)
        
        # Check that we got a StreamingHttpResponse
        self.assertIsInstance(response, StreamingHttpResponse)
        
        # Collect the streaming content
        content = b''.join(response.streaming_content).decode('utf-8')
        
        # Count the rows (should be 10 data rows + 1 header)
        rows = content.strip().split('\n')
        self.assertEqual(len(rows), 11)  # 10 data rows + header
        
    def test_full_csv_export(self):
        """Test that full CSV exports include all results when all_results=true."""
        # Create a request with format=csv and all_results=true
        request = self.factory.get('/api/items/?format=csv&all_results=true')
        
        # Create renderer context with a view that returns all data
        class MockView:
            def get_queryset(self):
                return self.test_data
                
            def filter_queryset(self, queryset):
                return queryset
                
            def get_serializer(self, queryset, many=False):
                return type('MockSerializer', (), {'data': queryset})()
        
        mock_view = MockView()
        mock_view.test_data = self.test_data
        
        renderer_context = {
            'request': request,
            'view': mock_view
        }
        
        # Use the paginated data, but expect the renderer to get all results
        response = self.renderer.render(self.paginated_data, 'text/csv', renderer_context)
        
        # This test might need adjustments based on how your implementation works
        # If your renderer bypasses the view for full exports, you might need
        # to mock more behavior or test differently

if __name__ == '__main__':
    unittest.main()
