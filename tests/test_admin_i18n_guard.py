"""Guard: the admin's interface catalog is complete and the templates use it.

Four checks over ``tempest_fastapi_sdk/admin``:

* every key the templates (``t("...")``, ``tn("...")``) and the Python code
  (``_t("...")``, ``_tn("...")``, ``admin_text(locale, "...")``,
  ``admin_plural(locale, "...")``) read exists in every locale of
  :data:`~tempest_fastapi_sdk.admin.i18n.ADMIN_MESSAGES`, and no call passes
  a key the scan cannot read (a variable instead of a literal);
* no catalog key is orphaned;
* each key carries the same ``{placeholders}`` in every locale;
* no template carries visible literal text outside the catalog.

The last one is a heuristic, measured on the templates of v0.369.1 (the
shape that shipped before the catalog): Jinja comments, statements and
expressions are dropped, then ``<script>``, ``<style>``, ``<code>`` and
``<pre>`` bodies and HTML entities, then the tags; any letter left in a text
node is visible text. The attributes a browser shows (``placeholder``,
``title``, ``aria-label``, ``alt``, ``data-label``, which the mobile table
prints through CSS, and ``data-confirm``) and the literal of an inline
``confirm('...')`` are checked the same way.
Run over the 19 templates of v0.369.1 it flags all 19, with 250 findings;
over the current ones, none.

Blind spots, by construction: a string literal inside a Jinja expression
(``{{ "x" if a else "y" }}``) is dropped with the expression, so it is not
seen; text inside ``<code>`` / ``<pre>`` is treated as code; a key built at
runtime is refused rather than resolved; translation quality is not checked.
"""

from __future__ import annotations

import re
from pathlib import Path
from string import Formatter

from tempest_fastapi_sdk.admin.i18n import ADMIN_LOCALES, ADMIN_MESSAGES

ADMIN_DIR = Path(__file__).resolve().parent.parent / "tempest_fastapi_sdk" / "admin"
TEMPLATES_DIR = ADMIN_DIR / "templates"

TEMPLATE_KEY = re.compile(r"""\bt\(\s*["']([^"']+)["']""")
TEMPLATE_PLURAL_KEY = re.compile(r"""\btn\(\s*["']([^"']+)["']""")
TEMPLATE_DYNAMIC = re.compile(r"""\bt[n]?\(\s*(?!["'])[^)\s]""")
PY_KEY = re.compile(r"""\b_t\(\s*"([^"]+)"|\badmin_text\(\s*[\w.]+\s*,\s*"([^"]+)\"""")
PY_PLURAL_KEY = re.compile(
    r"""\b_tn\(\s*"([^"]+)"|\badmin_plural\(\s*[\w.]+\s*,\s*"([^"]+)\""""
)
PY_DYNAMIC = re.compile(
    r"""(?<!def )\b_tn?\(\s*(?!["'])[^)\s]"""
    r"""|\badmin_(?:text|plural)\(\s*[\w.]+\s*,\s*(?!["']|key,)[^)\s]"""
)
PY_EXEMPT = frozenset({"i18n.py"})
"""Modules that define the helpers rather than call them with a key.

Inside other modules, ``def _t(`` and the helpers' own forwarding call
(``admin_text(site.locale, key, ...)``) are not reads of a key either, so
:data:`PY_DYNAMIC` skips both.
"""

JINJA = re.compile(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", re.DOTALL)
CODE_BLOCKS = re.compile(
    r"<(script|style|code|pre)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
ENTITY = re.compile(r"&[a-zA-Z]+;|&#\d+;")
TAG = re.compile(r"<[^>]+>", re.DOTALL)
VISIBLE_ATTRIBUTE = re.compile(
    r"""\b(placeholder|title|aria-label|alt|data-label|data-confirm)\s*=\s*"([^"]*)\"""",
    re.IGNORECASE,
)
INLINE_CONFIRM = re.compile(r"""confirm\(\s*'([^']*)'""")
LETTER = re.compile(r"[A-Za-zÀ-ÿ]")


def template_keys(source: str) -> set[str]:
    """Return the catalog keys a template reads.

    Args:
        source (str): The template text.

    Returns:
        set[str]: Keys, with a ``tn`` key expanded to ``.one`` / ``.other``.
    """
    keys = set(TEMPLATE_KEY.findall(source))
    for base in TEMPLATE_PLURAL_KEY.findall(source):
        keys.update({f"{base}.one", f"{base}.other"})
    return keys


def python_keys(source: str) -> set[str]:
    """Return the catalog keys a module reads.

    Args:
        source (str): The module text.

    Returns:
        set[str]: Keys, with a plural key expanded to ``.one`` / ``.other``.
    """
    keys = {a or b for a, b in PY_KEY.findall(source)}
    for a, b in PY_PLURAL_KEY.findall(source):
        base = a or b
        keys.update({f"{base}.one", f"{base}.other"})
    return keys


def visible_literals(source: str) -> list[str]:
    """Return the visible text a template carries outside the catalog.

    Args:
        source (str): The template text.

    Returns:
        list[str]: Each offending text node, attribute or confirm literal.
    """
    findings: list[str] = []
    stripped = JINJA.sub(" ", source)
    for name, value in VISIBLE_ATTRIBUTE.findall(stripped):
        if LETTER.search(value):
            findings.append(f'{name}="{value}"')
    for value in INLINE_CONFIRM.findall(stripped):
        if LETTER.search(value):
            findings.append(f"confirm('{value}')")
    text = TAG.sub(" ", ENTITY.sub(" ", CODE_BLOCKS.sub(" ", stripped)))
    findings.extend(chunk.strip() for chunk in text.split("\n") if LETTER.search(chunk))
    return findings


def placeholders(template: str) -> set[str]:
    """Return the ``{names}`` a catalog template formats.

    Args:
        template (str): The catalog text.

    Returns:
        set[str]: The field names.
    """
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def used_keys() -> set[str]:
    """Scan the admin templates and modules for every key they read.

    Returns:
        set[str]: Every key read.
    """
    keys: set[str] = set()
    for path in TEMPLATES_DIR.glob("*.html"):
        keys |= template_keys(path.read_text(encoding="utf-8"))
    for path in ADMIN_DIR.glob("*.py"):
        if path.name not in PY_EXEMPT:
            keys |= python_keys(path.read_text(encoding="utf-8"))
    return keys


def missing_keys(
    used: set[str], catalog: dict[str, dict[str, str]]
) -> dict[str, set[str]]:
    """Return, per locale, the used keys it lacks.

    Args:
        used (set[str]): Keys the code reads.
        catalog (dict[str, dict[str, str]]): The catalog to check.

    Returns:
        dict[str, set[str]]: Locale to missing keys (only non-empty).
    """
    gaps = {locale: used - set(entries) for locale, entries in catalog.items()}
    return {locale: keys for locale, keys in gaps.items() if keys}


def orphan_keys(used: set[str], catalog: dict[str, dict[str, str]]) -> set[str]:
    """Return the catalog keys nothing reads.

    Args:
        used (set[str]): Keys the code reads.
        catalog (dict[str, dict[str, str]]): The catalog to check.

    Returns:
        set[str]: Orphans across every locale.
    """
    defined: set[str] = set()
    for entries in catalog.values():
        defined |= set(entries)
    return defined - used


def placeholder_mismatches(catalog: dict[str, dict[str, str]]) -> dict[str, str]:
    """Return the keys whose placeholders differ between locales.

    Args:
        catalog (dict[str, dict[str, str]]): The catalog to check.

    Returns:
        dict[str, str]: Key to a description of the disagreement.
    """
    mismatches: dict[str, str] = {}
    keys = set().union(*(set(entries) for entries in catalog.values()))
    for key in sorted(keys):
        found = {
            locale: placeholders(entries[key])
            for locale, entries in catalog.items()
            if key in entries
        }
        if len({frozenset(names) for names in found.values()}) > 1:
            mismatches[key] = repr(found)
    return mismatches


def _catalog() -> dict[str, dict[str, str]]:
    """Copy the shipped catalog into plain dicts.

    Returns:
        dict[str, dict[str, str]]: ``{locale: {key: text}}``.
    """
    return {locale: dict(entries) for locale, entries in ADMIN_MESSAGES.items()}


def test_catalog_has_every_shipped_locale() -> None:
    assert set(ADMIN_MESSAGES) == set(ADMIN_LOCALES)


def test_every_used_key_exists_in_every_locale() -> None:
    used = used_keys()
    assert len(used) > 200, "the scan found too few keys to be reading the code"
    assert missing_keys(used, _catalog()) == {}


def test_no_orphan_key() -> None:
    assert orphan_keys(used_keys(), _catalog()) == set()


def test_placeholders_agree_between_locales() -> None:
    assert placeholder_mismatches(_catalog()) == {}


def test_no_key_is_built_at_runtime() -> None:
    offenders: list[str] = []
    for path in TEMPLATES_DIR.glob("*.html"):
        for match in TEMPLATE_DYNAMIC.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}: {match.group(0)}")
    for path in ADMIN_DIR.glob("*.py"):
        if path.name in PY_EXEMPT:
            continue
        for match in PY_DYNAMIC.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}: {match.group(0)}")
    assert offenders == []


def test_templates_carry_no_visible_literal_text() -> None:
    templates = sorted(TEMPLATES_DIR.glob("*.html"))
    assert len(templates) >= 19
    findings = {
        path.name: found
        for path in templates
        if (found := visible_literals(path.read_text(encoding="utf-8")))
    }
    assert findings == {}


SHIPPED_LIST_EXCERPT = """
<div class="tempest-admin-list__actions">
  <a class="tempest-admin-btn" href="{{ url }}">Export CSV</a>
</div>
<input type="search" name="q" value="{{ query.q or '' }}"
  placeholder="Search…" aria-label="Search">
<button type="submit" class="tempest-admin-btn">Apply</button>
<tr><td colspan="{{ ncols }}" class="tempest-admin-empty">No records.</td></tr>
<form onsubmit="return confirm('Delete this record? This cannot be undone.');"></form>
<p>{{ t("list.empty") }} <code>AdminModel</code> &laquo; {# a comment #}</p>
"""
"""Lines of ``list.html`` / ``detail.html`` as v0.369.1 shipped them."""


def test_visible_text_check_fires_on_the_shipped_templates() -> None:
    findings = visible_literals(SHIPPED_LIST_EXCERPT)
    assert 'placeholder="Search…"' in findings
    assert 'aria-label="Search"' in findings
    assert "confirm('Delete this record? This cannot be undone.')" in findings
    texts = " | ".join(findings)
    assert "Export CSV" in texts
    assert "Apply" in texts
    assert "No records." in texts
    assert "AdminModel" not in texts
    assert "laquo" not in texts
    assert "comment" not in texts


def test_key_checks_fire() -> None:
    catalog = {
        "en": {"a.b": "Hello {name}", "orphan": "x"},
        "pt-BR": {"a.b": "Olá {nome}"},
    }
    used = {"a.b", "c.d"}
    assert missing_keys(used, catalog) == {"en": {"c.d"}, "pt-BR": {"c.d"}}
    assert orphan_keys(used, catalog) == {"orphan"}
    assert set(placeholder_mismatches(catalog)) == {"a.b"}


def test_key_scans_read_every_call_form() -> None:
    template = """{{ t("x.one_key") }} {{ tn('x.count', n) }} {{ t(name) }}"""
    assert template_keys(template) == {"x.one_key", "x.count.one", "x.count.other"}
    assert TEMPLATE_DYNAMIC.search(template) is not None
    module = (
        '_t("p.a") _tn("p.b", 2) admin_text(locale, "p.c") '
        'admin_plural(site.locale, "p.d", 1) _t(key)'
    )
    assert python_keys(module) == {
        "p.a",
        "p.b.one",
        "p.b.other",
        "p.c",
        "p.d.one",
        "p.d.other",
    }
    assert PY_DYNAMIC.search(module) is not None
