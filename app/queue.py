from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app import config

redis_settings = RedisSettings.from_dsn(config.REDIS_URL)
_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings)
    return _pool


async def enqueue_run(run_id) -> None:
    pool = await get_pool()
    await pool.enqueue_job("execute_run", str(run_id))
