# SPDX-License-Identifier: AGPL-3.0-or-later
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .config import DATABASE_URL
from .models import Base, Bridge

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _migrate_bridges_columns(sync_conn):
    """Lazy ALTER TABLE for columns added after the table was first created.

    SQLAlchemy's create_all() does NOT add new columns to existing tables, so
    we add them ourselves on startup. SQLite supports ADD COLUMN with default.
    """
    inspector = inspect(sync_conn)
    if "bridges" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("bridges")}
    expected = {col.name: col for col in Bridge.__table__.columns}
    for name, col in expected.items():
        if name in existing:
            continue
        # Build minimal ALTER TABLE for SQLite
        sql_type = col.type.compile(dialect=sync_conn.dialect)
        default = ""
        if col.default is not None and col.default.arg is not None and not callable(col.default.arg):
            val = col.default.arg
            default = f" DEFAULT {val!r}" if isinstance(val, str) else f" DEFAULT {val}"
        sync_conn.execute(text(f'ALTER TABLE bridges ADD COLUMN {name} {sql_type}{default}'))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_bridges_columns)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
