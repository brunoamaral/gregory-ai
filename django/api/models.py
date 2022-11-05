from django.db import models
from django.utils.crypto import get_random_string
from django.utils.timezone import now
from datetime import timedelta

DEFAULT_MAX_CALLS_MINUTE = 60
DEFAULT_MAX_CALLS_HOUR = DEFAULT_MAX_CALLS_MINUTE * 60
DEFAULT_MAX_CALLS_DAY = DEFAULT_MAX_CALLS_HOUR * 24

# Util function to generate the API Key randomly
def generateRandomAPIKey():
    return get_random_string(length=64)

# Util function to generate a date one year from today
def oneYearFromToday():
    return now() + timedelta(days=365)


# This model describes the access scheme for a client to connect to the API
class APIAccessScheme(models.Model):

    # The API Key to be used by this client
    api_key = models.CharField(max_length=64, unique=True, blank=False, default=generateRandomAPIKey)

    # A short name to identify the client
    client_name = models.CharField(max_length=200, unique=False, blank=False, default='Client Name')

    # A list of contacts (e-mail, phone, etc.) for the client (separated by comma) to be used
    # in case of changes in the API that need to be notified
    client_contacts = models.CharField(max_length=200, unique=False, blank=False, default='client@contact.com')

    # The list of IP addresses (separated by comma) that are authorized to use this API Key
    # If empty, any IP address can access the service with this API Key
    ip_addresses = models.CharField(max_length=500, blank=True, unique=False)

    # The date of the beginning of the authorized access
    # (default: now)
    begin_date = models.DateTimeField(default=now, blank=False)

    # The date of the end of the authorized access
    # (default: 1 year from now)
    end_date = models.DateTimeField(default=oneYearFromToday, blank=False)

    # Maximum number of calls permitted per minute
    max_calls_minute = models.IntegerField(default=DEFAULT_MAX_CALLS_MINUTE, blank=False)

    # Maximum number of calls permitted per hour
    max_calls_hour = models.IntegerField(default=DEFAULT_MAX_CALLS_HOUR, blank=False)

    # Maximum number of calls permitted per day
    max_calls_day = models.IntegerField(default=DEFAULT_MAX_CALLS_DAY, blank=False)

    def __str__(self):
        return self.client_name


# This model describes the clients' access logs to the models
class APIAccessSchemeLog(models.Model):

    # A text describing the kind of call made
    call_type = models.CharField(max_length=200, blank=False, null=False, default="GET /")

    # The IP address of the client call
    ip_addr = models.CharField(max_length=20, blank=True, null=True)

    # The API Access Scheme that this log refers to
    api_access_scheme = models.ForeignKey(APIAccessScheme, on_delete=models.CASCADE, blank=True, null=True)

    # The date of the access log
    access_date = models.DateTimeField(blank=False, default=now)

    # The HTTP code returned (if any)
    http_code = models.IntegerField(blank=True)

    # In case of error, this field holds the error text
    error_message = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return "[" + self.access_date.strftime("%Y-%m-%d %H:%M:%S") + "] " + self.call_type + " (from " + self.ip_addr + "): " + str(self.http_code)
