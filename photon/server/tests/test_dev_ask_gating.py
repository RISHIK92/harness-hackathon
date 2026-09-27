"""/dev/ask impersonates a user with no password and no token. The only
thing standing between that and a real deployment is the app_env check in
main.py, so it is worth a test that cannot be satisfied by accident.

Static, like test_agent_loop.py's transport-import check: it needs no DB, no
network and no app startup, and it fails on the edit that would actually
cause the problem — someone moving the include_router line out of the
guarded block while tidying up.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"


def _guarded_includes(tree: ast.Module) -> set[str]:
    """Router modules included INSIDE an `if ... app_env != "production"` block."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = ast.unparse(node.test)
        if "app_env" not in test or "production" not in test:
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "include_router"
                and call.args
            ):
                found.add(ast.unparse(call.args[0]).split(".")[0])
    return found


def test_dev_ask_is_mounted_only_outside_production():
    tree = ast.parse(MAIN.read_text())
    assert "dev_ask" in _guarded_includes(tree), (
        "app.routers.dev_ask must be included inside the app_env != 'production' "
        "guard — it answers as any user with no authentication at all"
    )


def test_it_is_not_also_mounted_unguarded():
    """A second, unguarded include would make the guarded one meaningless."""
    tree = ast.parse(MAIN.read_text())
    guarded_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.If)
                     and "app_env" in ast.unparse(n.test) and "production" in ast.unparse(n.test)]
    guarded_lines = {
        c.lineno for g in guarded_nodes for c in ast.walk(g) if hasattr(c, "lineno")
    }

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "include_router"
            and node.args
            and ast.unparse(node.args[0]).startswith("dev_ask")
        ):
            assert node.lineno in guarded_lines, f"unguarded dev_ask mount at line {node.lineno}"
