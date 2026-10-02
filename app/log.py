import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "run_id": run_id_var.get(),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # uvicorn and arq install their own plain-text handlers; route them through ours
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "arq", "arq.worker"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
