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
