from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import models so every mapped table is registered for migration autogeneration.
import models  # noqa: E402,F401


def get_engine():
    return current_app.extensions['migrate'].db.engine


def get_metadata():
    return current_app.extensions['migrate'].db.metadata


def run_migrations_offline():
    context.configure(
        url=str(get_engine().url),
        target_metadata=get_metadata(),
        literal_binds=True,
        compare_type=True,
        dialect_opts={'paramstyle': 'named'},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            compare_type=True,
            render_as_batch=connection.dialect.name == 'sqlite',
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
