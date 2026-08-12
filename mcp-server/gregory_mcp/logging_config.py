"""Structured (JSON) logging for the Gregory MCP server."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

# 10 MB x 5 backups per file: generous enough that low-traffic instances never
# rotate for weeks, capped so an unattended instance can't fill the disk.
_LOG_MAX_BYTES = 10_000_000
_LOG_BACKUP_COUNT = 5


# Explicit allowlist, not "every extra field": this is the boundary that
# keeps a stray `extra={"search": ...}` elsewhere in the codebase from ever
# reaching the log. See gregory_mcp/telemetry.py and MCP-TELEMETRY-PLAN.md.
_EXTRA_FIELDS = (
	"tool",
	"duration_ms",
	"status_code",
	"path",
	"method",
	"outcome",
	"error_kind",
	"upstream_ms",
	"upstream_calls",
	"result_count",
	"total_count",
	"has_next",
	"params_used",
	"page",
	"page_size",
	"subject_id",
	"team_id",
	"category_slug",
	"category_modality",
	"client_name",
	"client_version",
	"protocol_version",
	"cache",
	"term_count",
	"has_boolean_ops",
	"has_quoted_phrase",
	"length_bucket",
	"matched_category_slugs",
	"unmatched_term_count",
)


def _add_exc_type(payload: dict, record: logging.LogRecord) -> None:
	"""Adds the exception's *class name* only — never a formatted traceback.

	A traceback routinely embeds exactly the kind of caller-controlled text
	this module's two formatters exist to keep out: an httpx transport
	error's message can carry the full request URL (query string and all),
	a ValueError's message echoes whatever made it invalid. Both formatters
	otherwise gate every field through an explicit allowlist for the same
	reason (see _EXTRA_FIELDS/_INTENT_FIELDS above) — exc_info bypassed that
	gate entirely until this function existed. The class name alone (e.g.
	"ConnectError") is diagnostically useful without carrying any of that.
	"""
	if record.exc_info:
		payload["exc_type"] = record.exc_info[0].__name__


class JsonFormatter(logging.Formatter):
	def format(self, record: logging.LogRecord) -> str:
		payload = {
			"ts": round(time.time(), 3),
			"level": record.levelname,
			"logger": record.name,
			"message": record.getMessage(),
		}
		for key in _EXTRA_FIELDS:
			value = getattr(record, key, None)
			if value is not None:
				payload[key] = value
		_add_exc_type(payload, record)
		return json.dumps(payload)


# `intent` (Phase 4 of MCP-TELEMETRY-PLAN.md) gets its own tiny, separate
# allowlist rather than a few more entries on _EXTRA_FIELDS above — so
# nothing from the telemetry stream's field set (duration_ms, upstream_ms,
# a request id, ...) can end up on an intent log line even by accident, and
# so an intent-only field can never end up on a telemetry line either. Two
# independent boundaries, not one shared one.
INTENT_LOGGER_NAME = "gregory_mcp.intent"
_INTENT_FIELDS = ("tool", "intent", "pii_flags")


class IntentJsonFormatter(logging.Formatter):
	def format(self, record: logging.LogRecord) -> str:
		payload = {
			"ts": round(time.time(), 3),
			"level": record.levelname,
			"logger": record.name,
			"message": record.getMessage(),
		}
		for key in _INTENT_FIELDS:
			value = getattr(record, key, None)
			if value is not None:
				payload[key] = value
		_add_exc_type(payload, record)
		return json.dumps(payload)


def configure_logging(level: str, log_dir: str | None = None) -> None:
	handler = logging.StreamHandler(sys.stdout)
	handler.setFormatter(JsonFormatter())
	root = logging.getLogger()
	root.handlers = [handler]
	root.setLevel(level)

	# `intent` goes to stderr, not stdout — a distinct OS-level stream, not
	# just a distinct logger name, so it can be routed and retained
	# separately downstream (see the plan's "separation is protection").
	# propagate=False keeps it off the root handler above entirely: an
	# intent event is written once, to this stream only. Retention (90-day
	# hard delete) and actual downstream routing are deploy-side — Phase 6,
	# same boundary as the rest of this file's rotation/retention story.
	intent_logger = logging.getLogger(INTENT_LOGGER_NAME)
	intent_handler = logging.StreamHandler(sys.stderr)
	intent_handler.setFormatter(IntentJsonFormatter())
	intent_logger.handlers = [intent_handler]
	intent_logger.propagate = False
	intent_logger.setLevel(level)

	# Optional on-disk mirror of both streams, additive to stdout/stderr
	# above rather than a replacement — `docker logs` keeps working exactly
	# as before whether or not this is configured. Unset in local dev; set
	# via MCP_LOG_DIR in production so the files land on a mounted volume
	# and survive container restarts.
	if log_dir:
		os.makedirs(log_dir, exist_ok=True)

		telemetry_file_handler = RotatingFileHandler(
			os.path.join(log_dir, "telemetry.log"),
			maxBytes=_LOG_MAX_BYTES,
			backupCount=_LOG_BACKUP_COUNT,
		)
		telemetry_file_handler.setFormatter(JsonFormatter())
		root.handlers.append(telemetry_file_handler)

		intent_file_handler = RotatingFileHandler(
			os.path.join(log_dir, "intent.log"),
			maxBytes=_LOG_MAX_BYTES,
			backupCount=_LOG_BACKUP_COUNT,
		)
		intent_file_handler.setFormatter(IntentJsonFormatter())
		intent_logger.handlers.append(intent_file_handler)
