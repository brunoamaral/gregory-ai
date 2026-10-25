"""Docker healthcheck probe for the gregory container.

docker-compose.yaml runs this inside the container as
`python -I /code/healthcheck.py`. It asks the running gunicorn for /healthz/
(admin/health.py) and exits 0 on a 200, 1 on anything else: another status,
a refused connection or a timeout.

Standard library only, and run with -I so PYTHONPATH=/code and the script's
own folder are ignored: the probe must start in milliseconds and must not
import Django or any app module. Starting Django is what made the old
`manage.py check` probe too slow.
"""

import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000/healthz/"

# Below the 5s healthcheck timeout in docker-compose.yaml, so a slow
# answer is reported here rather than killed by Docker with no message.
TIMEOUT_SECONDS = 4


def main() -> int:
	try:
		with urllib.request.urlopen(URL, timeout=TIMEOUT_SECONDS) as response:
			status = response.status
	except urllib.error.HTTPError as exc:
		status = exc.code
	except OSError as exc:
		# URLError (connection refused) and timeouts are both OSErrors.
		print(f"unhealthy: {exc}")  # noqa: T201 -- Docker keeps it in the health log
		return 1
	if status != 200:
		print(f"unhealthy: HTTP {status}")  # noqa: T201
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
