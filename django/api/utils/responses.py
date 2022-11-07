
from django.http.response import JsonResponse

# Set of possible error-based responses

UNEXPECTED = 0
NO_API_KEY = 1
ACCESS_DENIED = 2
INVALID_API_KEY = 3
INVALID_IP_ADDRESS = 4
SOURCE_NOT_FOUND = 5
FIELD_NOT_FOUND = 6
ARTICLE_EXISTS = 7
ARTICLE_NOT_SAVED = 8

ERRORS = {
    UNEXPECTED: 'Unexpected error. Please contact the support team.',
    NO_API_KEY: 'No API Key was provided.',
    ACCESS_DENIED: 'The client does not have access to the requested resource.',
    INVALID_API_KEY: 'The API Key that was provided is invalid.',
    INVALID_IP_ADDRESS: 'The client\'s IP address is not authorized for the provided API Key.',
    SOURCE_NOT_FOUND: 'The specified source_id did not return any configured source',
    FIELD_NOT_FOUND: 'One or more fields wasn\'t found in the payload',
    ARTICLE_EXISTS: 'An article already exists with one of these fields: title, doi.',
    ARTICLE_NOT_SAVED: 'Could not save the article that was received',
}

def returnData(data):
    return JsonResponse(data)

def returnErrorCode(data, status_error_code):
    return returnError(UNEXPECTED, data, status_error_code)

def returnError(code, extra_data='', status_error_code=500):
    data = {
        'code': code,
        'error_msg': ERRORS[code],
        'extra_data': extra_data,
    }
    return JsonResponse(data, status=status_error_code)
