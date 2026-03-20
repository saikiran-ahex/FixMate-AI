from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator
from uuid import uuid4


request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_var.get()),
        }

        event = getattr(record, "event", None)
        if event:
            payload["event"] = event

        extra = getattr(record, "extra_data", None)
        if isinstance(extra, dict):
            payload.update(extra)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    if root_logger.handlers:
        for handler in root_logger.handlers:
            handler.setFormatter(JsonFormatter())
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def set_request_id(request_id: str | None = None) -> str:
    value = request_id or str(uuid4())
    request_id_var.set(value)
    return value


def get_request_id() -> str:
    return request_id_var.get()


def log_event(logger: logging.Logger, level: int, event: str, **data: Any) -> None:
    logger.log(level, event, extra={"event": event, "extra_data": data, "request_id": get_request_id()})


@contextmanager
def log_timing(logger: logging.Logger, event: str, **data: Any) -> Iterator[None]:
    start = time.perf_counter()
    log_event(logger, logging.INFO, f"{event}_started", **data)
    try:
        yield
    except Exception:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(logger, logging.ERROR, f"{event}_failed", duration_ms=duration_ms, **data)
        raise
    else:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(logger, logging.INFO, f"{event}_completed", duration_ms=duration_ms, **data)
