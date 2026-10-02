import asyncio

from sqlalchemy import func, select

from app.db import Session
from app.models import Run


async def test_health_reports_db_and_redis(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok", "redis": "ok"}


async def test_same_idempotency_key_returns_same_run(client):
    body = {"task": "summarize https://example.com", "idempotency_key": "key-1"}
    first = await client.post("/runs", json=body)
    second = await client.post("/runs", json=body)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["status"] == "queued"
    assert second.json()["id"] == first.json()["id"]

    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(Run)) == 1


async def test_concurrent_posts_with_same_key_create_one_run(client):
    body = {"task": "t", "idempotency_key": "key-race"}
    responses = await asyncio.gather(*(client.post("/runs", json=body) for _ in range(5)))

    assert len({r.json()["id"] for r in responses}) == 1
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]


async def test_get_unknown_run_is_404(client):
    resp = await client.get("/runs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
