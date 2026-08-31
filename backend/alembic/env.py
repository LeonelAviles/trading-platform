import os
import sys
from logging.config import fileConfig

from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from database import Base  # noqa: E402
import models  # noqa: E402,F401 — import registers all mapped classes on Base

config = context.config

if config.config_file_name is not None and not config.attributes.get("connection_engine"):
    fileConfig(config.config_file_name)

# Same DATABASE_URL the app uses (env var, default data/platform.db) unless
# database.init_db() handed us an engine. alembic.ini carries no URL.
if not config.attributes.get("connection_engine"):
    config.set_main_option("sqlalchemy.url", database.DATABASE_URL.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection_engine") or database.make_engine(
        config.get_main_option("sqlalchemy.url")
    )
    with connectable.connect() as connection:
        # render_as_batch: SQLite cannot ALTER most things in place; batch
        # mode rewrites the table for future migrations.
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
