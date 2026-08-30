"""Tests for the first-boot schema bootstrap: adopt / sync_schema.

The defect these exist for shipped in a service using this SDK and took a
day of downtime: a database created with ``create_all`` before the project
had migrations was stamped at ``head`` on boot, so Alembic recorded every
revision as applied while none had run. ``TestTheReportedDefect`` reproduces
that state; the rest pin the surface that makes it unreachable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tempest_fastapi_sdk.db import (
    AlembicHelper,
    AmbiguousBaseRevisionError,
    DestructiveMigrationError,
    SchemaSyncOutcome,
)

BASELINE: str = (
    "op.create_table('messages', "
    "sa.Column('id', sa.Integer(), primary_key=True), "
    "sa.Column('body', sa.String(), nullable=False))"
)
"""The revision that creates the table, as a first boot would."""

ADD_COLUMN: str = (
    "op.add_column('messages', sa.Column('edited_at', sa.DateTime(), nullable=True))"
)
"""The revision whose column the broken bootstrap silently skipped."""

LEGACY_SCHEMA: str = (
    "CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
)
"""The same table as :data:`BASELINE`, created without Alembic."""


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return the SQLite file the helper is pointed at.

    Args:
        tmp_path (Path): pytest's per-test directory.

    Returns:
        Path: The database file, which does not exist yet.
    """
    return tmp_path / "service.db"


@pytest.fixture
def helper(
    tmp_path: Path,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AlembicHelper:
    """Scaffold an empty Alembic project and return its helper.

    Args:
        tmp_path (Path): pytest's per-test directory.
        db_path (Path): Where the SQLite database will live.
        monkeypatch (pytest.MonkeyPatch): Used to run inside ``tmp_path``.

    Returns:
        AlembicHelper: A helper over a tree with no revisions yet.
    """
    monkeypatch.chdir(tmp_path)
    url = f"sqlite+aiosqlite:///{db_path}"
    built = AlembicHelper(config_path=str(tmp_path / "alembic.ini"), db_url=url)
    built.init(directory=str(tmp_path / "alembic"), db_url=url)
    return built


def _versions_dir(helper: AlembicHelper) -> Path:
    """Locate the ``versions/`` directory of a scaffolded project.

    Args:
        helper (AlembicHelper): The helper under test.

    Returns:
        Path: The directory revision files are written to.
    """
    location = helper.config.get_main_option("script_location")
    assert location is not None
    return Path(location) / "versions"


def add_revision(helper: AlembicHelper, message: str, body: str) -> None:
    """Append a revision whose ``upgrade()`` runs ``body``.

    Args:
        helper (AlembicHelper): The helper under test.
        message (str): The revision message.
        body (str): A single statement for the upgrade path.
    """
    before = {p.name for p in _versions_dir(helper).glob("*.py")}
    helper.revision(message, autogenerate=False)
    created = [p for p in _versions_dir(helper).glob("*.py") if p.name not in before]
    assert len(created) == 1
    path = created[0]
    text = "from alembic import op\nimport sqlalchemy as sa\n" + path.read_text(
        encoding="utf-8"
    )
    marker = "def upgrade() -> None:"
    idx = text.index(marker) + len(marker)
    end = text.index("def downgrade", idx)
    path.write_text(text[:idx] + f"\n    {body}\n\n\n" + text[end:], encoding="utf-8")


def columns(db_path: Path, table: str) -> set[str]:
    """Read a table's column names straight from SQLite.

    Args:
        db_path (Path): The database file.
        table (str): The table to inspect.

    Returns:
        set[str]: Column names, empty when the table does not exist.
    """
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        connection.close()
    return {row[1] for row in rows}


def write_legacy_schema(db_path: Path) -> None:
    """Create the application table the way a pre-Alembic project did.

    Args:
        db_path (Path): The database file to create it in.
    """
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(LEGACY_SCHEMA)
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def two_revisions(helper: AlembicHelper) -> AlembicHelper:
    """Return the helper with a baseline and one follow-up revision.

    Args:
        helper (AlembicHelper): The empty scaffolded project.

    Returns:
        AlembicHelper: The same helper, now with two revisions.
    """
    add_revision(helper, "baseline", BASELINE)
    add_revision(helper, "add edited_at", ADD_COLUMN)
    return helper


class TestTheReportedDefect:
    """The state that took the service down, reproduced end to end."""

    def test_stamping_head_on_a_pre_alembic_schema_hides_the_gap(
        self,
        two_revisions: AlembicHelper,
        db_path: Path,
    ) -> None:
        """Alembic reports ``head`` while the column is missing."""
        write_legacy_schema(db_path)
        two_revisions.stamp("head")

        assert two_revisions.current() == two_revisions.heads()[0]
        assert "edited_at" not in columns(db_path, "messages")

        two_revisions.safe_upgrade()

        assert "edited_at" not in columns(db_path, "messages")

    def test_sync_schema_reaches_the_state_the_defect_missed(
        self,
        two_revisions: AlembicHelper,
        db_path: Path,
    ) -> None:
        """The same starting point, through the SDK, ends up correct."""
        write_legacy_schema(db_path)

        assert two_revisions.sync_schema() is SchemaSyncOutcome.ADOPTED

        assert "edited_at" in columns(db_path, "messages")
        assert two_revisions.current() == two_revisions.heads()[0]


class TestRepairingAWrongStamp:
    """Getting out of the state, once a service is already in it."""

    def test_stamp_base_then_sync_recovers_the_missing_column(
        self,
        two_revisions: AlembicHelper,
        db_path: Path,
    ) -> None:
        """``"base"`` clears the pointer, and the sync re-adopts properly."""
        write_legacy_schema(db_path)
        two_revisions.stamp("head")
        assert "edited_at" not in columns(db_path, "messages")

        two_revisions.stamp("base")
        assert two_revisions.current() is None

        assert two_revisions.sync_schema() is SchemaSyncOutcome.ADOPTED
        assert "edited_at" in columns(db_path, "messages")
        assert two_revisions.current() == two_revisions.heads()[0]


class TestSyncSchema:
    """One entry point, three starting states."""

    def test_empty_database_runs_the_whole_tree(
        self,
        two_revisions: AlembicHelper,
        db_path: Path,
    ) -> None:
        """No stamp is written; the baseline creates the tables."""
        assert two_revisions.sync_schema() is SchemaSyncOutcome.SYNCED
        assert columns(db_path, "messages") == {"id", "body", "edited_at"}

    def test_already_stamped_database_only_upgrades(
        self,
        two_revisions: AlembicHelper,
        db_path: Path,
    ) -> None:
        """A second call after a new revision takes the ordinary path."""
        two_revisions.sync_schema()
        add_revision(
            two_revisions,
            "add pinned",
            "op.add_column('messages', "
            "sa.Column('pinned', sa.Boolean(), nullable=True))",
        )

        assert two_revisions.sync_schema() is SchemaSyncOutcome.SYNCED
        assert "pinned" in columns(db_path, "messages")

    def test_project_without_revisions_leaves_the_database_alone(
        self,
        helper: AlembicHelper,
        db_path: Path,
    ) -> None:
        """Nothing to apply is a reported outcome, not a crash."""
        write_legacy_schema(db_path)

        assert helper.sync_schema() is SchemaSyncOutcome.NO_MIGRATIONS
        assert helper.current() is None

    def test_destructive_migration_is_still_refused(
        self,
        two_revisions: AlembicHelper,
    ) -> None:
        """Wrapping ``safe_upgrade`` does not weaken its guard."""
        two_revisions.sync_schema()
        add_revision(two_revisions, "drop body", "op.drop_column('messages', 'body')")

        with pytest.raises(DestructiveMigrationError):
            two_revisions.sync_schema()

    def test_force_passes_through(
        self,
        two_revisions: AlembicHelper,
        db_path: Path,
    ) -> None:
        """``force=True`` reaches ``safe_upgrade``."""
        two_revisions.sync_schema()
        add_revision(two_revisions, "drop body", "op.drop_column('messages', 'body')")

        assert two_revisions.sync_schema(force=True) is SchemaSyncOutcome.SYNCED
        assert "body" not in columns(db_path, "messages")


class TestAdopt:
    """Adoption stamps the base, and only when adoption is what is needed."""

    def test_pre_alembic_schema_is_stamped_at_base_not_head(
        self,
        two_revisions: AlembicHelper,
        db_path: Path,
    ) -> None:
        """The stamp records the baseline only, leaving the rest pending."""
        write_legacy_schema(db_path)

        assert two_revisions.adopt() is True
        assert two_revisions.current() == two_revisions.base_revision()
        assert two_revisions.current() != two_revisions.heads()[0]
        assert "edited_at" not in columns(db_path, "messages")

    def test_empty_database_is_not_adopted(
        self,
        two_revisions: AlembicHelper,
    ) -> None:
        """Stamping an empty database would skip the work of the baseline."""
        assert two_revisions.adopt() is False
        assert two_revisions.current() is None

    def test_already_stamped_database_is_not_adopted(
        self,
        two_revisions: AlembicHelper,
    ) -> None:
        """There is nothing to adopt once Alembic owns the database."""
        two_revisions.sync_schema()

        assert two_revisions.adopt() is False
        assert two_revisions.current() == two_revisions.heads()[0]


class TestBaseRevision:
    """``head`` is the wrong answer for adoption; this is the right one."""

    def test_single_root_is_returned(
        self,
        two_revisions: AlembicHelper,
    ) -> None:
        """The root is the first revision, not the last."""
        base = two_revisions.base_revision()
        assert base != two_revisions.heads()[0]
        assert two_revisions.show(base).splitlines()[1] == "Parent: None"

    def test_tree_without_revisions_raises(
        self,
        helper: AlembicHelper,
    ) -> None:
        """A project with no revisions has nothing to stamp."""
        with pytest.raises(AmbiguousBaseRevisionError) as exc:
            helper.base_revision()
        assert exc.value.bases == []


class TestHasExistingSchema:
    """The question a hand-rolled bootstrap forgets to ask."""

    def test_empty_database_has_no_schema(
        self,
        two_revisions: AlembicHelper,
    ) -> None:
        """A file that does not exist yet holds no tables."""
        assert two_revisions.has_existing_schema() is False

    def test_alembic_version_alone_does_not_count(
        self,
        two_revisions: AlembicHelper,
    ) -> None:
        """Alembic writes that table itself, so it proves nothing."""
        two_revisions.stamp(two_revisions.base_revision())

        assert two_revisions.current() is not None
        assert two_revisions.has_existing_schema() is False

    def test_application_table_counts(
        self,
        two_revisions: AlembicHelper,
        db_path: Path,
    ) -> None:
        """One non-Alembic table is enough to require adoption."""
        write_legacy_schema(db_path)

        assert two_revisions.has_existing_schema() is True
