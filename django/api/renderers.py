from rest_framework_csv.renderers import CSVRenderer
import csv
import io
import re
from html import unescape
import json
from datetime import datetime
from django.utils.text import slugify
from django.http import StreamingHttpResponse

class FlattenedCSVRenderer(CSVRenderer):
    """
    A CSV renderer that flattens paginated responses for CSV output.
    This renderer removes pagination metadata and only includes the actual results.
    It also properly handles problematic text fields that may break CSV format.
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
        # Note: No need to specify individual nested fields like 'teams.0.id', etc.
        # as the exclusion logic already handles excluding all fields that start with
        # an excluded column name followed by a dot
    ]
    
    def render(self, data, accepted_media_type=None, renderer_context=None):
        """
        Render the data by extracting only the 'results' from paginated data.
        
        For paginated responses, we only want the actual results in CSV format,
        not the pagination metadata (count, next, previous, etc.).
        """
        # Set the custom filename in the response headers
        if renderer_context and 'response' in renderer_context:
            # Determine the object type (articles, trials, etc.)
            object_type = self._determine_object_type(renderer_context)
            current_date = datetime.now().strftime('%Y-%m-%d')
            filename = f"gregory-ai-{object_type}-{current_date}.csv"
            
            # Set Content-Disposition header with the custom filename
            renderer_context['response']['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        # Check if this is paginated data
        if isinstance(data, dict) and 'results' in data and isinstance(data['results'], list):
            # Replace the entire data with just the results
            data = data['results']
        
        # Consolidate various fields into single columns
        if isinstance(data, list):
            data = self._consolidate_authors(data)
            data = self._consolidate_subjects(data)
            data = self._consolidate_ml_predictions(data)
            data = self._consolidate_clinical_trials(data)
            data = self._consolidate_team_categories(data)
            data = self._consolidate_sources(data)
            data = self._consolidate_article_subject_relevances(data)
            data = self._remove_excluded_columns(data)
            
        # Clean text fields to ensure proper CSV formatting
        if isinstance(data, list):
            data = self.clean_data_for_csv(data)
            
        # Use enhanced CSV writer with proper escaping and column ordering
        return self.render_csv_with_escaping(data, renderer_context)
    
    def _determine_object_type(self, renderer_context):
        """
        Determine the object type based on the request path.
        Returns 'articles', 'trials', etc.
        """
        if not renderer_context or 'request' not in renderer_context:
            return 'data'
            
        path = renderer_context['request'].path.lower()
        
        if 'article' in path:
            return 'articles'
        elif 'trial' in path:
            return 'trials'
        elif 'categor' in path:
            return 'categories'
        elif 'subject' in path:
            return 'subjects'
        else:
            # Default to a generic name
            return 'data'
    
    def _consolidate_authors(self, data_list):
        """
        Consolidate author information into a single 'authors' field with comma-separated full names.
        """
        for item in data_list:
            if not isinstance(item, dict):
                continue
                
            # Check if the item has authors field as a list
            if 'authors' in item and isinstance(item['authors'], list):
                author_names = []
                
                # Extract author full names
                for author in item['authors']:
                    if isinstance(author, dict):
                        if 'full_name' in author:
                            author_names.append(str(author['full_name']))
                        # Fallback if full_name isn't available
                        elif 'given_name' in author and 'family_name' in author:
                            author_names.append(f"{author['given_name']} {author['family_name']}")
                
                # Replace the authors list with a comma-separated string of names
                item['authors'] = ', '.join(author_names)
                
                # Remove individual author fields
                for key in list(item.keys()):
                    if key.startswith('authors.'):
                        item.pop(key, None)
        
        return data_list
        
    def _consolidate_subjects(self, data_list):
        """
        Consolidate subject information into a single 'subjects' field with comma-separated subject names.
        """
        for item in data_list:
            if not isinstance(item, dict):
                continue
                
            # Check if the item has subjects field as a list
            if 'subjects' in item and isinstance(item['subjects'], list):
                subject_names = []
                
                # Extract subject names
                for subject in item['subjects']:
                    if isinstance(subject, dict):
                        if 'subject_name' in subject:
                            subject_names.append(str(subject['subject_name']))
                
                # Replace the subjects list with a comma-separated string of names
                item['subjects'] = ', '.join(subject_names)
                
                # Remove individual subject fields
                for key in list(item.keys()):
                    if key.startswith('subjects.'):
                        item.pop(key, None)
                        
            # Also handle article_subject_relevances field if present
            if 'article_subject_relevances' in item and isinstance(item['article_subject_relevances'], list):
                relevant_subjects = []
                
                for rel in item['article_subject_relevances']:
                    if isinstance(rel, dict) and 'subject' in rel and isinstance(rel['subject'], dict):
                        if 'subject_name' in rel['subject']:
                            relevant_subjects.append(str(rel['subject']['subject_name']))
                
                # Create or update a relevant_subjects field
                if relevant_subjects:
                    item['relevant_subjects'] = ', '.join(relevant_subjects)
                
                # Remove individual article_subject_relevances fields
                for key in list(item.keys()):
                    if key.startswith('article_subject_relevances.'):
                        item.pop(key, None)
        
        return data_list
    
    def _consolidate_ml_predictions(self, data_list):
        """
        Consolidate machine learning predictions into a summary format.
        """
        for item in data_list:
            if not isinstance(item, dict):
                continue
                
            # Check if the item has ml_predictions field as a list
            if 'ml_predictions' in item and isinstance(item['ml_predictions'], list):
                predictions_summary = []
                
                # Extract key prediction information
                for pred in item['ml_predictions']:
                    if isinstance(pred, dict):
                        algorithm = str(pred.get('algorithm', 'unknown'))
                        score = pred.get('probability_score', 0)
                        relevant = pred.get('predicted_relevant', False)
                        
                        # Format: algorithm (score) - relevant/not relevant
                        prediction_str = f"{algorithm} ({score:.2f}) - {'relevant' if relevant else 'not relevant'}"
                        predictions_summary.append(prediction_str)
                
                # Replace the ml_predictions list with a summary string
                item['ml_predictions'] = ' | '.join(predictions_summary)
                
                # Remove individual ml_predictions fields
                for key in list(item.keys()):
                    if key.startswith('ml_predictions.'):
                        item.pop(key, None)
        
        return data_list
    
    def _consolidate_clinical_trials(self, data_list):
        """
        Consolidate clinical trials information into a single field.
        """
        for item in data_list:
            if not isinstance(item, dict):
                continue
                
            # Check if the item has clinical_trials field as a list
            if 'clinical_trials' in item and isinstance(item['clinical_trials'], list):
                trials_info = []
                
                # Extract trial identifiers or names
                for trial in item['clinical_trials']:
                    if isinstance(trial, dict):
                        if 'trial_id' in trial:
                            # Ensure trial_id is a string
                            trials_info.append(str(trial['trial_id']))
                        elif 'nct_id' in trial:
                            # Ensure nct_id is a string
                            trials_info.append(str(trial['nct_id']))
                        elif 'title' in trial:
                            # Truncate long titles
                            title = str(trial['title'])
                            if len(title) > 50:
                                title = title[:47] + '...'
                            trials_info.append(title)
                
                # Replace the clinical_trials list with a comma-separated string
                # Ensure all items in trials_info are strings before joining
                string_trials_info = [str(info) for info in trials_info]
                item['clinical_trials'] = ', '.join(string_trials_info) if string_trials_info else 'None'
                
                # Remove individual clinical_trials fields
                for key in list(item.keys()):
                    if key.startswith('clinical_trials.'):
                        item.pop(key, None)
        
        return data_list
    
    def _consolidate_team_categories(self, data_list):
        """
        Consolidate team categories information into a single field.
        """
        for item in data_list:
            if not isinstance(item, dict):
                continue
                
            # Check if the item has team_categories field as a list
            if 'team_categories' in item and isinstance(item['team_categories'], list):
                categories = []
                
                # Extract category names
                for category in item['team_categories']:
                    if isinstance(category, dict) and 'category_name' in category:
                        categories.append(str(category['category_name']))
                
                # Replace the team_categories list with a comma-separated string
                item['team_categories'] = ', '.join(categories) if categories else 'None'
                
                # Remove individual team_categories fields
                for key in list(item.keys()):
                    if key.startswith('team_categories.'):
                        item.pop(key, None)
        
        return data_list
    
    def _consolidate_sources(self, data_list):
        """
        Consolidate sources information into a single field.
        """
        for item in data_list:
            if not isinstance(item, dict):
                continue
                
            # Check if the item has sources field as a list
            if 'sources' in item and isinstance(item['sources'], list):
                # If sources is already a list of strings, just join them
                if all(isinstance(source, str) for source in item['sources']):
                    item['sources'] = ', '.join(item['sources'])
                # If it's a list of objects, extract relevant information
                else:
                    source_names = []
                    for source in item['sources']:
                        if isinstance(source, dict):
                            if 'name' in source:
                                source_names.append(str(source['name']))
                            elif 'source_id' in source:
                                source_names.append(f"Source #{source['source_id']}")
                    
                    # Ensure all values are strings before joining
                    item['sources'] = ', '.join(source_names) if source_names else ', '.join(str(s) for s in item['sources'])
        
        return data_list
    
    def _consolidate_article_subject_relevances(self, data_list):
        """
        Consolidate article_subject_relevances information into a single field.
        """
        for item in data_list:
            if not isinstance(item, dict):
                continue
                
            # Check if the item has article_subject_relevances field as a list
            if 'article_subject_relevances' in item and isinstance(item['article_subject_relevances'], list):
                relevances = []
                
                # Extract subject relevance information
                for rel in item['article_subject_relevances']:
                    if isinstance(rel, dict):
                        subject_info = ""
                        
                        # Get subject name
                        if 'subject' in rel and isinstance(rel['subject'], dict):
                            if 'subject_name' in rel['subject']:
                                subject_info += str(rel['subject']['subject_name'])
                        
                        # Add relevance status if available
                        if 'is_relevant' in rel:
                            is_relevant = rel['is_relevant']
                            if is_relevant is not None:  # Only add if it's not None
                                status = "Relevant" if is_relevant else "Not Relevant"
                                subject_info += f" ({status})"
                        
                        if subject_info:
                            relevances.append(subject_info)
                
                # Replace the article_subject_relevances list with a summary string
                item['article_subject_relevances'] = ' | '.join(relevances) if relevances else 'None'
                
                # Remove individual article_subject_relevances fields
                for key in list(item.keys()):
                    if key.startswith('article_subject_relevances.'):
                        item.pop(key, None)
        
        return data_list
    
    def clean_data_for_csv(self, data_list):
        """
        Clean text fields in the data to make them safe for CSV.
        - Removes HTML/XML tags
        - Strips excessive whitespace
        - Ensures proper escaping of special characters
        """
        cleaned_data = []
        
        for item in data_list:
            if isinstance(item, dict):
                cleaned_item = {}
                for key, value in item.items():
                    # Handle nested dictionaries and lists
                    if isinstance(value, (dict, list)):
                        cleaned_item[key] = value  # Let the JSON serializer handle this
                    elif isinstance(value, str):
                        # Clean text fields
                        cleaned_value = self.clean_text_field(value)
                        cleaned_item[key] = cleaned_value
                    else:
                        cleaned_item[key] = value
                cleaned_data.append(cleaned_item)
            else:
                cleaned_data.append(item)
                
        return cleaned_data
    
    def clean_text_field(self, text):
        """
        Clean a text field by:
        1. Removing HTML/XML tags
        2. Converting HTML entities to their Unicode equivalents
        3. Replacing multiple spaces, tabs, and newlines with a single space
        """
        if not text:
            return text
            
        # Remove HTML/XML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        
        # Convert HTML entities to Unicode
        text = unescape(text)
        
        # Replace multiple spaces, tabs, and newlines with a single space
        text = re.sub(r'\s+', ' ', text)
        
        # Trim whitespace
        text = text.strip()
        
        return text
    
    def render_csv_with_escaping(self, data, renderer_context):
        """
        Renders data as CSV using Python's built-in csv module
        with proper escaping of special characters.
        """
        if not data:
            return ""
            
        # Initialize a string buffer and CSV writer
        csv_buffer = io.StringIO()
        csv_writer = csv.writer(
            csv_buffer,
            quoting=csv.QUOTE_ALL,  # Quote all fields for maximum compatibility
            quotechar='"',
            doublequote=True,  # Double quotes within fields are doubled
            lineterminator='\n'
        )
        
        # Flatten the data structure
        flattened_data = self.flatten_data(data)
        
        # Reorder columns
        header, rows = self._reorder_columns(flattened_data['header'], flattened_data['rows'])
        
        # Final check to ensure all header items are strings
        header = [str(h) for h in header]
        
        
        # Write header row
        csv_writer.writerow(header)
        
        # Write data rows with extra type checking
        for i, row in enumerate(rows):
            # Final check to ensure all row items are strings
            string_row = []
            for item in row:
                if item is None:
                    string_row.append('')
                elif not isinstance(item, str):
                    try:
                        string_row.append(str(item))
                    except Exception as e:
                        # If conversion fails, add error info and empty string
                        string_row.append('')
                else:
                    string_row.append(item)
            try:
                csv_writer.writerow(string_row)
            except Exception as e:
                safe_row = ['' if item is None else str(item) for item in string_row]
                try:
                    csv_writer.writerow(safe_row)
                except Exception as new_e:
                    with open('/tmp/csv_debug.txt', 'a') as f:
                        f.write(f"Still failed with safe row: {str(new_e)}\n")
            
        return csv_buffer.getvalue()
    
    def _reorder_columns(self, header, rows):
        """
        Reorder columns to match the preferred order and exclude unwanted columns.
        Ensures all values are properly converted to strings.
        """
        try:
            # Create a mapping of column name to index
            column_indices = {column: idx for idx, column in enumerate(header)}
            
            # Determine the new order of columns
            new_order = []
            
            # First, add the preferred columns in the specified order (if they exist)
            for col in self.PREFERRED_COLUMN_ORDER:
                if col in column_indices:
                    new_order.append(col)
            
            # Then add any remaining columns, excluding those in EXCLUDED_COLUMNS
            for col in header:
                if col not in new_order and col not in self.EXCLUDED_COLUMNS:
                    # Skip columns that start with excluded prefixes
                    if not any(col.startswith(excluded + '.') for excluded in self.EXCLUDED_COLUMNS):
                        new_order.append(col)
            
            # Create a mapping from old indices to new indices
            index_mapping = [column_indices[col] for col in new_order]
            
            # Helper function to safely convert values to strings
            def safe_str(value):
                if value is None:
                    return ''
                try:
                    return str(value)
                except:
                    return ''
            
            # Reorder each row according to the new column order
            reordered_rows = []
            for row in rows:
                # Get the value at each index, ensure it's a string, or use empty string if index is out of bounds
                reordered_row = []
                for idx in index_mapping:
                    if idx < len(row):
                        reordered_row.append(safe_str(row[idx]))
                    else:
                        reordered_row.append('')
                reordered_rows.append(reordered_row)
            
            return new_order, reordered_rows
        except Exception as e:
            # Return empty lists as a fallback
            return [], []
    
    def flatten_data(self, data):
        """
        Use the parent CSVRenderer's flattening logic but with our customizations.
        Ensures the output is consistently formatted for our custom tablize method.
        """
        # First use the parent class's flatten method
        if hasattr(self, '_flatten_data'):
            try:
                # DRF's internal flattening function if available
                flattened = self._flatten_data(data)
                # Convert to our expected format
                return self.custom_flatten_data(flattened)
            except Exception as e:
                # If the parent method fails, fall back to our custom implementation
                return self.custom_flatten_data(data)
        else:
            # Fallback to our own implementation
            return self.custom_flatten_data(data)
            
    def custom_flatten_data(self, data):
        """
        Custom implementation of data flattening for CSV.
        This handles nested structures and converts them to flat column-based data.
        """
        # Convert data to tabular format
        table = self.tablize(data)
        
        # Extract headers and rows
        if len(table) > 0:
            header = table[0]
            rows = table[1:]
        else:
            header = []
            rows = []
            
        return {
            'header': header,
            'rows': rows
        }
    
    def tablize(self, data):
        """
        Convert the data to a table-like format for CSV.
        Ensures all values are strings to prevent "expected str instance" errors.
        """
        # Helper function to ensure a value is a string
        def ensure_string(value):
            if value is None:
                return ''
            elif isinstance(value, (dict, list)):
                try:
                    return json.dumps(value)
                except:
                    return str(value)
            else:
                try:
                    return str(value)
                except:
                    return ''
            
        # If data is a list, process each item
        if isinstance(data, list):
            # Get all possible field names from all items
            field_names = set()
            for item in data:
                if isinstance(item, dict):
                    field_names.update(self.get_field_names(item))
            
            field_names = sorted(field_names)
            
            # Create the table with headers
            # Convert all field names to strings to be safe
            table = [[ensure_string(field) for field in field_names]]
            
            # Add each item as a row
            for item in data:
                row = []
                for field in field_names:
                    if isinstance(item, dict) and field in item:
                        row.append(ensure_string(item[field]))
                    else:
                        row.append('')
                table.append(row)
                
            return table
        
        # If data is a single item
        elif isinstance(data, dict):
            field_names = self.get_field_names(data)
            # Convert all field names to strings to be safe
            table = [[ensure_string(field) for field in field_names]]
            
            row = []
            for field in field_names:
                if field in data:
                    row.append(ensure_string(data[field]))
                else:
                    row.append('')
            table.append(row)
            
            return table
            
        # If data is a non-dict value, treat as a single cell
        else:
            return [[ensure_string(data)]]
    
    def get_field_names(self, obj, prefix=''):
        """
        Recursively get all field names from a dictionary, 
        including nested dictionaries using dotted notation.
        """
        field_names = []
        
        if isinstance(obj, dict):
            for key, value in obj.items():
                new_prefix = f"{prefix}.{key}" if prefix else key
                
                # If value is a dict or list, recursively get field names
                if isinstance(value, dict):
                    field_names.extend(self.get_field_names(value, new_prefix))
                elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                    # For lists of dicts, use index notation
                    for i, item in enumerate(value):
                        field_names.extend(self.get_field_names(item, f"{new_prefix}.{i}"))
                else:
                    field_names.append(new_prefix)
        
        return field_names
    
    def _remove_excluded_columns(self, data_list):
        """
        Remove columns that should be excluded from the CSV output.
        """
        for item in data_list:
            if not isinstance(item, dict):
                continue
                
            # Remove excluded columns
            for key in list(item.keys()):
                # Case 1: The key exactly matches an excluded column
                # Case 2: The key starts with an excluded column followed by a dot
                #         (this handles nested fields like 'teams.0.id')
                if (key in self.EXCLUDED_COLUMNS or 
                    any(key.startswith(excluded + '.') for excluded in self.EXCLUDED_COLUMNS)):
                    item.pop(key, None)
        
        return data_list

class StreamingCSVRenderer(FlattenedCSVRenderer):
    """
    A streaming CSV renderer that extends FlattenedCSVRenderer but uses Django's
    StreamingHttpResponse to stream results one row at a time, reducing memory usage
    and making it less likely to time out for large datasets.
    
    This renderer is designed to be a drop-in replacement for FlattenedCSVRenderer.
    """
    
    def render(self, data, accepted_media_type=None, renderer_context=None):
        """
        Prepare the data for streaming but return a generator function instead of
        the complete CSV content. The actual StreamingHttpResponse will be created
        in a middleware that wraps the response.
        """
        # Set the custom filename in the response headers if we have a response object
        if renderer_context and 'response' in renderer_context:
            # Determine the object type (articles, trials, etc.)
            object_type = self._determine_object_type(renderer_context)
            current_date = datetime.now().strftime('%Y-%m-%d')
            filename = f"gregory-ai-{object_type}-{current_date}.csv"
            
            # Set Content-Disposition header with the custom filename
            renderer_context['response']['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            # Set a flag on the response to indicate it should be streamed
            renderer_context['response'].streaming = True
            
            # Store the renderer in the context so middleware can access it
            renderer_context['renderer'] = self
        
        # Check if this is paginated data
        if isinstance(data, dict) and 'results' in data and isinstance(data['results'], list):
            # Replace the entire data with just the results
            data = data['results']
        
        # Create the generator function and store it for the middleware to access
        self.generator_function = self.generate_csv_rows(data, renderer_context)
        
        # Return an empty string - the middleware will handle the streaming
        # This prevents the framework from trying to consume the generator
        return ""
    
    def generate_csv_rows(self, data, renderer_context):
        """
        Generator function that yields CSV rows one at a time.
        """
        # Consolidate various fields into single columns
        if isinstance(data, list):
            data = self._consolidate_authors(data)
            data = self._consolidate_subjects(data)
            data = self._consolidate_ml_predictions(data)
            data = self._consolidate_clinical_trials(data)
            data = self._consolidate_team_categories(data)
            data = self._consolidate_sources(data)
            data = self._consolidate_article_subject_relevances(data)
            data = self._remove_excluded_columns(data)
            
        # Clean text fields to ensure proper CSV formatting
        if isinstance(data, list):
            data = self.clean_data_for_csv(data)
        
        # Flatten the data structure
        flattened_data = self.flatten_data(data)
        
        # Reorder columns
        header, rows = self._reorder_columns(flattened_data['header'], flattened_data['rows'])
        
        # Final check to ensure all header items are strings
        header = [str(h) for h in header]
        
        # Create a StringIO buffer for a single row at a time
        csv_buffer = io.StringIO()
        csv_writer = csv.writer(
            csv_buffer,
            quoting=csv.QUOTE_ALL,  # Quote all fields for maximum compatibility
            quotechar='"',
            doublequote=True,  # Double quotes within fields are doubled
            lineterminator='\n'
        )
        
        # Yield the header row first
        csv_writer.writerow(header)
        yield csv_buffer.getvalue()
        
        # Clear the buffer for the next row
        csv_buffer.seek(0)
        csv_buffer.truncate(0)
        
        # Yield each data row one at a time
        for row in rows:
            # Final check to ensure all row items are strings
            string_row = []
            for item in row:
                if item is None:
                    string_row.append('')
                elif not isinstance(item, str):
                    try:
                        string_row.append(str(item))
                    except Exception:
                        string_row.append('')
                else:
                    string_row.append(item)
            
            try:
                csv_writer.writerow(string_row)
                row_data = csv_buffer.getvalue()
                yield row_data
                
                # Clear the buffer for the next row
                csv_buffer.seek(0)
                csv_buffer.truncate(0)
            except Exception:
                # If writing fails, try with a safer approach
                safe_row = ['' if item is None else str(item) for item in string_row]
                try:
                    csv_writer.writerow(safe_row)
                    row_data = csv_buffer.getvalue()
                    yield row_data
                    
                    # Clear the buffer for the next row
                    csv_buffer.seek(0)
                    csv_buffer.truncate(0)
                except Exception:
                    # Skip this row if it still fails
                    csv_buffer.seek(0)
                    csv_buffer.truncate(0)
