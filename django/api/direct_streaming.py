import csv
import io
import json
import logging
from django.http import StreamingHttpResponse
from django.utils.text import slugify
from datetime import datetime
from rest_framework_csv.renderers import CSVRenderer

# Configure logging
logger = logging.getLogger(__name__)

class DirectStreamingCSVRenderer(CSVRenderer):
    """
    A CSV renderer that directly returns a StreamingHttpResponse without relying on middleware.
    This is a more reliable approach for streaming CSV data.
    """
    media_type = 'text/csv'
    format = 'csv'
    charset = 'utf-8'
    
    # Initialize flags for simplified data
    using_simplified_data = False
    article_ids = []
    
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
    
    # List of text fields to clean line breaks from (for articles and trials)
    TEXT_FIELDS_TO_CLEAN = [
# Trials
'title',
'summary',
'summary_plain_english',
'scientific_title',
'primary_sponsor',
'target_size',
'study_type',
'study_design',
'phase',
'countries',
'contact_firstname',
'contact_lastname',
'contact_address',
'contact_tel',
'contact_affiliation',
'inclusion_criteria',
'exclusion_criteria',
'condition',
'intervention',
'primary_outcome',
'secondary_outcome',
'secondary_id',
'source_support',
'ethics_review_status',
'ethics_review_contact_address',
'ethics_review_contact_phone',
'therapeutic_areas',
'country_status',

# Articles
'title',
'summary',
'subjects',
'relevant_subjects',
'takeaways',
'ml_predictions',
'clinical_trials',
'team_categories',
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
        
        # Check if this is paginated data but ONLY for CSV requests
        if renderer_context and 'request' in renderer_context:
            request = renderer_context['request']
            
            # Helper function to get query params from both Django and DRF requests
            def get_query_params(request):
                # DRF request has query_params, Django request has GET
                return getattr(request, 'query_params', request.GET)
            
            query_params = get_query_params(request)
            
            # For CSV requests, check if we need to use paginated data or get all results
            if query_params.get('format', '').lower() == 'csv':
                # Check if this is explicitly paginated or if we should fetch all results
                all_results = query_params.get('all_results', '').lower() in ('true', '1', 'yes')
                
                # Check if we have paginated data
                if isinstance(data, dict) and 'results' in data:
                    # For paginated requests without all_results=true, just use the paginated data
                    if not all_results:
                        # Log what we're doing
                        logger.debug(f"CSV Export: Using paginated data with {len(data['results'])} items")
                        # Use the paginated results directly
                        data = data['results']
                    else:
                        # Extract the paginated results as fallback
                        paginated_data = data['results']
                        
                        # If we have a view object and all_results=true, try to get all results without pagination
                        if renderer_context.get('view'):
                            view = renderer_context['view']
                            
                            # Try a direct approach using a custom serializer for better performance
                            try:
                                # Get the original queryset
                                queryset = view.filter_queryset(view.get_queryset())
                                
                                # Log query details
                                query_str = str(queryset.query)
                                logger.debug(f"CSV Export: SQL Query: {query_str[:300]}...")  # Truncate long queries
                                
                                # Always use the serializer for all_results CSV export
                                serializer = view.get_serializer(queryset, many=True)
                                data = serializer.data
                                
                                logger.debug(f"CSV Export: Serialized data count: {len(data)}")
                                # Log unique article_ids if present
                                article_ids = [item.get('article_id') for item in data if isinstance(item, dict) and 'article_id' in item]
                                logger.debug(f"CSV Export: Unique article_ids in export: {set(article_ids)} (count: {len(set(article_ids))})")
                                self.using_simplified_data = False
                                
                            except Exception as e:
                                # Log the error
                                logger.error(f"CSV Export Error: {str(e)}")
                                
                                # Fallback to the standard approach
                                serializer = view.get_serializer(queryset, many=True)
                                serialized_data = serializer.data
                                
                                # Log the number of serialized items
                                serialized_count = len(serialized_data)
                                logger.debug(f"CSV Export: Serialized data count: {serialized_count}")
                                # Log unique article_ids in fallback data
                                article_ids = [item.get('article_id') for item in serialized_data if isinstance(item, dict) and 'article_id' in item]
                                logger.debug(f"CSV Export: Unique article_ids in export: {set(article_ids)} (count: {len(set(article_ids))})")
                                # Use the complete data instead of paginated data
                                data = serialized_data
                                self.using_simplified_data = False
                        else:
                            # Fallback to just using the paginated results
                            data = paginated_data
                            
        # If still paginated (fallback case), just use the results
        if isinstance(data, dict) and 'results' in data and isinstance(data['results'], list):
            data = data['results']
            
        # Log unique article_ids just before CSV generation
        if isinstance(data, list):
            article_ids = [item.get('article_id') for item in data if isinstance(item, dict) and 'article_id' in item]
            logger.debug(f"CSV Export: Final unique article_ids before CSV: {set(article_ids)} (count: {len(set(article_ids))})")
        
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
        Generator function that yields CSV rows one at a time, with detailed logging for skipped rows.
        """
        # Process data to flatten and consolidate it
        processed_data = self.process_data(data)
        
        # Log unique article_ids in processed_data
        article_ids = [item.get('article_id') for item in processed_data if isinstance(item, dict) and 'article_id' in item]
        logger.debug(f"CSV Export: Processed data unique article_ids: {set(article_ids)} (count: {len(set(article_ids))})")
        
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
        
        skipped = 0
        written = 0
        # Write and yield each data row
        for idx, row in enumerate(rows):
            # Ensure all values are strings
            string_row = ['' if item is None else str(item) for item in row]
            
            try:
                csv_writer.writerow(string_row)
                yield csv_buffer.getvalue()
                csv_buffer.seek(0)
                csv_buffer.truncate(0)
                written += 1
            except Exception as e:
                logger.error(f"CSV Export: Skipped row {idx} due to error: {e}. Row content: {string_row}")
                csv_buffer.seek(0)
                csv_buffer.truncate(0)
                skipped += 1
        
        logger.debug(f"CSV Export: Attempted {len(rows)} rows, written {written}, skipped {skipped}")
    
    def process_data(self, data):
        """
        Process the data to prepare it for CSV output.
        """
        if not isinstance(data, list):
            if isinstance(data, dict):
                data = [data]
            else:
                logger.warning(f"CSV Export: Invalid data type: {type(data)}, expected list or dict")
                return []
        
        # Log the number of items we're processing
        logger.debug(f"CSV Export: Processing {len(data)} items")
                
        # Process each item
        processed_data = []
        for item in data:
            if not isinstance(item, dict):
                logger.warning(f"CSV Export: Invalid item type: {type(item)}, expected dict")
                continue
                
            # Create a new item with processed values
            processed_item = {}
            
            # Copy the item with proper string conversion
            for key, value in item.items():
                # Skip excluded columns
                if key in self.EXCLUDED_COLUMNS or any(key.startswith(excluded + '.') for excluded in self.EXCLUDED_COLUMNS):
                    continue
                    
                # Clean text fields (remove line breaks)
                if key in self.TEXT_FIELDS_TO_CLEAN and isinstance(value, str):
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
        
        logger.debug(f"CSV Export: Processed data has {len(processed_data)} items")
        return processed_data
    
    def tablize(self, data):
        """
        Convert data to a table format suitable for CSV.
        """
        if not data:
            logger.warning("CSV Export: No data to tablize")
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
        
        logger.debug(f"CSV Export: Tablized data has {len(table)-1} rows (plus header)")
        return table
