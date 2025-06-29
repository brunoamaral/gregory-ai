from rest_framework_csv.renderers import CSVRenderer
import csv
import io
from django.http import StreamingHttpResponse
from django.utils.text import slugify
from datetime import datetime
import json

class DirectStreamingCSVRenderer(CSVRenderer):
    """
    A CSV renderer that directly returns a StreamingHttpResponse without relying on middleware.
    This is a more reliable approach for streaming CSV data.
    """
    media_type = 'text/csv'
    format = 'csv'
    charset = 'utf-8'
    
    # Define the order of columns we want to appear first
    PREFERRED_COLUMN_ORDER = [
        'article_id', 'title', 'summary', 'link', 'doi', 'published_date', 'discovery_date',
        'container_title', 'publisher', 'access', 'authors', 'subjects', 'relevant_subjects',
        'article_subject_relevances', 'sources', 'takeaways', 'ml_predictions', 'clinical_trials', 
        'team_categories'
    ]
    
    # Define columns to exclude from the CSV output
    EXCLUDED_COLUMNS = [
        'teams'
    ]
    
    def render(self, data, accepted_media_type=None, renderer_context=None):
        """
        Render the data and return a StreamingHttpResponse directly.
        """
        # Set the custom filename in the response headers
        filename = "gregory-data.csv"
        if renderer_context and 'request' in renderer_context:
            # Determine the object type (articles, trials, etc.)
            path = renderer_context['request'].path.lower()
            object_type = 'data'
            
            if 'article' in path:
                object_type = 'articles'
            elif 'trial' in path:
                object_type = 'trials'
            elif 'categor' in path:
                object_type = 'categories'
            elif 'subject' in path:
                object_type = 'subjects'
                
            current_date = datetime.now().strftime('%Y-%m-%d')
            filename = f"gregory-ai-{object_type}-{current_date}.csv"
        
        # Check if this is paginated data
        if isinstance(data, dict) and 'results' in data and isinstance(data['results'], list):
            # Replace the entire data with just the results
            data = data['results']
            
        # Create a StreamingHttpResponse with the generator function
        response = StreamingHttpResponse(
            streaming_content=self.generate_csv_rows(data),
            content_type='text/csv'
        )
        
        # Set the filename for download
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
    
    def generate_csv_rows(self, data):
        """
        Generator function that yields CSV rows one at a time.
        """
        # Process data to flatten and consolidate it
        processed_data = self.process_data(data)
        
        # Flatten to a table structure
        table = self.tablize(processed_data)
        
        if not table or len(table) == 0:
            yield ""
            return
            
        # Extract header and rows
        header = table[0]
        rows = table[1:] if len(table) > 1 else []
        
        # Create a CSV writer for each row
        csv_buffer = io.StringIO()
        csv_writer = csv.writer(
            csv_buffer,
            quoting=csv.QUOTE_ALL,
            quotechar='"',
            doublequote=True,
            lineterminator='\n'
        )
        
        # Write and yield the header row
        csv_writer.writerow(header)
        yield csv_buffer.getvalue()
        csv_buffer.seek(0)
        csv_buffer.truncate(0)
        
        # Write and yield each data row
        for row in rows:
            # Ensure all values are strings
            string_row = ['' if item is None else str(item) for item in row]
            
            try:
                csv_writer.writerow(string_row)
                yield csv_buffer.getvalue()
                csv_buffer.seek(0)
                csv_buffer.truncate(0)
            except Exception:
                # Skip problematic rows
                csv_buffer.seek(0)
                csv_buffer.truncate(0)
    
    def process_data(self, data):
        """
        Process the data to prepare it for CSV output.
        """
        if not isinstance(data, list):
            if isinstance(data, dict):
                data = [data]
            else:
                return []
                
        # Process each item
        processed_data = []
        for item in data:
            if not isinstance(item, dict):
                continue
                
            # Create a new item with processed values
            processed_item = {}
            
            # Copy the item with proper string conversion
            for key, value in item.items():
                # Skip excluded columns
                if key in self.EXCLUDED_COLUMNS or any(key.startswith(excluded + '.') for excluded in self.EXCLUDED_COLUMNS):
                    continue
                    
                # Clean text fields (remove line breaks)
                if key in ['summary', 'title', 'takeaways', 'summary_plain_english'] and isinstance(value, str):
                    # Replace line breaks with spaces
                    value = value.replace('\n', ' ').replace('\r', ' ')
                    # Normalize multiple spaces to single spaces
                    while '  ' in value:
                        value = value.replace('  ', ' ')
                    
                # Handle lists and nested objects
                if isinstance(value, (list, dict)):
                    try:
                        processed_item[key] = json.dumps(value)
                    except:
                        processed_item[key] = str(value)
                else:
                    processed_item[key] = value
                    
            processed_data.append(processed_item)
            
        return processed_data
    
    def tablize(self, data):
        """
        Convert data to a table format suitable for CSV.
        """
        if not data:
            return []
            
        # Get all unique keys
        all_keys = set()
        for item in data:
            all_keys.update(item.keys())
            
        # Sort keys with preferred columns first
        ordered_keys = []
        
        # First add preferred columns in the specified order
        for key in self.PREFERRED_COLUMN_ORDER:
            if key in all_keys:
                ordered_keys.append(key)
                all_keys.remove(key)
                
        # Then add remaining keys alphabetically
        ordered_keys.extend(sorted(all_keys))
        
        # Create the table
        table = [ordered_keys]  # Header row
        
        # Add data rows
        for item in data:
            row = []
            for key in ordered_keys:
                row.append(item.get(key, ''))
            table.append(row)
            
        return table
