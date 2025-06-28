from django.http import StreamingHttpResponse
from rest_framework.renderers import BaseRenderer
import csv
from io import StringIO
from api.renderers import FlattenedCSVRenderer
from datetime import datetime

class Echo:
    """
    An object that implements just the write method of the file-like interface.
    This is used for streaming CSV data without loading everything into memory.
    """
    def write(self, value):
        """Write the value by returning it, instead of storing in a buffer."""
        return value

class StreamingCSVRenderer(BaseRenderer):
    """
    A CSV renderer that streams the response, making it suitable for large datasets.
    It inherits CSV formatting logic from FlattenedCSVRenderer but uses streaming.
    """
    media_type = 'text/csv'
    format = 'csv'  # Use 'csv' format to make this the default CSV renderer
    charset = 'utf-8'
    
    def render(self, data, accepted_media_type=None, renderer_context=None):
        """
        Render a streaming CSV response.
        """
        if data is None:
            return ""
            
        # Set headers for the response
        if renderer_context and 'response' in renderer_context:
            # Determine the object type for the filename
            object_type = 'data'
            if 'request' in renderer_context:
                path = renderer_context['request'].path.lower()
                if 'article' in path:
                    object_type = 'articles'
                elif 'trial' in path:
                    object_type = 'trials'
            
            current_date = datetime.now().strftime('%Y-%m-%d')
            filename = f"gregory-ai-{object_type}-{current_date}.csv"
            
            # Set Content-Disposition header with the custom filename
            response = renderer_context['response']
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Content-Type'] = 'text/csv'
        
        # Extract the results from paginated data
        if isinstance(data, dict) and 'results' in data and isinstance(data['results'], list):
            data = data['results']
            
        # Use FlattenedCSVRenderer to preprocess data in smaller chunks
        batch_size = 100  # Process 100 records at a time
        
        # Handle empty data case
        if not data or not isinstance(data, list) or len(data) == 0:
            pseudo_file = Echo()
            writer = csv.writer(pseudo_file)
            # Return a simple CSV with headers only
            streaming_content = [writer.writerow(['No data found'])]
            return StreamingHttpResponse(
                streaming_content,
                content_type='text/csv'
            )
        
        # Process the first batch to get headers
        first_batch = data[:min(batch_size, len(data))]
        flattened_renderer = FlattenedCSVRenderer()
        
        # Pre-process the first batch to get the headers
        processed_batch = flattened_renderer._consolidate_authors(first_batch)
        processed_batch = flattened_renderer._consolidate_subjects(processed_batch)
        processed_batch = flattened_renderer._consolidate_ml_predictions(processed_batch)
        processed_batch = flattened_renderer._consolidate_clinical_trials(processed_batch)
        processed_batch = flattened_renderer._consolidate_team_categories(processed_batch)
        processed_batch = flattened_renderer._consolidate_sources(processed_batch)
        processed_batch = flattened_renderer._consolidate_article_subject_relevances(processed_batch)
        processed_batch = flattened_renderer._remove_excluded_columns(processed_batch)
        processed_batch = flattened_renderer.clean_data_for_csv(processed_batch)
        
        # Flatten the data structure to get the headers
        flattened_data = flattened_renderer.flatten_data(processed_batch)
        headers = flattened_data['header']
        
        # Reorder columns using the same logic as FlattenedCSVRenderer
        reordered_headers, _ = flattened_renderer._reorder_columns(headers, [])
        
        # Set up the streaming response
        pseudo_file = Echo()
        writer = csv.writer(pseudo_file)
        
        # Create a generator function for the streaming content
        def stream_csv():
            # Yield the header row
            yield writer.writerow(reordered_headers)
            
            # Process and yield the data in batches
            for i in range(0, len(data), batch_size):
                batch = data[i:i+batch_size]
                
                # Process this batch
                processed_batch = flattened_renderer._consolidate_authors(batch)
                processed_batch = flattened_renderer._consolidate_subjects(processed_batch)
                processed_batch = flattened_renderer._consolidate_ml_predictions(processed_batch)
                processed_batch = flattened_renderer._consolidate_clinical_trials(processed_batch)
                processed_batch = flattened_renderer._consolidate_team_categories(processed_batch)
                processed_batch = flattened_renderer._consolidate_sources(processed_batch)
                processed_batch = flattened_renderer._consolidate_article_subject_relevances(processed_batch)
                processed_batch = flattened_renderer._remove_excluded_columns(processed_batch)
                processed_batch = flattened_renderer.clean_data_for_csv(processed_batch)
                
                # Flatten the batch
                flattened_batch = flattened_renderer.flatten_data(processed_batch)
                
                # Reorder columns
                _, reordered_rows = flattened_renderer._reorder_columns(
                    flattened_batch['header'], 
                    flattened_batch['rows']
                )
                
                # Yield each row
                for row in reordered_rows:
                    yield writer.writerow(row)
        
        # Return the streaming response
        return StreamingHttpResponse(
            stream_csv(),
            content_type='text/csv'
        )
