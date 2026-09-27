from __future__ import annotations
from sqlmodel import SQLModel, create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import sessionmaker
from app.config import get_settings

settings = get_settings()

# Async engine for FastAPI
async_engine = create_async_engine(settings.database_url, echo=False, future=True)

# Sync engine for Alembic / Celery tasks
sync_engine = create_engine(settings.sync_database_url, echo=False)


def get_sync_engine():
    """Accessor for the sync engine, used by the Celery task modules."""
    return sync_engine

AsyncSessionLocal = sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:  # type: ignore[override]
    async with AsyncSessionLocal() as session:
        yield session


async def create_db_and_tables() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # Idempotent migration: add owner_id to repos if not present
        await conn.execute(text(
            "ALTER TABLE repos ADD COLUMN IF NOT EXISTS owner_id VARCHAR REFERENCES users(id) ON DELETE SET NULL"
        ))
        # create_all() creates missing TABLES but never adds a column to an
        # existing one, so every new column needs a line here. Same
        # idempotent pattern as owner_id above. This is a stopgap that suits
        # a fast-moving build; if this outlives the demo, replace the whole
        # block with Alembic before the data matters.
        await conn.execute(text(
            "ALTER TABLE repos ADD COLUMN IF NOT EXISTS workspace_id VARCHAR REFERENCES workspaces(id) ON DELETE CASCADE"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_repos_workspace_id ON repos (workspace_id)"
        ))
        await conn.execute(text(
            "ALTER TABLE repos ADD COLUMN IF NOT EXISTS ingest_seconds DOUBLE PRECISION"
        ))
        # Postgres enum types are created once by create_all and NEVER
        # altered by it, so adding a value to a Python Enum leaves the DB
        # type behind — inserts then fail with
        # `invalid input value for enum workspacerole: "VIEWER"`.
        # SQLAlchemy persists enum NAMES, hence the uppercase literal.
        # (PG allows ADD VALUE inside a transaction as long as the new value
        # is not also USED in that same transaction — it isn't, this runs at
        # startup.)
        await conn.execute(text(
            "ALTER TYPE workspacerole ADD VALUE IF NOT EXISTS 'VIEWER'"
        ))

        # Connections can belong to the whole workspace or to one person
        # (see core/workspace.py). Existing rows predate the distinction and
        # were all workspace-wide, which is the correct backfill.
        await conn.execute(text(
            "ALTER TABLE github_installations ADD COLUMN IF NOT EXISTS scope VARCHAR NOT NULL DEFAULT 'workspace'"
        ))
        await conn.execute(text(
            "ALTER TABLE github_installations ADD COLUMN IF NOT EXISTS owner_user_id VARCHAR REFERENCES users(id) ON DELETE CASCADE"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_slack_channels_workspace ON slack_channels (workspace_id)"
        ))
        # Call configuration added after `meetings` already existed. JSONB
        # rather than JSON: it is what Postgres actually wants for these,
        # and the columns are new so there is nothing to convert.
        await conn.execute(text(
            "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS bot_types JSONB DEFAULT '[\"support\"]'::jsonb"
        ))
        await conn.execute(text(
            "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS language_mode VARCHAR DEFAULT 'english'"
        ))
        await conn.execute(text(
            "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS enabled_sources JSONB"
        ))
        await conn.execute(text(
            "ALTER TABLE whisper_sessions ADD COLUMN IF NOT EXISTS webhook_secret VARCHAR"
        ))
        # Who a call's agent attends for — see Meeting.attends_as. Existing
        # calls were the member's own, which is the correct backfill.
        await conn.execute(text(
            "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS attends_as VARCHAR NOT NULL DEFAULT 'member'"
        ))
        # Whisper as a call mode; every existing call was a speaking one.
        await conn.execute(text(
            "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS mode VARCHAR NOT NULL DEFAULT 'speak'"
        ))
        await conn.execute(text(
            "ALTER TABLE whisper_sessions ADD COLUMN IF NOT EXISTS bot_status VARCHAR"
        ))
        await conn.execute(text(
            "ALTER TABLE whisper_sessions ADD COLUMN IF NOT EXISTS bot_status_at TIMESTAMP"
        ))
        await conn.execute(text(
            "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS represents_user_id VARCHAR REFERENCES users(id) ON DELETE SET NULL"
        ))
        # Company-level agent: off until an owner turns it on.
        await conn.execute(text(
            "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS org_agent_enabled BOOLEAN NOT NULL DEFAULT false"
        ))
        await conn.execute(text(
            "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS org_agent_sources JSON"
        ))
        # Sessions created before the column existed have no secret, so their
        # webhook can never authenticate. Filled rather than left null: a null
        # would compare equal to a missing path segment in a careless check.
        await conn.execute(text(
            "UPDATE whisper_sessions SET webhook_secret = md5(random()::text || id) "
            "WHERE webhook_secret IS NULL"
        ))

        # A DATA migration, not a schema one, and the only one here.
        #
        # enabled_sources is a frozen snapshot of the source toggles as they
        # stood when a call was set up, so a source group added LATER can
        # never appear in it — and null (meaning "use the defaults") is not
        # what these rows hold. The effect was found live through /dev/ask:
        # every call created before the past_calls group existed had
        # cross-call memory silently switched off, with the agent honestly
        # reporting it had no access to call history on a workspace that had
        # plenty.
        #
        # Appending is right specifically because the group is default-on and
        # was never OFFERED to these calls: nobody disabled it, it did not
        # exist. That reasoning does NOT generalise to an opt-in source, and
        # a stored list still cannot distinguish "turned off" from "did not
        # exist yet" — see TODOS.md's known defects for the general case.
        # On a FRESH database create_all makes these two columns plain JSON
        # (the model says Column(JSON)); the ADD COLUMN lines above only made
        # them JSONB on databases that predate them. The update below needs
        # JSONB operators, so a fresh install failed to start at all.
        # Converted once, only when still JSON.
        await conn.execute(text("""
            DO $$
            DECLARE col text;
            BEGIN
              FOREACH col IN ARRAY ARRAY['bot_types', 'enabled_sources'] LOOP
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'meetings' AND column_name = col) = 'json' THEN
                  EXECUTE format('ALTER TABLE meetings ALTER COLUMN %I TYPE JSONB USING %I::jsonb', col, col);
                END IF;
              END LOOP;
            END $$;
        """))
        await conn.execute(text(
            "UPDATE meetings SET enabled_sources = enabled_sources || '[\"past_calls\"]'::jsonb "
            "WHERE enabled_sources IS NOT NULL "
            "AND NOT (enabled_sources @> '[\"past_calls\"]'::jsonb)"
        ))
        # Existing workspaces predate the distinction. Backfilled as
        # individual rather than team: a workspace nobody was ever invited
        # to IS an individual one, and defaulting the other way would
        # silently label every existing workspace as shared.
        await conn.execute(text(
            "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS kind VARCHAR NOT NULL DEFAULT 'INDIVIDUAL'"
        ))
        await conn.execute(text(
            "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS agent_name VARCHAR"
        ))
        # GitHub App support: OAuth login linking (users.github_id/login,
        # password now optional) and per-repo installation tagging (for the
        # picker's already-imported diff and installation-token cloning).
        await conn.execute(text(
            "ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS github_id VARCHAR UNIQUE"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS github_login VARCHAR"
        ))
        await conn.execute(text(
            "ALTER TABLE repos ADD COLUMN IF NOT EXISTS github_repo_id INTEGER"
        ))
        await conn.execute(text(
            "ALTER TABLE repos ADD COLUMN IF NOT EXISTS github_installation_id INTEGER"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_repos_github_repo_id ON repos (github_repo_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_repos_github_installation_id ON repos (github_installation_id)"
        ))
        # The dashboard's "Mock" button (routers/mock.py) — fictional
        # "Adventa" content, never a real connection.
        await conn.execute(text(
            "ALTER TABLE repos ADD COLUMN IF NOT EXISTS is_mock BOOLEAN NOT NULL DEFAULT false"
        ))
