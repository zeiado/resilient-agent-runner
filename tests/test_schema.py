from sqlalchemy import text

from app.db import engine
from app.models import Base


async def test_migration_matches_models():
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'")
        )
        migrated = {(t, c) for t, c in rows if t != "alembic_version"}
    declared = {(t.name, c.name) for t in Base.metadata.tables.values() for c in t.columns}
    assert migrated == declared
