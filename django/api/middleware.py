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
            # Get the content
            content = response.content
            
            # Create a streaming response
            streaming_response = StreamingHttpResponse(
                content,
                content_type=response['Content-Type']
            )
            
            # Copy all headers from the original response
            for header, value in response.items():
                streaming_response[header] = value
                
            return streaming_response
            
        return response
