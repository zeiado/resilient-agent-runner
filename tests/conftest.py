import os

# Tests get their own database; this has to be set before app modules are imported.
ADMIN_URL = os.environ["DATABASE_URL"]
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/runner_test"
os.environ["DATABASE_URL"] = TEST_URL

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.main import app


@pytest.fixture(scope="session", autouse=True)
async def test_database():
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = 'runner_test'"))
        if not exists:
            await conn.execute(text("CREATE DATABASE runner_test"))
    await admin.dispose()
    yield


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
