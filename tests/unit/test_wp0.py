"""WP0 unit tests: config precedence, redaction, budgets, event log."""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness import config as C
from harness.budget import BudgetExceeded, Budgets, StepCounter, TokenBudget, WallClock
from harness.context.events import EventLog
from harness.logging_ui import Logger, redact


# -- T0.3 config -----------------------------------------------------------
def test_empty_env_var_is_treated_as_unset(monkeypatch):
    """AI_BASE_URL="" must not defeat getenv(name, default). SPEC.md 12."""
    monkeypatch.setenv("AI_BASE_URL", "")
    assert C.env("AI_BASE_URL") is None
    assert C.env("AI_BASE_URL", "https://default") == "https://default"


def test_whitespace_env_var_is_unset(monkeypatch):
    monkeypatch.setenv("HARNESS_MODEL", "   ")
    assert C.env("HARNESS_MODEL") is None


def test_env_int_and_bool(monkeypatch):
    monkeypatch.setenv("HARNESS_MAX_CYCLES", "3")
    assert C.env_int("HARNESS_MAX_CYCLES", 5) == 3
    assert C.env_int("HARNESS_ABSENT", 7) == 7
    monkeypatch.setenv("HARNESS_DRY_RUN", "yes")
    assert C.env_bool("HARNESS_DRY_RUN") is True
    monkeypatch.setenv("HARNESS_DRY_RUN", "0")
    assert C.env_bool("HARNESS_DRY_RUN") is False


def test_env_int_rejects_garbage(monkeypatch):
    monkeypatch.setenv("HARNESS_MAX_CYCLES", "five")
    with pytest.raises(C.ConfigError):
        C.env_int("HARNESS_MAX_CYCLES", 5)


def test_missing_api_key_is_config_error(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    with pytest.raises(C.ConfigError, match="AI_API_KEY"):
        C.load(["prog"])


def test_issue_precedence_argv_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ISSUE", "from env")
    assert C.read_issue(["prog", "from", "argv"]) == "from argv"


def test_issue_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ISSUE", raising=False)
    f = tmp_path / "issue.txt"
    f.write_text("bug in parser")
    monkeypatch.setenv("ISSUE_FILE", str(f))
    assert "bug in parser" in C.read_issue(["prog"])


def test_bad_tier_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ISSUE", "x")
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    monkeypatch.setenv("HARNESS_TIER", "T9")
    with pytest.raises(C.ConfigError):
        C.load(["prog"])


def test_context_budget_derived_not_hardcoded(monkeypatch, tmp_path):
    """A small-window model must never be handed a 180k budget. SPEC.md 6.2."""
    monkeypatch.setenv("AI_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ISSUE", "x")
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    monkeypatch.setenv("HARNESS_TIER", "T0")
    cfg = C.load(["prog"])
    assert cfg.context_budget(8_000) == int(8_000 * 0.35)
    assert cfg.context_budget(8_000) < 3_000
    cfg.effective_tier = "T2"
    assert cfg.context_budget(200_000) == 150_000


def test_phase_budget_shares_sum_sanely(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ISSUE", "x")
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    cfg = C.load(["prog"])
    assert abs(sum(C.PHASE_SHARE.values()) - 1.0) < 1e-9
    assert cfg.phase_budget("P1") == int(cfg.token_budget * 0.35)


# -- T0.4 redaction (NFR-4) ------------------------------------------------
@pytest.mark.parametrize("secret", [
    "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv",
    "sk-or-v1-0123456789abcdef0123",
    "sk-proj-abcdefghijklmnop12345",
    "gsk_ABCDEFGHIJKLMNOP1234",
    "xai-ABCDEFGHIJKLMNOP1234",
    "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123",
])
def test_secrets_are_redacted(secret):
    out = redact(f"calling provider with {secret} now")
    assert secret not in out
    assert "***" in out


def test_logger_redacts_the_live_key():
    buf = io.StringIO()
    key = "sk-ant-api03-LIVEKEY1234567890"
    log = Logger(secrets=[key], stream=buf)
    log.line(f"auth header {key}")
    assert key not in buf.getvalue()


def test_logger_no_ansi_when_not_tty():
    buf = io.StringIO()
    log = Logger(stream=buf)
    log.phase("P1")
    log.line("hello")
    assert "\x1b[" not in buf.getvalue()
    assert "[P1 INVESTIGATE]" in buf.getvalue()


# -- T0.6 budgets ----------------------------------------------------------
def test_token_budget_charges_and_raises():
    tb = TokenBudget(total=1000)
    tb.charge("P1", 100, 50)
    assert tb.used == 150
    assert tb.per_phase["P1"]["calls"] == 1
    tb.charge("P1", 400, 0)
    with pytest.raises(BudgetExceeded) as exc:
        tb.check("P1", cap=500)
    assert exc.value.kind == "token"


def test_token_budget_reserve_is_withheld():
    tb = TokenBudget(total=1000, reserve_frac=0.12)
    assert tb.spendable == 880
    tb.charge("P3", 880, 0)
    with pytest.raises(BudgetExceeded):
        tb.check("P3", cap=10_000)


def test_cache_ratio():
    tb = TokenBudget(total=1000)
    tb.charge("P1", 100, 10, cached=300)
    assert 0.74 < tb.cache_ratio() < 0.76


def test_wall_clock_and_steps():
    wc = WallClock(limit_s=0.0)
    with pytest.raises(BudgetExceeded):
        wc.check("P1")
    sc = StepCounter()
    sc.cap("P1", 2)
    sc.step("P1")
    sc.step("P1")
    with pytest.raises(BudgetExceeded):
        sc.step("P1")


# -- T0.7 event log --------------------------------------------------------
def test_event_log_roundtrip(tmp_path):
    log = EventLog(tmp_path)
    for i in range(1000):
        log.append("tool_call", "P1", {"n": i}, summary=f"call {i}")
    assert len(log.events) == 1000
    read = EventLog.read(tmp_path / "trajectory.jsonl")
    assert len(read) == 1000
    assert read[0].i == 0 and read[-1].i == 999
    assert read[500].payload["n"] == 500


def test_large_payload_becomes_a_blob(tmp_path):
    log = EventLog(tmp_path)
    ev = log.append("tool_result", "P1", {"data": "x" * 9000})
    assert ev.payload_ref is not None
    assert ev.payload["_elided"] is True
    assert log.load_payload(ev)["data"].startswith("xxx")


def test_event_log_redacts_secrets(tmp_path):
    key = "sk-ant-api03-SECRETKEY0987654321"
    log = EventLog(tmp_path, secrets=[key])
    log.append("model_request", "P1", {"headers": {"x-api-key": key}})
    assert key not in (tmp_path / "trajectory.jsonl").read_text()


def test_unknown_event_kind_rejected(tmp_path):
    log = EventLog(tmp_path)
    with pytest.raises(ValueError):
        log.append("not_a_kind", "P1", {})
