from django.utils.timezone import now
from datetime import timedelta
from django.db.models import Q

from api.models import APIAccessScheme, APIAccessSchemeLog
from api.utils.exceptions import APIAccessDeniedError, APIInvalidAPIKeyError, APIInvalidIPAddressError, APINoAPIKeyError


def getAPIKey(request):
    # Get the API Key from the Authorization header
    api_key = request.headers.get("Authorization")
    if api_key:
        if len(api_key.strip()) > 0:
            return api_key.strip()

    # If no API Key was provided, raise error
    raise APINoAPIKeyError('No API Key found')


# Only two ways to retrieve the client's IP address:
# 1. from the HTTP_X_FORWARDED_FOR header
# 2. from the REMOTE_ADDR header
def getIPAddress(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def checkValidAccess(api_key, ip_address):
    # Get the access scheme associated with this API Key
    access_schemes = APIAccessScheme.objects.filter(api_key=api_key)

    # Only expecting one access scheme from this API Key
    if access_schemes and len(access_schemes) == 1:
        access_scheme = access_schemes[0]

        # Check if the IP address is not empty
        if not ip_address:
            raise APIInvalidIPAddressError('Empty IP address')
        
        # Only check IP address if the access scheme's IP addresses are not empty
        if access_scheme.ip_addresses:
            if not (ip_address in [i.strip() for i in access_scheme.ip_addresses.split(',')]):
                raise APIInvalidIPAddressError('Unauthorized IP address')
        
        # Check if the authorization period is valid
        if not (access_scheme.begin_date <= now() <= access_scheme.end_date):
            raise APIAccessDeniedError('Access out of authorized period')

        # Check if call limits have been surpassed
        calls_last_minute = getNumberOfCallsInLastMinute(access_scheme)
        if calls_last_minute >= access_scheme.max_calls_minute:
            raise APIAccessDeniedError('Minute quota limit exceeded')
        calls_last_hour = getNumberOfCallsInLastHour(access_scheme)
        if calls_last_hour >= access_scheme.max_calls_hour:
            raise APIAccessDeniedError('Hourly quota limit exceeded')
        calls_last_day = getNumberOfCallsInLastDay(access_scheme)
        if calls_last_day >= access_scheme.max_calls_day:
            raise APIAccessDeniedError('Daily quota limit exceeded')

        # All good, return the access scheme
        return access_scheme
    else:
        # Raise APIInvalidKeyError if the API Key is not found
        # in the DB
        raise APIInvalidAPIKeyError('Invalid API Key')


# Util function to get the number of calls made by the access scheme in the last minute
def getNumberOfCallsInLastMinute(access_scheme: APIAccessScheme) -> int:
    now_datetime = now()
    return getNumberOfCallsForDateRange(now_datetime - timedelta(seconds=60), now_datetime)

# Util function to get the number of calls made by the access scheme in the last hour
def getNumberOfCallsInLastHour(access_scheme: APIAccessScheme) -> int:
    now_datetime = now()
    return getNumberOfCallsForDateRange(now_datetime - timedelta(minutes=60), now_datetime)

# Util function to get the number of calls made by the access scheme in the last day
def getNumberOfCallsInLastDay(access_scheme: APIAccessScheme) -> int:
    now_datetime = now()
    return getNumberOfCallsForDateRange(now_datetime - timedelta(hours=24), now_datetime)

# Util function to get the number of calls made by the access scheme in a date range
def getNumberOfCallsForDateRange(earlier_date, later_date) -> int:
    return APIAccessSchemeLog.objects.filter(access_date__range=(earlier_date, later_date)).count()


def find_trial_by_identifier(identifiers: dict | None):
    """Return a queryset of Trials matching any identifier key (euct, nct, eudract).

    Caller decides whether to use .first() or .count()/.all() depending on
    whether duplicates should be allowed.  The queryset is unevaluated so the
    caller controls evaluation timing.
    """
    from gregory.models import Trials
    if not identifiers:
        return Trials.objects.none()
    id_query = Q()
    if identifiers.get('euct'):
        id_query |= Q(identifiers__euct=identifiers['euct'])
    if identifiers.get('nct'):
        id_query |= Q(identifiers__nct=identifiers['nct'])
    if identifiers.get('eudract'):
        id_query |= Q(identifiers__eudract=identifiers['eudract'])
    if not id_query:
        return Trials.objects.none()
    return Trials.objects.filter(id_query)