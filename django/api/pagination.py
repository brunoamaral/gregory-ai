from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

class FlexiblePagination(PageNumberPagination):
    """
    A flexible pagination class that can handle pagination parameters 
    from both query strings (GET requests) and request data (POST requests).
    """
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100
    
    def get_page_size(self, request):
        """
        Get the page size from either query parameters or request data.
        """
        if request.method == 'POST' and 'page_size' in request.data:
            try:
                return min(int(request.data['page_size']), self.max_page_size)
            except (KeyError, ValueError, TypeError):
                pass
        
        return super().get_page_size(request)
    
    def get_page_number(self, request, paginator):
        """
        Get the page number from either query parameters or request data.
        """
        page_number = request.query_params.get(self.page_query_param, 1)
        
        if request.method == 'POST' and 'page' in request.data:
            try:
                page_number = request.data['page']
            except (KeyError, ValueError, TypeError):
                pass
        
        return page_number
    
    def get_paginated_response(self, data):
        """
        Enhance the paginated response with additional metadata.
        """
        return Response({
            'count': self.page.paginator.count,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'current_page': self.page.number,
            'total_pages': self.page.paginator.num_pages,
            'page_size': self.get_page_size(self.request),
            'results': data
        })
