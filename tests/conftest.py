import os

# Tests get their own database; this has to be set before app modules are imported.
ADMIN_URL = os.environ["DATABASE_URL"]
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/runner_test"
os.environ["DATABASE_URL"] = TEST_URL
# Separate Redis DB so a running dev worker never sees jobs enqueued by tests.
os.environ["REDIS_URL"] = os.environ["REDIS_URL"].rsplit("/", 1)[0] + "/1"

import subprocess

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
async def test_database():
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = 'runner_test'"))
        if not exists:
            await conn.execute(text("CREATE DATABASE runner_test"))
    await admin.dispose()

    # Build the schema from the real migration so the migration itself is under test.
    test = create_async_engine(TEST_URL, isolation_level="AUTOCOMMIT")
    async with test.connect() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await test.dispose()
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    yield


@pytest.fixture(autouse=True)
async def clean_tables():
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE runs, run_steps, outbox RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
