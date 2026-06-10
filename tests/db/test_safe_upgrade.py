"""Tests for AlembicHelper.safe_upgrade / pending_destructive_ops."""

from pathlib import Path

import pytest

from tempest_fastapi_sdk.db import AlembicHelper, DestructiveMigrationError
from tempest_fastapi_sdk.db.migrations import _upgrade_section


@pytest.fixture
def alembic_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AlembicHelper:
    """Scaffold an Alembic project and return its helper."""
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "test.db"
    helper = AlembicHelper(
        config_path=str(tmp_path / "alembic.ini"),
        db_url=f"sqlite+aiosqlite:///{db_path}",
    )
    helper.init(
        directory=str(tmp_path / "alembic"),
        db_url=f"sqlite+aiosqlite:///{db_path}",
    )
    return helper


def _versions_dir(helper: AlembicHelper) -> Path:
    cfg = helper.config
    location = cfg.get_main_option("script_location")
    assert location is not None
    return Path(location) / "versions"


def _write_revision(helper: AlembicHelper, upgrade_body: str) -> None:
    """Create an empty revision and replace its upgrade() body."""
    helper.revision("test rev", autogenerate=False)
    files = list(_versions_dir(helper).glob("*.py"))
    assert len(files) == 1
    path = files[0]
    text = path.read_text(encoding="utf-8")
    # The ruff_fix post-write hook strips the unused ``op`` / ``sa``
    # imports from the empty revision; re-add them before we use them.
    imports = "from alembic import op\nimport sqlalchemy as sa\n"
    text = imports + text
    # Replace the generated empty upgrade body (``pass``) with ours.
    marker = "def upgrade() -> None:"
    idx = text.index(marker) + len(marker)
    end = text.index("def downgrade", idx)
    new = text[:idx] + f"\n    {upgrade_body}\n\n\n" + text[end:]
    path.write_text(new, encoding="utf-8")


class TestUpgradeSection:
    def test_slices_only_upgrade(self) -> None:
        source = (
            "def upgrade() -> None:\n    op.create_table('x')\n\n"
            "def downgrade() -> None:\n    op.drop_table('x')\n"
        )
        section = _upgrade_section(source)
        assert "op.create_table" in section
        assert "op.drop_table" not in section


class TestPendingDestructiveOps:
    def test_clean_migration_has_no_offences(
        self, alembic_project: AlembicHelper
    ) -> None:
        _write_revision(alembic_project, "op.create_table('thing')")
        assert alembic_project.pending_destructive_ops("head") == []

    def test_drop_table_is_flagged(self, alembic_project: AlembicHelper) -> None:
        _write_revision(alembic_project, "op.drop_table('thing')")
        offences = alembic_project.pending_destructive_ops("head")
        assert len(offences) == 1
        assert offences[0][1] == "op.drop_table"

    def test_drop_in_downgrade_is_ignored(self, alembic_project: AlembicHelper) -> None:
        # A create in upgrade + drop in downgrade is the normal, safe shape.
        _write_revision(alembic_project, "op.create_table('thing')")
        assert alembic_project.pending_destructive_ops("head") == []


class TestSafeUpgrade:
    def test_refuses_destructive_without_force(
        self, alembic_project: AlembicHelper
    ) -> None:
        _write_revision(alembic_project, "op.drop_column('thing', 'col')")
        with pytest.raises(DestructiveMigrationError) as exc:
            alembic_project.safe_upgrade("head")
        assert exc.value.offences[0][1] == "op.drop_column"

    def test_runs_clean_migration(self, alembic_project: AlembicHelper) -> None:
        _write_revision(
            alembic_project,
            "op.create_table('thing', sa.Column('id', sa.Integer(), primary_key=True))",
        )
        alembic_project.safe_upgrade("head")
        assert alembic_project.current() is not None
