"""Logs and metrics (22.4, review F16): what an operator's tools read, instead of prints.

Logs are JSON lines on stderr (`LORE_LOG_FORMAT=text` for people): time, level, component,
message, the request id when there is one, and any structured fields. The API logs one line per
request (method, path, status, ms, who) and returns the id as `X-Request-ID` (a caller's own
id is kept when it is a plain token). Paths only, never query strings or bodies: no question
text reaches a log. Docker rotates the logs (docker-compose.yml), so what they hold of a person
ages out with them.

Metrics are Prometheus text at `/metrics` (main.py). The API counts what it serves; the
connector worker is another process, so it writes its counters onto the heartbeat it already
writes each round, and the API reads them at scrape time with the graph's memory, today's
spend, dead letters and per-tool backoff. No client library: the text format is a few lines.
"""

import json
import logging
import sys
import threading
import time
from contextvars import ContextVar
from typing import Any

from app.config import get_settings

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
log = logging.getLogger("yaan")
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "component": record.name,
            "msg": record.getMessage(),
        }
        if rid := request_id.get():
            body["request_id"] = rid
        body.update(getattr(record, "fields", None) or {})
        if record.exc_info:
            body["error"] = self.formatException(record.exc_info)
        return json.dumps(body, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """Once per process: the `yaan` loggers and uvicorn's own go to stderr in one format;
    uvicorn's access log is off, the API writes its own (with request ids)."""
    global _configured
    if _configured:
        return
    _configured = True
    handler = logging.StreamHandler(sys.stderr)
    if get_settings().lore_log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error"):
        other = logging.getLogger(name)
        other.handlers = [handler]
        other.propagate = False
    logging.getLogger("uvicorn.access").disabled = True


def event(component: str, msg: str, level: int = logging.INFO, **fields: Any) -> None:
    """One log line from `yaan.<component>`, with structured fields."""
    logging.getLogger(f"yaan.{component}").log(level, msg, extra={"fields": fields})


def say(component: str):
    """A `say` for code that narrates (the connector loop): each line a log record."""
    return lambda text: event(component, str(text))


# --- metrics ---------------------------------------------------------------------------------
HELP = {
    "yaan_http_requests_total": ("counter", "API requests answered, by route, method and status"),
    "yaan_http_request_seconds": ("summary", "time to answer API requests, by route"),
}


class Metrics:
    """Counters and summaries (sum + count) kept in this process; thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def inc(self, name: str, value: float = 1, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._values[key] = self._values.get(key, 0) + value

    def observe(self, name: str, value: float, **labels: str) -> None:
        self.inc(f"{name}_sum", value, **labels)
        self.inc(f"{name}_count", 1, **labels)

    def samples(self) -> list[tuple[str, dict[str, str], float]]:
        with self._lock:
            return [(n, dict(labels), v) for (n, labels), v in sorted(self._values.items())]


METRICS = Metrics()


def render(samples: list[tuple[str, dict[str, str], float]], help_: dict | None = None) -> str:
    """Prometheus text exposition (version 0.0.4): HELP and TYPE once per family."""
    help_ = HELP | (help_ or {})

    def family_of(name: str) -> str:
        return next((f for f in help_ if name == f or name in (f"{f}_sum", f"{f}_count")), name)

    out: list[str] = []
    said: set[str] = set()
    # a family's samples must be contiguous: sort by family, keep each family's own order
    for name, labels, value in sorted(samples, key=lambda s: family_of(s[0])):
        family = family_of(name)
        if family not in said:
            said.add(family)
            kind, text = help_.get(family, ("gauge", ""))
            out += [f"# HELP {family} {text}", f"# TYPE {family} {kind}"]
        inner = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
        out.append(f"{name}{{{inner}}} {_number(value)}" if inner else f"{name} {_number(value)}")
    return "\n".join(out) + "\n"


def _escape(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))
