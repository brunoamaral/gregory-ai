from __future__ import annotations

import json
import logging
import sys

from gregory_mcp.logging_config import IntentJsonFormatter, JsonFormatter


def _make_record_with_exc_info(logger_name: str, exc_message: str) -> logging.LogRecord:
	logger = logging.getLogger(logger_name)
	try:
		raise ValueError(exc_message)
	except ValueError:
		return logger.makeRecord(logger.name, logging.WARNING, __file__, 0, "something_failed", (), sys.exc_info())


def test_json_formatter_logs_exception_type_never_a_traceback():
	record = _make_record_with_exc_info("gregory_mcp.client", "GET https://gregory.test/articles/?search=SENSITIVE-marker failed")

	payload = json.loads(JsonFormatter().format(record))

	assert payload["exc_type"] == "ValueError"
	assert "exc_info" not in payload
	assert "SENSITIVE-marker" not in json.dumps(payload)
	assert "Traceback" not in json.dumps(payload)


def test_intent_json_formatter_logs_exception_type_never_a_traceback():
	record = _make_record_with_exc_info("gregory_mcp.intent", "taxonomy fetch failed for SENSITIVE-intent-text")

	payload = json.loads(IntentJsonFormatter().format(record))

	assert payload["exc_type"] == "ValueError"
	assert "exc_info" not in payload
	assert "SENSITIVE-intent-text" not in json.dumps(payload)


def test_json_formatter_omits_exc_type_when_there_is_no_exception():
	record = logging.getLogger("gregory_mcp.telemetry").makeRecord(
		"gregory_mcp.telemetry", logging.INFO, __file__, 0, "mcp_request", (), None
	)
	payload = json.loads(JsonFormatter().format(record))
	assert "exc_type" not in payload
