"""Module boundaries that keep R4's MCP backend a drop-in: the tools talk to
the Backend interface, never to the JSON:API client module directly (only
tools/_shared.py, which builds the backend, may)."""

from __future__ import annotations

import ast
from pathlib import Path

import dreachy.tools

_TOOLS_DIR = Path(dreachy.tools.__file__).parent


def test_no_tool_imports_the_json_api_client_module() -> None:
    offenders = []
    for path in sorted(_TOOLS_DIR.glob("*.py")):
        if path.name == "_shared.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "dreachy.client":
                offenders.append(path.name)

    assert offenders == []
