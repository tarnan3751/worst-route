"""Request ids and the JSON logs (`docs/reference/api.md`, **Logs**).

`RequestLog` is the API's outermost middleware of its own: it gives every HTTP request an id,
16 hex digits (or, behind a trusted reverse proxy, the `X-Request-ID` the proxy sent, when it is
at most 64 letters, digits, dots, dashes and underscores), answers it as `X-Request-ID`, keeps it
in `request_id` while the request is served (tasks started meanwhile inherit it; a job keeps
the id of the request that submitted it, `api/jobs.py`) and in the scope (`request_id_of`, for
the app's handler of an unhandled error, which answers outside this middleware), and when the
response has ended logs one line to `worst_route.access`: the method,
the path without its query (a place search's text stays out of the logs), the status, the
duration and the hashed client (`limits.client_hash`), never its address.

`serve --log-format json` logs through `json_log_config()`: every record of uvicorn and of the
package as one JSON object a line on stdout (`JsonFormatter`: `ts`, `level`, `logger`,
`message`, the record's own fields such as `request_id`, `job_id`, `client`, `status`, and `exc`
with a traceback), the request's id added by `ContextFilter`, uvicorn's access log replaced by
`worst_route.access`. The text format is uvicorn's, as before, without the access lines of
`worst_route.access`.
"""

import json
import logging
import re
import secrets
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from worst_route.api.limits import client_hash, client_of

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
access = logging.getLogger("worst_route.access")

_GIVEN_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SCOPE_KEY = "worst_route.request_id"
# A record's own attributes, which are not fields of its line (uvicorn adds `color_message`).
_RECORD = {*vars(logging.makeLogRecord({})), "message", "asctime", "color_message"}


class JsonFormatter(logging.Formatter):
    """A record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        at = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        line: dict[str, Any] = {
            "ts": at.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        line.update((k, v) for k, v in vars(record).items() if k not in _RECORD)
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


class ContextFilter(logging.Filter):
    """Adds the id of the request being served to a record that has none."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id.get()
        if rid is not None and not hasattr(record, "request_id"):
            record.request_id = rid
        return True


class RequestLog:
    """The request id and the access line of every HTTP request."""

    def __init__(self, app: ASGIApp, *, trust_proxy: bool = False) -> None:
        self.app = app
        self.trust_proxy = trust_proxy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        given = Headers(scope=scope).get("x-request-id")
        if self.trust_proxy and given is not None and _GIVEN_ID.fullmatch(given):
            rid = given
        else:
            rid = secrets.token_hex(8)
        token = request_id.set(rid)
        scope[_SCOPE_KEY] = rid
        status = 500  # unless a response starts: an exception the app did not answer
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message).append("X-Request-ID", rid)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            method, path = scope["method"], scope["path"]
            fields = {
                "request_id": rid,
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": duration_ms,
                "client": client_hash(client_of(scope, self.trust_proxy)),
            }
            access.info("%s %s %d %.1f ms", method, path, status, duration_ms, extra=fields)
            request_id.reset(token)


def request_id_of(scope: Scope) -> str | None:
    """The id `RequestLog` gave the request `scope` describes."""
    rid = scope.get(_SCOPE_KEY)
    return rid if isinstance(rid, str) else None


def json_log_config() -> dict[str, Any]:
    """The logging configuration (`logging.config.dictConfig`) of `serve --log-format json`."""
    handler = {
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stdout",
        "formatter": "json",
        "filters": ["context"],
    }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter}},
        "filters": {"context": {"()": ContextFilter}},
        "handlers": {"json": handler},
        "loggers": {
            "uvicorn": {"handlers": ["json"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
            "worst_route": {"handlers": ["json"], "level": "INFO", "propagate": False},
        },
    }
