"""GET /healthz/: the probe behind the gregory container's Docker healthcheck.

django/healthcheck.py requests this from inside the container, against the
gunicorn that is already serving traffic. It replaced `manage.py check`,
which started a whole new Django process every 30 seconds; on House that
took about 10 seconds of CPU each time, ran past the 10-second healthcheck
timeout and left the container marked unhealthy while it served fine.

200 means gunicorn answered and the database took a query. 503 means
gunicorn answered but the database did not. The body says which, and
nothing else: the route is reachable from outside, so it reveals no
versions, hosts or error details.
"""

from django.db import DatabaseError, connection
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

CONTENT_TYPE = "text/plain; charset=utf-8"


@never_cache
@require_safe
def healthz(request):
	try:
		with connection.cursor() as cursor:
			cursor.execute("SELECT 1")
	except DatabaseError:
		return HttpResponse(
			"database unavailable\n", status=503, content_type=CONTENT_TYPE
		)
	return HttpResponse("ok\n", content_type=CONTENT_TYPE)
