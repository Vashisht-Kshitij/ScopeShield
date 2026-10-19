"""Use the application's URL or its supplied connection without logging secrets."""
from alembic import context

from app.database import Base, build_engine, database_url
import app.storage  # Register ORM metadata for future autogeneration.

config = context.config


def migrate(connection):
    context.configure(
        connection=connection, target_metadata=Base.metadata,
        render_as_batch=connection.dialect.name == "sqlite", compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        migrate(supplied)
    else:
        engine = build_engine(database_url())
        try:
            with engine.connect() as connection:
                migrate(connection)
        finally:
            engine.dispose()
