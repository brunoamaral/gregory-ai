from django.http import StreamingHttpResponse

class StreamingCSVMiddleware:
    """
    Middleware that converts regular responses to StreamingHttpResponse when they
    contain streaming CSV content.
    
    This middleware detects when a response has been flagged for streaming by the 
    StreamingCSVRenderer and converts it to a StreamingHttpResponse.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
    def __call__(self, request):
        # Process the request
        response = self.get_response(request)
        
        # Check if this response should be streamed
        if hasattr(response, 'streaming') and response.streaming:
            # The rendered_content attribute contains the generator from the renderer
            # We should not access response.content directly as that would consume the generator
            
            # Check if the content has already been generated
            if hasattr(response, 'renderer_context') and response.renderer_context.get('renderer'):
                renderer = response.renderer_context['renderer']
                # We need to get the original generator function from the render method
                if hasattr(renderer, 'generator_function'):
                    streaming_content = renderer.generator_function
                else:
                    # If there's no stored generator function, create a new one
                    streaming_content = renderer.generate_csv_rows(
                        response.data, 
                        response.renderer_context
                    )
                
                # Create a streaming response with the generator function
                streaming_response = StreamingHttpResponse(
                    streaming_content=streaming_content,
                    content_type=response['Content-Type']
                )
                
                # Copy all headers from the original response
                for header, value in response.items():
                    streaming_response[header] = value
                    
                return streaming_response
            
        return response
