"""Schema migrations with Alembic, driven from Python so containers and the CLI need no ini file."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from tinystudio.config import config

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def alembic_config(database_url: str | None = None) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", (database_url or config().database_url).replace("%", "%%"))
    cfg.set_main_option("file_template", "%%(rev)s_%%(slug)s")
    return cfg


def upgrade(revision: str = "head", database_url: str | None = None) -> None:
    """Apply every pending migration (run at API start and by ``tinystudio db upgrade``)."""
    command.upgrade(alembic_config(database_url), revision)


def downgrade(revision: str, database_url: str | None = None) -> None:
    command.downgrade(alembic_config(database_url), revision)


def revision(message: str, autogenerate: bool = True, database_url: str | None = None) -> None:
    """Create a migration from the difference between the models and the database (development only)."""
    command.revision(alembic_config(database_url), message=message, autogenerate=autogenerate)


def current(database_url: str | None = None) -> None:
    command.current(alembic_config(database_url), verbose=True)
