"""``PgVectorStore`` approximate index options, without a database.

Validation happens before any I/O, and the pgvector version gate and the
per-search settings are checked against a recording fake session. The same
paths run against a real ``pgvector/pgvector:pg16`` in
``tests/genai/test_vectorstore_pg_live.py`` (``make test-docker``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from tempest_fastapi_sdk.genai.rag import PgVectorStore


class _Result:
    """The slice of a SQLAlchemy result the store reads."""

    def __init__(self, scalar: Any = None, row: Any = None, rows: Any = ()) -> None:
        """Hold the canned values.

        Args:
            scalar (Any): What ``scalar_one`` returns.
            row (Any): What ``first`` returns.
            rows (Any): What ``all`` returns.
        """
        self._scalar = scalar
        self._row = row
        self._rows = list(rows)

    def scalar_one(self) -> Any:
        """Return the canned scalar."""
        return self._scalar

    def first(self) -> Any:
        """Return the canned row."""
        return self._row

    def all(self) -> list[Any]:
        """Return the canned rows."""
        return self._rows


class _Session:
    """Record every statement and answer the version probe."""

    def __init__(self, version: str) -> None:
        """Initialize.

        Args:
            version (str): The ``extversion`` the fake database reports.
        """
        self.version = version
        self.statements: list[tuple[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        """Record ``statement`` and return a canned result.

        Args:
            statement (Any): The SQLAlchemy ``text`` clause.
            params (Any): Its bound parameters.

        Returns:
            _Result: The version for the probe, nothing otherwise.
        """
        sql = str(statement)
        self.statements.append((sql, params))
        if "extversion" in sql:
            return _Result(scalar=self.version)
        return _Result()


class _Db:
    """A database manager whose sessions share one recorder."""

    def __init__(self, version: str = "0.8.0") -> None:
        """Initialize.

        Args:
            version (str): The ``extversion`` to report.
        """
        self.session = _Session(version)

    @asynccontextmanager
    async def get_session_context(self) -> AsyncIterator[_Session]:
        """Yield the shared recording session."""
        yield self.session


def _store(db: _Db, table: str = "rag_chunks") -> PgVectorStore:
    """Build a store over the fake manager.

    Args:
        db (_Db): The fake manager.
        table (str): The table name.

    Returns:
        PgVectorStore: The store.
    """
    return PgVectorStore(db, dim=3, table=table)  # type: ignore[arg-type]


class TestAnnIndexValidation:
    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"ann_index": "hnsw", "lists": 10}, "lists does not apply to a hnsw"),
            ({"ann_index": "ivfflat", "m": 8}, "m does not apply to a ivfflat"),
            (
                {"ann_index": "ivfflat", "ef_construction": 8},
                "ef_construction does not apply",
            ),
            ({"m": 8}, "m needs ann_index"),
            ({"lists": 8}, "lists needs ann_index"),
            ({"ann_index": "hnsw", "m": 0}, "m must be a positive integer"),
            ({"ann_index": "hnsw", "m": True}, "m must be a positive integer"),
            ({"ann_index": "ivfflat", "lists": -1}, "lists must be a positive"),
            ({"ann_index": "flat"}, "ann_index must be"),
            ({"ann_index": "hnsw; DROP TABLE x"}, "ann_index must be"),
        ],
    )
    async def test_rejects_before_touching_the_database(
        self,
        kwargs: dict[str, Any],
        match: str,
    ) -> None:
        db = _Db()
        with pytest.raises(ValueError, match=match):
            await _store(db).ensure_schema(**kwargs)
        assert db.session.statements == []

    async def test_hnsw_ddl_uses_the_cosine_opclass_and_given_params(self) -> None:
        db = _Db()
        await _store(db, "kb.docs").ensure_schema(
            ann_index="hnsw",
            m=8,
            ef_construction=32,
        )
        ddl = db.session.statements[-1][0]
        assert ddl == (
            "CREATE INDEX docs_embedding_idx ON kb.docs USING hnsw "
            "(embedding vector_cosine_ops) WITH (m = 8, ef_construction = 32)"
        )

    async def test_defaults_omit_the_with_clause(self) -> None:
        db = _Db()
        await _store(db).ensure_schema(ann_index="ivfflat")
        assert db.session.statements[-1][0] == (
            "CREATE INDEX rag_chunks_embedding_idx ON rag_chunks USING ivfflat "
            "(embedding vector_cosine_ops)"
        )

    async def test_without_ann_index_no_approximate_index_is_built(self) -> None:
        db = _Db()
        await _store(db).ensure_schema()
        assert not any("USING" in sql for sql, _ in db.session.statements)
        assert not any("extversion" in sql for sql, _ in db.session.statements)


class TestPgvectorVersionGate:
    @pytest.mark.parametrize("version", ["0.4.4", "0.4.0", "0.1.8"])
    async def test_hnsw_on_old_pgvector_is_a_clear_error(self, version: str) -> None:
        db = _Db(version)
        with pytest.raises(RuntimeError, match=rf"pgvector >= 0\.5\.0.*{version}"):
            await _store(db).ensure_schema(ann_index="hnsw")
        assert not any("USING hnsw" in sql for sql, _ in db.session.statements)

    @pytest.mark.parametrize("version", ["0.5.0", "0.5.1", "0.8.0", "1.0"])
    async def test_hnsw_on_supported_pgvector_is_built(self, version: str) -> None:
        db = _Db(version)
        await _store(db).ensure_schema(ann_index="hnsw")
        assert "USING hnsw" in db.session.statements[-1][0]

    async def test_ivfflat_does_not_need_0_5(self) -> None:
        db = _Db("0.4.4")
        await _store(db).ensure_schema(ann_index="ivfflat", lists=10)
        assert "USING ivfflat" in db.session.statements[-1][0]


class TestAnnIndexName:
    def test_is_schema_qualified_like_the_table(self) -> None:
        assert _store(_Db(), "kb.docs").ann_index_name == "kb.docs_embedding_idx"
        assert _store(_Db()).ann_index_name == "rag_chunks_embedding_idx"

    def test_keeps_the_suffix_under_the_identifier_limit(self) -> None:
        name = _store(_Db(), "t" * 63).ann_index_name
        assert len(name) == 63
        assert name.endswith("_embedding_idx")


class TestPerSearchSettings:
    async def test_ef_search_and_probes_are_transaction_local(self) -> None:
        db = _Db()
        store = _store(db)
        store._ready = True
        await store.search([1.0, 0.0, 0.0], top_k=3, ef_search=100, probes=7)
        calls = [
            params
            for sql, params in db.session.statements
            if sql == "SELECT set_config(:name, :value, true)"
        ]
        assert calls == [
            {"name": "hnsw.ef_search", "value": "100"},
            {"name": "ivfflat.probes", "value": "7"},
        ]

    async def test_unset_options_send_nothing(self) -> None:
        db = _Db()
        store = _store(db)
        store._ready = True
        await store.search([1.0, 0.0, 0.0], top_k=3)
        assert not any("set_config" in sql for sql, _ in db.session.statements)

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"ef_search": 0}, "ef_search"),
            ({"ef_search": False}, "ef_search"),
            ({"probes": -3}, "probes"),
        ],
    )
    async def test_invalid_settings_are_refused(
        self,
        kwargs: dict[str, Any],
        match: str,
    ) -> None:
        db = _Db()
        with pytest.raises(ValueError, match=match):
            await _store(db).search([1.0, 0.0, 0.0], **kwargs)
        assert db.session.statements == []
