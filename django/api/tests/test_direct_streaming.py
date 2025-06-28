import unittest
import io
import csv
from django.http import StreamingHttpResponse
from api.direct_streaming import DirectStreamingCSVRenderer

class DirectStreamingCSVRendererTest(unittest.TestCase):
    def test_renderer_returns_streaming_response(self):
        """Test that the DirectStreamingCSVRenderer returns a StreamingHttpResponse"""
        # Create test data
        data = [
            {'id': 1, 'title': 'Test Article 1', 'summary': 'Summary 1'},
            {'id': 2, 'title': 'Test Article 2', 'summary': 'Summary 2'},
            {'id': 3, 'title': 'Test Article 3', 'summary': 'Summary 3'},
        ]
        
        # Create the renderer
        renderer = DirectStreamingCSVRenderer()
        
        # Create a mock renderer context
        renderer_context = {
            'request': type('obj', (object,), {
                'path': '/articles/'
            })
        }
        
        # Render the data
        response = renderer.render(data, accepted_media_type='text/csv', renderer_context=renderer_context)
        
        # Verify the response is a StreamingHttpResponse
        self.assertIsInstance(response, StreamingHttpResponse)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertTrue('attachment; filename=' in response['Content-Disposition'])
        
        # Verify the CSV content
        content = b''.join(response.streaming_content).decode('utf-8')
        csv_reader = csv.reader(io.StringIO(content))
        rows = list(csv_reader)
        
        # Verify we have a header row and data rows
        self.assertEqual(len(rows), 4)  # Header + 3 data rows
        
        # Verify the header contains the expected columns
        self.assertTrue('id' in rows[0])
        self.assertTrue('title' in rows[0])
        self.assertTrue('summary' in rows[0])
        
        # Verify data rows contain the expected values
        title_index = rows[0].index('title')
        self.assertEqual(rows[1][title_index], 'Test Article 1')
        self.assertEqual(rows[2][title_index], 'Test Article 2')
        self.assertEqual(rows[3][title_index], 'Test Article 3')

if __name__ == '__main__':
    unittest.main()
