import unittest
from django.test import TestCase
from unittest.mock import patch, MagicMock
from gregory.unpaywall import unpaywall_utils
import requests

class UnpaywallUtilsTests(TestCase):
    
    @patch('requests.request')
    def test_getDataByDOI_with_404_response(self, mock_request):
        # Setup mock response with 404 status code
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_request.return_value = mock_response
        
        # Call the function with test DOI
        result = unpaywall_utils.getDataByDOI("10.1186/s13287-025-04457-5", "test@example.com")
        
        # Assert the function handled the 404 gracefully
        self.assertEqual(result, {})
        mock_request.assert_called_once()
    
    @patch('requests.request')
    def test_getDataByDOI_with_request_exception(self, mock_request):
        # Setup mock to raise an exception
        mock_request.side_effect = requests.exceptions.RequestException("Connection error")
        
        # Call the function
        result = unpaywall_utils.getDataByDOI("10.1186/s13287-025-04457-5", "test@example.com")
        
        # Assert the function handled the exception gracefully
        self.assertEqual(result, {})
        mock_request.assert_called_once()
    
    @patch('requests.request')
    def test_getDataByDOI_with_valid_response(self, mock_request):
        # Setup mock with valid JSON response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"is_oa": true, "best_oa_location": {"url": "https://example.com/paper.pdf"}}'
        mock_request.return_value = mock_response
        
        # Call the function
        result = unpaywall_utils.getDataByDOI("10.1186/valid-doi", "test@example.com")
        
        # Assert the function parsed the JSON correctly
        self.assertEqual(result["is_oa"], True)
        self.assertEqual(result["best_oa_location"]["url"], "https://example.com/paper.pdf")
        mock_request.assert_called_once()

if __name__ == '__main__':
    unittest.main()
