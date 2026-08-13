"""Brazilian document formatting: money, dates, and value in words.

These are template filters, but they are here rather than inline in the
templates because they are the part with rules. ``valor_por_extenso`` in
particular is conventional on a *recibo* — a receipt without it reads as
unfinished — and it is exactly the kind of thing that gets written from
memory, wrongly, in every project.

Money is handled in **cents**, as integers, everywhere. The SDK already
made that choice for payments (see ``to_cents`` in the OpenPix
integration): a float cannot hold ``0.1 + 0.2`` and a document that
prints a total off by a cent is worse than one that fails.
"""

from __future__ import annotations

from datetime import date, datetime

MONTHS_PT_BR: tuple[str, ...] = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
"""Month names used by :func:`format_date_long`, lowercase as in BR usage."""

_UNITS: tuple[str, ...] = (
    "",
    "um",
    "dois",
    "três",
    "quatro",
    "cinco",
    "seis",
    "sete",
    "oito",
    "nove",
    "dez",
    "onze",
    "doze",
    "treze",
    "quatorze",
    "quinze",
    "dezesseis",
    "dezessete",
    "dezoito",
    "dezenove",
)

_TENS: tuple[str, ...] = (
    "",
    "",
    "vinte",
    "trinta",
    "quarenta",
    "cinquenta",
    "sessenta",
    "setenta",
    "oitenta",
    "noventa",
)

_HUNDREDS: tuple[str, ...] = (
    "",
    "cento",
    "duzentos",
    "trezentos",
    "quatrocentos",
    "quinhentos",
    "seiscentos",
    "setecentos",
    "oitocentos",
    "novecentos",
)

_SCALES: tuple[tuple[str, str], ...] = (
    ("mil", "mil"),
    ("milhão", "milhões"),
    ("bilhão", "bilhões"),
    ("trilhão", "trilhões"),
)

MAX_EXTENSO_CENTS: int = 10**15 - 1
"""Largest value :func:`valor_por_extenso` spells, in cents.

The scale table stops at *trilhões*; a value above it would silently
lose its most significant group, so it raises instead.
"""


def format_cents(cents: int, *, symbol: bool = True) -> str:
    """Format an integer number of cents as Brazilian currency.

    Args:
        cents (int): Amount in cents. Negative values keep the sign in
            front of the symbol (``-R$ 1,00``), which is how a credit
            reads on a Brazilian statement.
        symbol (bool): Whether to prefix ``R$``.

    Returns:
        str: The formatted amount, e.g. ``"R$ 1.234,56"``.
    """
    sign = "-" if cents < 0 else ""
    whole, remainder = divmod(abs(cents), 100)
    grouped = f"{whole:,}".replace(",", ".")
    body = f"{grouped},{remainder:02d}"
    return f"{sign}R$ {body}" if symbol else f"{sign}{body}"


def format_date(value: date | datetime) -> str:
    """Format a date as ``dd/mm/aaaa``.

    Args:
        value (date | datetime): The date to format.

    Returns:
        str: The formatted date.
    """
    return value.strftime("%d/%m/%Y")


def format_date_long(value: date | datetime) -> str:
    """Format a date the way a signed document spells it.

    Args:
        value (date | datetime): The date to format.

    Returns:
        str: E.g. ``"13 de agosto de 2026"``.
    """
    return f"{value.day} de {MONTHS_PT_BR[value.month - 1]} de {value.year}"


def _spell_group(value: int) -> str:
    """Spell a number below 1000 in Portuguese.

    Args:
        value (int): A number in ``1..999``.

    Returns:
        str: The spelled number.
    """
    if value == 100:
        return "cem"
    parts: list[str] = []
    hundreds, rest = divmod(value, 100)
    if hundreds:
        parts.append(_HUNDREDS[hundreds])
    if rest:
        if rest < 20:
            parts.append(_UNITS[rest])
        else:
            tens, units = divmod(rest, 10)
            parts.append(
                _TENS[tens] if not units else f"{_TENS[tens]} e {_UNITS[units]}"
            )
    return " e ".join(parts)


def _spell_integer(value: int) -> str:
    """Spell a non-negative integer in Portuguese.

    Args:
        value (int): The number to spell.

    Returns:
        str: The spelled number, ``"zero"`` for ``0``.
    """
    if value == 0:
        return "zero"
    groups: list[int] = []
    remaining = value
    while remaining:
        remaining, group = divmod(remaining, 1000)
        groups.append(group)
    chunks: list[tuple[str, int]] = []
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if not group:
            continue
        if index == 0:
            chunks.append((_spell_group(group), group))
            continue
        singular, plural = _SCALES[index - 1]
        if index == 1:
            spelled = "mil" if group == 1 else f"{_spell_group(group)} mil"
        else:
            scale = singular if group == 1 else plural
            spelled = f"{_spell_group(group)} {scale}"
        chunks.append((spelled, group))
    return _join_extenso(chunks)


def _join_extenso(chunks: list[tuple[str, int]]) -> str:
    """Join spelled groups with the connector Portuguese actually uses.

    The final group takes ``e`` when its own value is below one hundred
    or a round hundred — ``mil e quinze``, ``mil e duzentos``, ``dois
    milhões e quinhentos mil`` — and a comma otherwise: ``mil, duzentos
    e quinze``. Getting this wrong is what makes a hand-rolled
    implementation read as machine output.

    The test is against the value of the group that produced the **last
    printed chunk**, not the units group: ``2_500_000`` prints down to
    the thousands and its units group is zero, so reading that one gave
    ``dois milhões, quinhentos mil``.

    Args:
        chunks (list[tuple[str, int]]): ``(spelled, value)`` per printed
            group, most significant first.

    Returns:
        str: The joined text.
    """
    if len(chunks) == 1:
        return chunks[0][0]
    head = [spelled for spelled, _ in chunks[:-1]]
    tail, last_value = chunks[-1]
    if last_value < 100 or last_value % 100 == 0:
        return f"{', '.join(head)} e {tail}"
    return ", ".join([*head, tail])


def _takes_de(reais: int) -> bool:
    """Whether the amount needs ``de`` before the currency noun.

    Portuguese inserts it when the number *ends* on a
    milhão/bilhão/trilhão — ``um milhão de reais`` — and not when a
    smaller group follows: ``dois milhões e quinhentos mil reais``.
    ``mil`` never takes it, which is why the test is against a million
    rather than a thousand.

    Args:
        reais (int): Whole currency units.

    Returns:
        bool: ``True`` when ``de`` belongs before the noun.
    """
    return reais >= 10**6 and reais % 10**6 == 0


def valor_por_extenso(cents: int) -> str:
    """Spell an amount in cents as Brazilian currency, in words.

    This is the line a *recibo* carries under the figure, and it exists
    because a figure alone can be altered after signing.

    Args:
        cents (int): Amount in cents. Must be non-negative — a receipt
            for a negative amount is a different document.

    Returns:
        str: E.g. ``"mil duzentos e trinta e quatro reais e cinquenta e
        seis centavos"``.

    Raises:
        ValueError: If ``cents`` is negative, or above
            :data:`MAX_EXTENSO_CENTS` where the scale table runs out and
            the most significant group would be dropped in silence.
    """
    if cents < 0:
        raise ValueError("valor_por_extenso does not spell negative amounts")
    if cents > MAX_EXTENSO_CENTS:
        raise ValueError(
            f"valor_por_extenso handles up to {MAX_EXTENSO_CENTS} cents",
        )
    reais, centavos = divmod(cents, 100)
    parts: list[str] = []
    if reais:
        noun = "real" if reais == 1 else "reais"
        connector = "de " if _takes_de(reais) else ""
        parts.append(f"{_spell_integer(reais)} {connector}{noun}")
    if centavos:
        unit = "centavo" if centavos == 1 else "centavos"
        parts.append(f"{_spell_integer(centavos)} {unit}")
    if not parts:
        return "zero real"
    return " e ".join(parts)


def format_quantity(value: float, *, decimals: int = 3) -> str:
    """Format a quantity the way an invoice line reads in Brazil.

    A whole quantity prints without a decimal part (``2``, not
    ``2,000``); a fractional one uses a comma and drops trailing zeros
    (``2,5``). Printing ``2.5`` on a Brazilian document reads as a
    thousands separator to the person holding it.

    Args:
        value (float): The quantity.
        decimals (int): Maximum decimal places kept.

    Returns:
        str: The formatted quantity.
    """
    rounded = round(value, decimals)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.{decimals}f}".rstrip("0").rstrip(".").replace(".", ",")


def format_document(value: str) -> str:
    """Format a CPF or CNPJ for display, leaving anything else alone.

    Args:
        value (str): Digits, with or without punctuation.

    Returns:
        str: ``000.000.000-00`` for 11 digits, ``00.000.000/0000-00``
        for 14, and the input unchanged otherwise — a foreign passport
        number in this field must not be mangled into a shape it is not.
    """
    from tempest_fastapi_sdk.utils.regex import only_digits

    digits = only_digits(value)
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return value


__all__: list[str] = [
    "MAX_EXTENSO_CENTS",
    "MONTHS_PT_BR",
    "format_cents",
    "format_date",
    "format_date_long",
    "format_document",
    "format_quantity",
    "valor_por_extenso",
]
