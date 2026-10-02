import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import Session
from app.log import setup_logging
from app.queue import get_pool

setup_logging()
log = logging.getLogger("api")

app = FastAPI(title="resilient-agent-runner")


@app.get("/health")
async def health():
    checks = {}
    try:
        async with Session() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
    try:
        await (await get_pool()).ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        log.error("health check failed: %s", checks)
    return JSONResponse({"status": "ok" if healthy else "unhealthy", **checks}, status_code=200 if healthy else 503)
