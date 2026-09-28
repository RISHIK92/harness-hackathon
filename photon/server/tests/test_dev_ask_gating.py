"""/dev/ask impersonates a user with no password and no token. What stands
between that and a real deployment is two guards in main.py: an explicit
APP_ENV=development (the default is production) AND a second, specific
ENABLE_DEV_IMPERSONATION opt-in — a development box can still be exposed
(scripts/dev.sh --with-ngrok publishes :8000). Worth a test that cannot be
satisfied by accident.

Static, like test_agent_loop.py's transport-import check: it needs no DB, no
network and no app startup, and it fails on the edit that would actually
cause the problem — someone moving the include_router line out of the
guarded block while tidying up, or flipping the default back.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"


def _is_dev_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = ast.unparse(node.test)
    return "app_env" in test and "'development'" in test.replace('"', "'") and "==" in test


def _is_impersonation_guard(node: ast.AST) -> bool:
    return isinstance(node, ast.If) and "enable_dev_impersonation" in ast.unparse(node.test)


def _includes(node: ast.AST, module: str) -> list[ast.Call]:
    return [
        c for c in ast.walk(node)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr == "include_router"
        and c.args
        and ast.unparse(c.args[0]).startswith(module)
    ]


def test_dev_ask_is_mounted_only_under_both_opt_ins():
    tree = ast.parse(MAIN.read_text())
    dev_blocks = [n for n in ast.walk(tree) if _is_dev_guard(n)]
    assert dev_blocks, "main.py must mount dev routers only under `if settings.app_env == 'development'`"
    nested = [g for block in dev_blocks for g in ast.walk(block) if _is_impersonation_guard(g)]
    assert any(_includes(g, "dev_ask") for g in nested), (
        "app.routers.dev_ask must be included inside BOTH the development guard and the "
        "enable_dev_impersonation guard — it answers as any user with no authentication at all"
    )


def test_it_is_not_also_mounted_unguarded():
    """A second, less-guarded include would make the guarded one meaningless."""
    tree = ast.parse(MAIN.read_text())
    guarded_lines = {
        c.lineno
        for block in ast.walk(tree) if _is_dev_guard(block)
        for g in ast.walk(block) if _is_impersonation_guard(g)
        for c in ast.walk(g) if hasattr(c, "lineno")
    }
    for call in _includes(tree, "dev_ask"):
        assert call.lineno in guarded_lines, f"under-guarded dev_ask mount at line {call.lineno}"


def test_the_default_environment_is_production():
    """Fail closed: a deployment that forgets APP_ENV must not get dev routes."""
    from app.config import Settings

    assert Settings.model_fields["app_env"].default == "production"
