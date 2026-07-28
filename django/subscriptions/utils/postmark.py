"""
Normalise Postmark send responses so the three send commands stop carrying
three divergent copies of the same parsing logic.

Postmark returns HTTP 422 with ErrorCode 406 for hard-bounced,
spam-complained, or manually suppressed recipients — see
subscriptions.utils.suppression for what happens to that signal.
"""

POSTMARK_INACTIVE_RECIPIENT = 406


def classify_postmark_response(result):
	"""
	Normalise a Postmark send result into (delivered, error_code, detail).

	Accepts a requests.Response, a plain dict (test doubles, shaped like
	{"status_code": ..., "ErrorCode": ..., "Message": ...}), or None.

	IMPORTANT: never test `result` (or a requests.Response) for truthiness —
	requests.Response.__bool__ is `self.ok`, so a 422/500 response is falsy
	and `if result:` would wrongly treat a real error response as "no
	response". Always check `result is None` instead.
	"""
	if result is None:
		return False, None, "No response from Postmark"

	if isinstance(result, dict):
		status_code = result.get("status_code", 200)
		body = result
	else:
		status_code = result.status_code
		try:
			body = result.json()
		except ValueError:
			return False, None, f"HTTP {status_code} - unable to parse error details"

	error_code = body.get("ErrorCode")
	message = body.get("Message", "Unknown error")

	if status_code == 200:
		if not error_code:  # 0 or missing => success
			return True, 0, message
		return False, error_code, message

	detail = (
		f"HTTP {status_code} - ErrorCode: "
		f"{error_code if error_code is not None else 'Unknown'}, Message: {message}"
	)
	return False, error_code, detail
