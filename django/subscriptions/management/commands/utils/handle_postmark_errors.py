from subscriptions.models import FailedNotification

# Postmark error codes that indicate the recipient address is permanently undeliverable.
# 406: Inactive recipient (hard bounce, spam complaint, or manual suppression)
DEACTIVATION_ERROR_CODES = {406}


def parse_postmark_error(result):
	"""
	Parse a Postmark API response and extract error details.
	Returns (error_code, error_message, error_details_str) or None if successful.
	"""
	if result is None:
		return (None, None, "No response from Postmark API")

	if result.status_code == 200:
		response_data = result.json()
		error_code = response_data.get("ErrorCode", 0)
		message = response_data.get("Message", "Unknown error")
		if error_code == 0:
			return None  # Success
		return (error_code, message, f"ErrorCode: {error_code}, Message: {message}")

	# Non-200 status codes
	error_details = f"HTTP Status {result.status_code}"
	error_code = None
	error_message = None

	if result.status_code == 422:
		try:
			error_response = result.json()
			error_code = error_response.get("ErrorCode")
			error_message = error_response.get("Message", "No details provided")
			error_details = f"422 Unprocessable Entity - ErrorCode: {error_code}, Message: {error_message}"
		except (ValueError, KeyError):
			error_details = "422 Unprocessable Entity - Unable to parse error details"

	return (error_code, error_message, error_details)


def deactivate_subscriber_from_list(subscriber, lst, error_code, error_message, stdout=None):
	"""
	Remove a subscriber from a specific list due to a Postmark delivery failure.
	Uses simple_history to record the reason for the change.
	If the subscriber has no remaining subscriptions, sets active=False.
	"""
	reason = f"Auto-removed from list '{lst.list_name}': Postmark ErrorCode {error_code} - {error_message}"

	subscriber.subscriptions.remove(lst)
	subscriber._change_reason = reason
	subscriber.save()

	if stdout:
		stdout.write(stdout.style.WARNING(
			f"Subscriber {subscriber.email} removed from list '{lst.list_name}' due to Postmark error {error_code}."
		))

	# If no subscriptions remain, deactivate globally
	if subscriber.subscriptions.count() == 0:
		subscriber.active = False
		subscriber._change_reason = f"Auto-deactivated: no remaining subscriptions after Postmark ErrorCode {error_code}"
		subscriber.save()
		if stdout:
			stdout.write(stdout.style.WARNING(
				f"Subscriber {subscriber.email} deactivated (no remaining subscriptions)."
			))


def handle_postmark_error(result, subscriber, lst, stdout=None):
	"""
	Handle a Postmark API response for a failed email send.
	Parses the error, records a FailedNotification, and auto-deactivates
	the subscriber from the list if the error code is in DEACTIVATION_ERROR_CODES.

	Returns a dict with was_error, was_deactivated, error_details,
	or None if the response indicates success.
	"""
	parsed = parse_postmark_error(result)
	if parsed is None:
		return None  # Success

	error_code, error_message, error_details = parsed

	FailedNotification.objects.create(
		subscriber=subscriber,
		list=lst,
		reason=error_details
	)

	if stdout:
		stdout.write(stdout.style.ERROR(
			f"Failed to send email to {subscriber.email} for list '{lst.list_name}'. {error_details}"
		))

	was_deactivated = False
	if error_code is not None and error_code in DEACTIVATION_ERROR_CODES:
		deactivate_subscriber_from_list(subscriber, lst, error_code, error_message, stdout=stdout)
		was_deactivated = True

	return {
		'was_error': True,
		'was_deactivated': was_deactivated,
		'error_details': error_details,
	}
