"""Tests for the name and municipality agent tools."""

from __future__ import annotations

from typing import Any

import pytest

from tempest_fastapi_sdk.agents import (
    AgentToolError,
    municipality_search_tool,
    name_search_tool,
)
from tempest_fastapi_sdk.text import FuzzyIndex


async def _call(tool: Any, arguments: dict[str, Any]) -> str:
    """Run a tool's handler outside an agent.

    Args:
        tool (Any): The ``AgentTool``.
        arguments (dict[str, Any]): What the model would pass.

    Returns:
        str: The handler's text.
    """
    return str(await tool.handler(arguments, None))


class TestNameSearchTool:
    async def test_lists_matches_with_why(self) -> None:
        """Cenário: ``FuzzyIndex`` síncrono, consulta com erro.

        Esperado: uma linha por achado, com nome, valor e nota.
        """
        tool = name_search_tool(FuzzyIndex([("Camiseta básica", 101)]))
        assert tool.name == "search_names"
        text = await _call(tool, {"query": "camizeta basica"})
        assert text.startswith("camiseta basica -> 101 (score 0.9")

    async def test_says_when_nothing_matches(self) -> None:
        """Cenário: consulta sem achado.

        Esperado: frase dizendo que nada casou, não string vazia.
        """
        tool = name_search_tool(FuzzyIndex([("Camiseta", 1)]))
        assert await _call(tool, {"query": "geladeira"}) == (
            "No entry matches 'geladeira'."
        )

    async def test_query_is_required(self) -> None:
        """Cenário: sem ``query``.

        Esperado: ``AgentToolError``, que o agente devolve ao modelo.
        """
        with pytest.raises(AgentToolError):
            await _call(name_search_tool(FuzzyIndex()), {})


class TestMunicipalitySearchTool:
    async def test_city_in_a_sentence(self) -> None:
        """Cenário: frase do usuário com cidade e UF.

        Esperado: a cidade, a UF, o código IBGE e a coordenada.
        """
        text = await _call(
            municipality_search_tool(), {"text": "moro em sao jose dos campos - sp"}
        )
        assert text.startswith("São José dos Campos/SP — IBGE 3549904, lat ")
        assert "state confirmed" in text

    async def test_uf_filters_homonyms(self) -> None:
        """Cenário: ``Bom Jesus`` com ``uf=PI``.

        Esperado: só a do Piauí.
        """
        text = await _call(
            municipality_search_tool(), {"text": "bom jesus", "uf": "PI"}
        )
        assert text.splitlines() == [
            line for line in text.splitlines() if "/PI " in line
        ]
        assert "Bom Jesus/PI" in text

    async def test_bad_state_is_a_tool_error(self) -> None:
        """Cenário: ``uf`` que não é estado.

        Esperado: ``AgentToolError`` nomeando o valor.
        """
        with pytest.raises(AgentToolError, match="XX"):
            await _call(municipality_search_tool(), {"text": "campinas", "uf": "XX"})

    async def test_nothing_found(self) -> None:
        """Cenário: texto sem cidade.

        Esperado: frase dizendo que nada foi achado.
        """
        assert await _call(
            municipality_search_tool(), {"text": "estou feliz hoje"}
        ) == ("No Brazilian municipality found in 'estou feliz hoje'.")
