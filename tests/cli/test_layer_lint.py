"""Tests for the ``services`` / ``repositories`` layer rules (#520).

Each rule is exercised with a source tree written to a temporary
directory, in the shape that shipped in the service the issue describes,
and with the near-misses it must leave alone. The scaffold is checked as
``tempest new`` writes it, and again with the ``CLAUDE.md`` examples
copied into it — the template's own advice has to pass its own rules.
"""

from __future__ import annotations

import inspect
import io
import re
import textwrap
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli import layer_lint
from tempest_fastapi_sdk.cli.layer_lint import (
    BASE_REPOSITORY_METHODS,
    NO_BASE_SERVICE_MARKER,
    LayerFinding,
    lint_layers,
    run_lint_layers,
    scan_roots,
)
from tempest_fastapi_sdk.cli.main import LINT_LAYERS_STEP
from tempest_fastapi_sdk.cli.main import app as cli_app
from tempest_fastapi_sdk.db.repository import BaseRepository

runner = CliRunner()

_BLOCK = re.compile(r"```python\n(# (src/[\w/]+\.py)\n.*?)```", re.DOTALL)


def _write(root: Path, relative: str, source: str) -> Path:
    """Write a dedented source file under ``root``.

    Args:
        root (Path): The project root.
        relative (str): Path of the file below ``root``.
        source (str): The file content (dedented before writing).

    Returns:
        Path: The written file.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def _codes(findings: list[LayerFinding]) -> list[tuple[str, int, str]]:
    """Reduce findings to ``(file name, line, code)`` triples.

    Args:
        findings (list[LayerFinding]): The checker output.

    Returns:
        list[tuple[str, int, str]]: One triple per finding, in order.
    """
    return [(finding.path.name, finding.line, finding.code) for finding in findings]


GOOD_SERVICE = """
    from tempest_fastapi_sdk import BaseService

    from src.db.repositories import ProductRepository
    from src.schemas import ProductResponseSchema


    class ProductService(BaseService[ProductRepository, ProductResponseSchema]):
        \"\"\"Business rules for products.\"\"\"

        async def taken(self, name: str) -> bool:
            \"\"\"Tell whether a name is registered.\"\"\"
            return await self.repository.exists({"name": name})
"""


class TestServiceInheritsBaseService:
    """``SVC001``: every public class in ``services/`` is a ``BaseService``."""

    def test_flags_the_hand_rolled_service(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/news.py",
            """
            class NewsService:
                \"\"\"News, rebuilt by hand.\"\"\"

                async def list(self) -> list[int]:
                    \"\"\"Every item.\"\"\"
                    return []
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [("news.py", 1, "SVC001")]
        assert "BaseService[Repository, ResponseSchema]" in findings[0].message
        assert NO_BASE_SERVICE_MARKER in findings[0].message

    def test_accepts_base_service_subscripted_or_qualified(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "src/services/product.py", GOOD_SERVICE)
        _write(
            tmp_path,
            "src/services/order.py",
            """
            import tempest_fastapi_sdk as sdk


            class OrderService(sdk.BaseService):
                \"\"\"Orders.\"\"\"
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_accepts_a_subclass_of_a_project_service(self, tmp_path: Path) -> None:
        """Resolution reaches a fixed point whatever the file order."""
        _write(
            tmp_path,
            "src/services/a_admin_product.py",
            """
            from src.services.product import ProductService


            class AdminProductService(ProductService):
                \"\"\"Products, with the admin rules.\"\"\"
            """,
        )
        _write(tmp_path, "src/services/product.py", GOOD_SERVICE)
        assert lint_layers(tmp_path) == []

    def test_accepts_an_sdk_service_base(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/auth.py",
            """
            from tempest_fastapi_sdk.auth import UserAuthService as SdkAuth


            class AuthService(SdkAuth):
                \"\"\"Signup and login, extended.\"\"\"
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_a_service_named_class_from_elsewhere_is_not_accepted(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            "src/services/news.py",
            """
            from somewhere import RemoteService


            class NewsService(RemoteService):
                \"\"\"News.\"\"\"
            """,
        )
        assert _codes(lint_layers(tmp_path)) == [("news.py", 4, "SVC001")]

    def test_marker_opts_out(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/catalogue.py",
            f"""
            class CatalogueService:  {NO_BASE_SERVICE_MARKER}
                \"\"\"Static catalogue read from a file; there is no repository.\"\"\"

                def __init__(self, path: str) -> None:
                    \"\"\"Remember the file.\"\"\"
                    self.path = path
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_marker_on_a_split_header(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/catalogue.py",
            f"""
            class CatalogueService(
                object,
            ):  {NO_BASE_SERVICE_MARKER}
                \"\"\"Static catalogue.\"\"\"
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_marker_inside_the_body_does_not_count(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/catalogue.py",
            f"""
            class CatalogueService:
                \"\"\"Static catalogue.\"\"\"

                path = "x"  {NO_BASE_SERVICE_MARKER}
            """,
        )
        assert _codes(lint_layers(tmp_path)) == [("catalogue.py", 1, "SVC001")]

    def test_private_class_is_not_checked(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/product.py",
            GOOD_SERVICE
            + """

    class _Row:
        \"\"\"Internal row shape.\"\"\"
""",
        )
        assert lint_layers(tmp_path) == []


class TestOneServicePerModule:
    """``SVC002``: one public class and no module-level function."""

    def test_flags_each_extra_class_and_each_function(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/feeds.py",
            """
            import re

            from tempest_fastapi_sdk import BaseService


            def page_envelope(items: list[int]) -> dict[str, list[int]]:
                \"\"\"Wrap items.\"\"\"
                return {"items": items}


            def word_pattern(word: str) -> re.Pattern[str]:
                \"\"\"Compile a word regex.\"\"\"
                return re.compile(word)


            async def alignment_of(score: float) -> str:
                \"\"\"Name a score.\"\"\"
                return "left"


            class NewsService(BaseService):
                \"\"\"News.\"\"\"


            class TrendService(BaseService):
                \"\"\"Trends.\"\"\"


            class FactCheckService(BaseService):
                \"\"\"Fact checks.\"\"\"
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [
            ("feeds.py", 6, "SVC002"),
            ("feeds.py", 11, "SVC002"),
            ("feeds.py", 16, "SVC002"),
            ("feeds.py", 25, "SVC002"),
            ("feeds.py", 29, "SVC002"),
        ]
        assert "utils/" in findings[0].message
        assert "`TrendService` shares this module with `NewsService`" in (
            findings[3].message
        )

    def test_private_function_is_still_a_module_function(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/product.py",
            GOOD_SERVICE
            + """

    def _slug(name: str) -> str:
        \"\"\"Slugify.\"\"\"
        return name.lower()
""",
        )
        assert [finding.code for finding in lint_layers(tmp_path)] == ["SVC002"]

    def test_module_dunder_getattr_is_allowed(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/__init__.py",
            """
            from typing import Any


            def __getattr__(name: str) -> Any:
                \"\"\"Lazy re-export.\"\"\"
                raise AttributeError(name)
            """,
        )
        assert lint_layers(tmp_path) == []


class TestRedundantInit:
    """``SVC003``: an ``__init__`` that only does what the base does."""

    @pytest.mark.parametrize(
        "body",
        [
            "self.repository = repository",
            "self.repository: ProductRepository = repository",
            "super().__init__(repository)",
            "super().__init__(repository=repository)",
            "BaseService.__init__(self, repository)",
            "super().__init__(repository)\n"
            + " " * 20
            + "self.repository = repository",
        ],
    )
    def test_flags_each_redundant_shape(self, tmp_path: Path, body: str) -> None:
        _write(
            tmp_path,
            "src/services/product.py",
            f"""
            from tempest_fastapi_sdk import BaseService


            class ProductService(BaseService):
                \"\"\"Products.\"\"\"

                def __init__(self, repository: ProductRepository) -> None:
                    \"\"\"Store the repository.\"\"\"
                    {body}
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [("product.py", 7, "SVC003")]
        assert "delete it" in findings[0].message

    @pytest.mark.parametrize(
        "signature, body",
        [
            ("session: AsyncSession", "super().__init__(ProductRepository(session))"),
            (
                "repository: ProductRepository, mailer: Mailer",
                "super().__init__(repository)\n" + " " * 20 + "self.mailer = mailer",
            ),
            ("repository: ProductRepository", "self.repository = build(repository)"),
        ],
    )
    def test_leaves_an_init_with_its_own_work(
        self, tmp_path: Path, signature: str, body: str
    ) -> None:
        _write(
            tmp_path,
            "src/services/product.py",
            f"""
            from tempest_fastapi_sdk import BaseService


            class ProductService(BaseService):
                \"\"\"Products.\"\"\"

                def __init__(self, {signature}) -> None:
                    \"\"\"Build the service.\"\"\"
                    {body}
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_opted_out_service_keeps_its_init(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/catalogue.py",
            f"""
            class CatalogueService:  {NO_BASE_SERVICE_MARKER}
                \"\"\"Static catalogue.\"\"\"

                def __init__(self, repository: object) -> None:
                    \"\"\"Store the source.\"\"\"
                    self.repository = repository
            """,
        )
        assert lint_layers(tmp_path) == []


class TestRepositoryWrapper:
    """``REPO001``: a method that only forwards to ``BaseRepository``."""

    def test_flags_the_three_wrappers_of_the_issue(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/db/repositories/news.py",
            """
            from typing import Any

            from tempest_fastapi_sdk import BaseRepository


            class NewsRepository(BaseRepository[NewsModel]):
                \"\"\"News rows.\"\"\"

                async def list_all(self) -> list[NewsModel]:
                    \"\"\"Every row.\"\"\"
                    return await self.list()

                async def list_ordered(self) -> list[NewsModel]:
                    return await self.list(order_by="-created_at")

                def list_filtered(self, filters: dict[str, Any]) -> Any:
                    \"\"\"Rows matching the filters, not awaited.\"\"\"
                    return self.list(filters)
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [
            ("news.py", 9, "REPO001"),
            ("news.py", 13, "REPO001"),
            ("news.py", 16, "REPO001"),
        ]
        assert "BaseRepository.list" in findings[0].message
        assert "`repository.list(...)`" in findings[0].message

    def test_leaves_real_queries_and_overrides(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/db/repositories/news.py",
            """
            from sqlalchemy import select
            from tempest_fastapi_sdk import BaseRepository


            class NewsRepository(BaseRepository[NewsModel]):
                \"\"\"News rows.\"\"\"

                async def delete(self, id: int) -> None:
                    \"\"\"Deleting news hides it.\"\"\"
                    return await self.soft_delete(id)

                async def trending(self) -> list[NewsModel]:
                    \"\"\"A query the base cannot express.\"\"\"
                    result = await self.session.execute(select(NewsModel))
                    return list(result.scalars())

                async def recent(self) -> list[NewsModel]:
                    \"\"\"Forwards to the class's own method, not the base.\"\"\"
                    return await self.paginate()

                async def paginate(self) -> list[NewsModel]:
                    \"\"\"Overridden here.\"\"\"
                    return []

                async def via_other(self) -> list[NewsModel]:
                    \"\"\"Calls another object.\"\"\"
                    return await self.session.list()
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_base_method_list_matches_base_repository(self) -> None:
        """A method added to the base must reach the rule, not slip past it."""
        public = {
            name
            for name, value in vars(BaseRepository).items()
            if not name.startswith("_")
            and (
                inspect.isfunction(value)
                or isinstance(value, classmethod | staticmethod)
            )
        }
        assert public == BASE_REPOSITORY_METHODS


class TestDiscovery:
    """Where the checker looks, and what it never reads."""

    def test_app_root_and_nested_layers(self, tmp_path: Path) -> None:
        _write(
            tmp_path, "app/billing/services/invoice.py", "class InvoiceService: ...\n"
        )
        assert _codes(lint_layers(tmp_path)) == [("invoice.py", 1, "SVC001")]

    def test_target_may_be_the_source_root(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/services/invoice.py", "class InvoiceService: ...\n")
        assert scan_roots(tmp_path / "src") == [tmp_path / "src"]
        assert len(lint_layers(tmp_path / "src")) == 1

    def test_virtualenv_and_tests_are_never_read(self, tmp_path: Path) -> None:
        for relative in (
            ".venv/lib/pkg/services/x.py",
            "tests/services/x.py",
            "node_modules/a/services/x.py",
        ):
            _write(tmp_path, relative, "class XService: ...\n")
        assert lint_layers(tmp_path) == []

    def test_a_file_that_does_not_parse_is_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/services/broken.py", "class (:\n")
        assert lint_layers(tmp_path) == []

    def test_no_layers_is_clean(self, tmp_path: Path) -> None:
        assert lint_layers(tmp_path) == []
        assert lint_layers(tmp_path / "missing") == []


class TestRunner:
    """``run_lint_layers``: the callable ``tempest check`` runs."""

    def test_prints_one_line_per_finding_and_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write(tmp_path, "src/services/news.py", "class NewsService: ...\n")
        monkeypatch.chdir(tmp_path)
        stream = io.StringIO()
        assert run_lint_layers(".", stream=stream) == 1
        lines = stream.getvalue().splitlines()
        assert lines[0].startswith("src/services/news.py:1: SVC001 `NewsService`")
        assert lines[-1] == "1 layer finding(s)."

    def test_clean_project_passes(self, tmp_path: Path) -> None:
        stream = io.StringIO()
        assert run_lint_layers(str(tmp_path), stream=stream) == 0
        assert stream.getvalue() == "Layer rules: no findings.\n"

    def test_command_exit_codes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        clean = runner.invoke(cli_app, ["lint-layers", str(tmp_path)])
        assert clean.exit_code == 0, clean.stdout
        _write(tmp_path, "src/services/news.py", "class NewsService: ...\n")
        dirty = runner.invoke(cli_app, ["lint-layers"])
        assert dirty.exit_code == 1
        assert "src/services/news.py:1: SVC001" in dirty.stdout

    def test_check_runs_the_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``tempest check`` hands the layer step to the shared gate."""
        import tempest_cli.lint

        seen: dict[str, Any] = {}

        def fake_full_check(target: str, **options: Any) -> int:
            seen["target"] = target
            seen["steps"] = list(options["extra_steps"])
            return 0

        monkeypatch.setattr(tempest_cli.lint, "run_full_check", fake_full_check)
        result = runner.invoke(cli_app, ["check", str(tmp_path)])
        assert result.exit_code == 0, result.stdout
        assert seen["steps"] == [LINT_LAYERS_STEP]
        assert LINT_LAYERS_STEP.name == "lint-layers"
        assert LINT_LAYERS_STEP.run is layer_lint.run_lint_layers


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Scaffold a project with the extras that add layers.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Pytest's temporary
            directory factory.

    Returns:
        Path: The generated project root.
    """
    parent = tmp_path_factory.mktemp("layers")
    result = runner.invoke(
        cli_app,
        ["new", "demo", "--path", str(parent), "--extras", "auth,queue,tasks,ssr"],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    return parent / "demo"


class TestScaffold:
    """The project ``tempest new`` writes passes its own rules."""

    def test_scaffold_is_clean(self, project: Path) -> None:
        assert (project / "src" / "services").is_dir()
        assert (project / "src" / "db" / "repositories").is_dir()
        assert lint_layers(project) == []

    def test_claude_md_examples_are_clean(self, project: Path) -> None:
        """Every ``# src/...`` block of the template, written to disk, passes."""
        written = []
        document = (project / "CLAUDE.md").read_text(encoding="utf-8")
        for source, relative in _BLOCK.findall(document):
            written.append(_write(project, relative, source))
        assert project / "src/services/product.py" in written
        assert project / "src/db/repositories/product.py" in written
        assert lint_layers(project) == []

    def test_claude_md_documents_the_gate(self, project: Path) -> None:
        """The template names the step, every code and the opt-out marker."""
        document = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert "does not enforce these yet" not in document
        for needle in (
            "tempest lint-layers",
            NO_BASE_SERVICE_MARKER,
            "SVC001",
            "SVC002",
            "SVC003",
            "REPO001",
        ):
            assert needle in document
