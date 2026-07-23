"""Fixed evaluation corpus for RAG retrieval metrics.

A small, hand-labelled set used to compute reproducible recall@k / MRR for
items #4/#6/#13. Kept intentionally tiny and deterministic — no randomness, no
network. ``PROPER_NOUN_QUERIES`` targets the exact-term weakness dense-only
retrieval has, which #6 (hybrid BM25+dense) must beat.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalDoc:
    """A corpus document.

    Attributes:
        id (str): Stable identifier used in ranked results.
        text (str): The document body.
    """

    id: str
    text: str


@dataclass(frozen=True)
class EvalQuery:
    """A labelled query.

    Attributes:
        text (str): The query string.
        relevant_id (str): The single doc id that answers it.
    """

    text: str
    relevant_id: str


CORPUS: tuple[EvalDoc, ...] = (
    EvalDoc(
        "pix", "PIX is the Brazilian instant payment system run by the Central Bank."
    ),
    EvalDoc(
        "cpf", "The CPF is the individual taxpayer registry identification in Brazil."
    ),
    EvalDoc("cnpj", "The CNPJ identifies legal entities for tax purposes in Brazil."),
    EvalDoc("boleto", "A boleto is a bank slip payment method widely used in Brazil."),
    EvalDoc("ted", "TED is a wire transfer settled on the same business day."),
    EvalDoc("selic", "The Selic rate is Brazil's benchmark interest rate."),
    EvalDoc(
        "ibge", "IBGE is the institute that produces Brazil's official statistics."
    ),
    EvalDoc(
        "bacen", "BACEN, the Banco Central do Brasil, regulates the financial system."
    ),
)


PROPER_NOUN_QUERIES: tuple[EvalQuery, ...] = (
    EvalQuery("What is PIX?", "pix"),
    EvalQuery("Tell me about CNPJ.", "cnpj"),
    EvalQuery("Explain the Selic rate.", "selic"),
    EvalQuery("What does BACEN do?", "bacen"),
    EvalQuery("What is a boleto?", "boleto"),
)
