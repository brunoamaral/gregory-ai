
from django.http.response import JsonResponse

# Set of possible error-based responses

UNEXPECTED = 0
NO_API_KEY = 1
ACCESS_DENIED = 2
INVALID_API_KEY = 3
INVALID_API_KEY = 4
INVALID_IP_ADDRESS = 5

ERRORS = {
    UNEXPECTED: 'Unexpected error. Please contact the support team.',
    NO_API_KEY: 'No API Key was provided.',
    ACCESS_DENIED: 'The client does not have access to the requested resource.',
    INVALID_API_KEY: 'The API Key that was provided is invalid.',
    INVALID_IP_ADDRESS: 'The client\'s IP address is not authorized for the provided API Key.',
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
