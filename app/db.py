from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import config

engine = create_async_engine(config.DATABASE_URL, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)
