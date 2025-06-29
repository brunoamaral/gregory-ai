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
            {'id': 1, 'title': 'Test Article 1', 'summary': 'Summary 1\nwith a line break'},
            {'id': 2, 'title': 'Test Article 2', 'summary': 'Summary 2\r\nwith different line breaks'},
            {'id': 3, 'title': 'Test Article 3', 'summary': 'Summary 3\rwith just CR'},
        ]
        
        # Create the renderer
        renderer = DirectStreamingCSVRenderer()
        
        # Create a mock renderer context
        renderer_context = {
            'request': type('obj', (object,), {
                'path': '/articles/',
                'query_params': {'format': 'csv'}
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
        summary_index = rows[0].index('summary')
        
        # Verify line breaks have been removed from summaries
        self.assertEqual(rows[1][title_index], 'Test Article 1')
        self.assertFalse('\n' in rows[1][summary_index])
        self.assertTrue('Summary 1 with a line break' in rows[1][summary_index])
        
        self.assertEqual(rows[2][title_index], 'Test Article 2')
        self.assertFalse('\r\n' in rows[2][summary_index])
        self.assertTrue('Summary 2 with different line breaks' in rows[2][summary_index])
        
        self.assertEqual(rows[3][title_index], 'Test Article 3')
        self.assertFalse('\r' in rows[3][summary_index])
        self.assertTrue('Summary 3 with just CR' in rows[3][summary_index])

if __name__ == '__main__':
    unittest.main()
