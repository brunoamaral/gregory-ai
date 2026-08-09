"""Structured (JSON) logging for the Gregory MCP server."""

from __future__ import annotations

import json
import logging
import sys
import time


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
)


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
		if record.exc_info:
			payload["exc_info"] = self.formatException(record.exc_info)
		return json.dumps(payload)


def configure_logging(level: str) -> None:
	handler = logging.StreamHandler(sys.stdout)
	handler.setFormatter(JsonFormatter())
	root = logging.getLogger()
	root.handlers = [handler]
	root.setLevel(level)
