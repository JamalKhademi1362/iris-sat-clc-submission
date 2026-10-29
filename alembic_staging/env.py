from logging.config import fileConfig
import os
from pathlib import Path

from dotenv import load_dotenv
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy import pool


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

project_root = Path(config.config_file_name).resolve().parent if config.config_file_name else Path.cwd()
load_dotenv(project_root / ".env.clc.local")

database_url = os.getenv("CLC_DATABASE_URL")
if not database_url:
    raise RuntimeError("CLC_DATABASE_URL is required for the standalone staging migration")

config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="iris_staging",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS iris_staging")
        context.configure(
            connection=connection,
            target_metadata=None,
            version_table_schema="iris_staging",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
