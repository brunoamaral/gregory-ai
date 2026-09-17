from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from gregory_mcp.logging_config import (
	INTENT_LOGGER_NAME,
	IntentJsonFormatter,
	JsonFormatter,
	configure_logging,
)


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


def test_json_formatter_includes_site_id_when_resolved():
	record = logging.getLogger("gregory_mcp.telemetry").makeRecord(
		"gregory_mcp.telemetry", logging.INFO, __file__, 0, "mcp_request", (), None, extra={"site_id": 5}
	)
	payload = json.loads(JsonFormatter().format(record))
	assert payload["site_id"] == 5


def test_json_formatter_includes_null_site_id_when_unresolved():
	# telemetry.py always sets this key, even to None -- the formatter must
	# not treat that the same as "never set" (see _add_site_id).
	record = logging.getLogger("gregory_mcp.telemetry").makeRecord(
		"gregory_mcp.telemetry", logging.INFO, __file__, 0, "mcp_request", (), None, extra={"site_id": None}
	)
	payload = json.loads(JsonFormatter().format(record))
	assert "site_id" in payload
	assert payload["site_id"] is None


def test_json_formatter_omits_site_id_when_the_event_never_set_it():
	# A log line from elsewhere in the codebase (a retry warning, a cache-dir
	# failure, ...) never mentions site_id at all -- must stay omitted, not
	# turn into a misleading `"site_id": null` implying "no site resolved"
	# for an event that was never about a site in the first place.
	record = logging.getLogger("gregory_mcp.client").makeRecord(
		"gregory_mcp.client", logging.WARNING, __file__, 0, "gregory_api_retry", (), None
	)
	payload = json.loads(JsonFormatter().format(record))
	assert "site_id" not in payload


def test_intent_json_formatter_includes_site_id_when_resolved():
	record = logging.getLogger(INTENT_LOGGER_NAME).makeRecord(
		INTENT_LOGGER_NAME, logging.INFO, __file__, 0, "mcp_intent", (), None, extra={"site_id": 9}
	)
	payload = json.loads(IntentJsonFormatter().format(record))
	assert payload["site_id"] == 9


def test_intent_json_formatter_includes_null_site_id_when_unresolved():
	record = logging.getLogger(INTENT_LOGGER_NAME).makeRecord(
		INTENT_LOGGER_NAME, logging.INFO, __file__, 0, "mcp_intent", (), None, extra={"site_id": None}
	)
	payload = json.loads(IntentJsonFormatter().format(record))
	assert "site_id" in payload
	assert payload["site_id"] is None


def test_configure_logging_without_log_dir_only_attaches_stream_handlers():
	configure_logging("INFO")

	root_handlers = logging.getLogger().handlers
	intent_handlers = logging.getLogger(INTENT_LOGGER_NAME).handlers

	assert len(root_handlers) == 1 and isinstance(root_handlers[0], logging.StreamHandler)
	assert not isinstance(root_handlers[0], RotatingFileHandler)
	assert len(intent_handlers) == 1 and isinstance(intent_handlers[0], logging.StreamHandler)
	assert not isinstance(intent_handlers[0], RotatingFileHandler)


def test_configure_logging_with_log_dir_writes_rotated_files(tmp_path):
	configure_logging("INFO", log_dir=str(tmp_path))

	logging.getLogger("gregory_mcp.telemetry").info("mcp_request", extra={"tool": "search_articles"})
	logging.getLogger(INTENT_LOGGER_NAME).info("mcp_intent", extra={"tool": "search_articles", "intent": "test"})

	telemetry_path = tmp_path / "telemetry.log"
	intent_path = tmp_path / "intent.log"
	assert telemetry_path.exists()
	assert intent_path.exists()

	telemetry_payload = json.loads(telemetry_path.read_text().splitlines()[-1])
	assert telemetry_payload["tool"] == "search_articles"

	intent_payload = json.loads(intent_path.read_text().splitlines()[-1])
	assert intent_payload["intent"] == "test"

	# Additive, not a replacement: the stdout/stderr handlers are still there.
	root_handler_types = [type(h) for h in logging.getLogger().handlers]
	intent_handler_types = [type(h) for h in logging.getLogger(INTENT_LOGGER_NAME).handlers]
	assert root_handler_types.count(logging.StreamHandler) == 1
	assert root_handler_types.count(RotatingFileHandler) == 1
	assert intent_handler_types.count(logging.StreamHandler) == 1
	assert intent_handler_types.count(RotatingFileHandler) == 1


def test_configure_logging_writes_site_id_to_disk(tmp_path):
	"""The requirement is site_id in the JSON *lines written to disk* --
	exercise the real RotatingFileHandler + formatter pipeline end to end,
	not just the formatter in isolation, the way
	test_configure_logging_with_log_dir_writes_rotated_files already does
	for `tool`/`intent`.
	"""
	configure_logging("INFO", log_dir=str(tmp_path))

	logging.getLogger("gregory_mcp.telemetry").info(
		"mcp_request", extra={"tool": "search_articles", "site_id": 7}
	)
	logging.getLogger(INTENT_LOGGER_NAME).info(
		"mcp_intent", extra={"tool": "search_articles", "intent": "test", "site_id": None}
	)

	telemetry_payload = json.loads((tmp_path / "telemetry.log").read_text().splitlines()[-1])
	assert telemetry_payload["site_id"] == 7

	intent_payload = json.loads((tmp_path / "intent.log").read_text().splitlines()[-1])
	assert "site_id" in intent_payload
	assert intent_payload["site_id"] is None


def test_configure_logging_falls_back_when_log_dir_is_unwritable(tmp_path):
	# A regular file where a directory is expected makes os.makedirs(...,
	# exist_ok=True) raise FileExistsError (an OSError subclass) — the same
	# failure shape as a bind-mounted host directory the container's
	# non-root user can't create/write into.
	blocked_path = tmp_path / "not-a-directory"
	blocked_path.write_text("occupied")

	configure_logging("INFO", log_dir=str(blocked_path))

	root_handlers = logging.getLogger().handlers
	intent_handlers = logging.getLogger(INTENT_LOGGER_NAME).handlers
	assert len(root_handlers) == 1 and isinstance(root_handlers[0], logging.StreamHandler)
	assert not isinstance(root_handlers[0], RotatingFileHandler)
	assert len(intent_handlers) == 1 and isinstance(intent_handlers[0], logging.StreamHandler)
	assert not isinstance(intent_handlers[0], RotatingFileHandler)
