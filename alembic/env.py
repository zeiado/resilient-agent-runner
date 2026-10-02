import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app import config
from app.models import Base


def do_migrations(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run():
    engine = create_async_engine(config.DATABASE_URL)
    async with engine.connect() as connection:
        await connection.run_sync(do_migrations)
    await engine.dispose()


asyncio.run(run())
