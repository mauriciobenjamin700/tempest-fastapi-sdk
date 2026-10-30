"""AsyncAPI prose stays inert inside the generated docstrings.

The stream emitter shares the OpenAPI emitter's docstring path: ``info.title``
lands in three module docstrings and the class docstring, and the channel's
``description`` is wrapped into the class docstring. A triple quote in either
used to close the literal, so the rest of the text ran on import.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tempest_fastapi_sdk.asyncapi.generate import generate_stream
from tests.asyncapi.conftest import _document


def _payload(marker: Path, tag: str) -> str:
    """Build prose that writes ``marker`` if it ever escapes its docstring.

    Args:
        marker (Path): File the escaped code would create.
        tag (str): Text written into the file.

    Returns:
        str: The hostile prose, free of spaces so wrapping keeps it whole.
    """
    return (
        '"""+__import__("pathlib").Path('
        + json.dumps(str(marker))
        + ').write_text("'
        + tag
        + '").__str__()+"""'
    )


@pytest.mark.parametrize("run_format", [False, True])
def test_title_and_channel_description_run_nothing(
    tmp_path: Path, run_format: bool
) -> None:
    """Importing the package creates no marker and keeps the prose verbatim."""
    marker = tmp_path / "marker.txt"
    document = _document(info={"title": _payload(marker, "title"), "version": "1.0.0"})
    document["channels"]["socket"]["description"] = _payload(marker, "channel")
    spec = tmp_path / "chat.json"
    spec.write_text(json.dumps(document), encoding="utf-8")
    out = tmp_path / "pkg" / "chat_ws"

    generate_stream(spec.as_posix(), out=out, name="chat", run_format=run_format)

    completed = subprocess.run(
        [sys.executable, "-c", "import chat_ws, chat_ws.stream, chat_ws.schemas"],
        cwd=out.parent,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert not marker.exists()

    tree = ast.parse((out / "stream.py").read_text(encoding="utf-8"))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    client = next(node for node in classes if node.name == "ChatStream")
    docstring = " ".join((ast.get_docstring(client, clean=False) or "").split())
    assert _payload(marker, "title") in docstring
    assert _payload(marker, "channel") in docstring
    module_doc = ast.get_docstring(tree, clean=False) or ""
    assert _payload(marker, "title") in module_doc
