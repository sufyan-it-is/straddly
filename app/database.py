"""
app/database.py
===============
asyncpg connection pool — single pool shared across all modules.
Call `init_db()` in FastAPI lifespan startup.
"""
import asyncpg
import logging
from pathlib import Path
from app.config import get_settings

log = logging.getLogger(__name__)

# Module-level pool — initialised once, used everywhere
_pool: asyncpg.Pool | None = None


async def init_db() -> None:
    """Create the connection pool and run migration if needed."""
    global _pool
    cfg = get_settings()
    log.info("Connecting to PostgreSQL…")
    _pool = await asyncpg.create_pool(
        dsn=cfg.database_url,
        min_size=5,
        max_size=20,
        command_timeout=600,
    )
    await _run_migrations()
    log.info("PostgreSQL pool ready.")


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        log.info("PostgreSQL pool closed.")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised. Call init_db() first.")
    return _pool


async def _run_migrations() -> None:
    """Apply all SQL files in migrations/ in filename order (idempotent)."""
    migrations_dir = Path(__file__).parent.parent / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))
    async with _pool.acquire() as conn:
        for sql_file in sql_files:
            log.info(f"Applying migration: {sql_file.name}")
            sql = sql_file.read_text(encoding="utf-8")
            try:
                await conn.execute(sql)
            except (asyncpg.exceptions.DuplicateTableError, asyncpg.exceptions.DuplicateObjectError) as exc:
                log.warning("Skipping migration %s due to existing objects: %s", sql_file.name, exc)
                continue
    log.info(f"Migrations complete ({len(sql_files)} file(s)).")
