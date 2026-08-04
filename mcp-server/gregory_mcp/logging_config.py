"""Structured (JSON) logging for the Gregory MCP server."""

from __future__ import annotations

import json
import logging
import sys
import time


class JsonFormatter(logging.Formatter):
	def format(self, record: logging.LogRecord) -> str:
		payload = {
			"ts": round(time.time(), 3),
			"level": record.levelname,
			"logger": record.name,
			"message": record.getMessage(),
		}
		for key in ("tool", "duration_ms", "status_code", "path"):
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
