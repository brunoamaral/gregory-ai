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
    
    def get_filename(self, renderer_context):
        """
        Determine the filename for CSV export based on the request path.
        """
        filename = "gregory-data.csv"
        if renderer_context and 'request' in renderer_context:
            # Determine the object type (articles, trials, etc.)
            path = renderer_context['request'].path.lower()
            object_type = 'data'
            
            if 'article' in path:
                object_type = 'articles'
            elif 'trial' in path:
                object_type = 'trials'
            elif 'author' in path:
                object_type = 'authors'
            elif 'category' in path:
                object_type = 'categories'
            elif 'subject' in path:
                object_type = 'subjects'
                
            current_date = datetime.now().strftime('%Y-%m-%d')
            filename = f"gregory-ai-{object_type}-{current_date}.csv"
        
        return filename
    
    def render(self, data, accepted_media_type=None, renderer_context=None):
        """
        Render the data to CSV bytes. The view layer will handle streaming via finalize_response.
        This keeps the renderer compliant with DRF's contract (returns bytes, not Response).
        """
        # Handle paginated data
        if isinstance(data, dict) and 'results' in data and isinstance(data['results'], list):
            data = data['results']
            
        # Log unique article_ids just before CSV generation
        if isinstance(data, list):
            article_ids = [item.get('article_id') for item in data if isinstance(item, dict) and 'article_id' in item]
            logger.debug(f"CSV Export: Final unique article_ids before CSV: {set(article_ids)} (count: {len(set(article_ids))})")
        
        # Generate CSV content as a string (not streaming)
        csv_content = self.generate_csv_content(data)
        
        # Return as UTF-8 encoded bytes (DRF renderer contract)
        return csv_content.encode('utf-8')
    
    def generate_csv_content(self, data):
        """
        Generate complete CSV content as a string.
        """
        # Process data to flatten and consolidate it
        processed_data = self.process_data(data)
        
        # Log unique article_ids in processed_data
        article_ids = [item.get('article_id') for item in processed_data if isinstance(item, dict) and 'article_id' in item]
        logger.debug(f"CSV Export: Processed data unique article_ids: {set(article_ids)} (count: {len(set(article_ids))})")
        
        # Flatten to a table structure
        table = self.tablize(processed_data)
        
        if not table or len(table) == 0:
            return ""
            
        # Extract header and rows
        header = table[0]
        rows = table[1:] if len(table) > 1 else []
        
        # Create CSV output
        csv_buffer = io.StringIO()
        csv_writer = csv.writer(
            csv_buffer,
            quoting=csv.QUOTE_ALL,
            quotechar='"',
            doublequote=True,
            lineterminator='\n'
        )
        
        # Write header row
        csv_writer.writerow(header)
        
        skipped = 0
        written = 0
        # Write each data row
        for idx, row in enumerate(rows):
            # Ensure all values are strings
            string_row = ['' if item is None else str(item) for item in row]
            
            try:
                csv_writer.writerow(string_row)
                written += 1
            except Exception as e:
                logger.error(f"CSV Export: Skipped row {idx} due to error: {e}. Row content: {string_row}")
                skipped += 1
        
        logger.debug(f"CSV Export: Attempted {len(rows)} rows, written {written}, skipped {skipped}")
        
        # Return the complete CSV as a string
        return csv_buffer.getvalue()
    
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
