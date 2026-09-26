"""T1.2/T1.5/T1.6/T1.7/T1.8/T1.9: discovery, adapters, gateway, router, probe.

All offline: harness.model.http.request is monkeypatched.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness import config as C
from harness.logging_ui import Logger
from harness.model import bootstrap as boot
from harness.model import discover as disc_mod
from harness.model import http as http_mod
from harness.model import probe as probe_mod
from harness.model.adapters import anthropic as anth
from harness.model.adapters import openai_compat as oai
from harness.model.detect import PROVIDERS
from harness.model.gateway import Gateway, ModelSpec
from harness.model.router import Router
from harness.model.types import ProviderError


def make_cfg(tmp_path, **kw):
    cfg = C.Config(api_key=kw.pop("api_key", "sk-ant-api03-TESTKEY1234567890"),
                   issue="boom", repo_path=tmp_path)
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def silent_log():
    return Logger(stream=io.StringIO())


# -- T1.2 discovery + chat filter -----------------------------------------
def test_discovery_filters_non_chat(monkeypatch, tmp_path):
    def fake(method, url, headers, body=None, timeout=60.0):
        return {"data": [{"id": "gpt-5"}, {"id": "dall-e-3"},
                         {"id": "text-embedding-3-small"},
                         {"id": "gpt-4o-mini", "context_length": 128000}]}

    monkeypatch.setattr(http_mod, "request", fake)
    monkeypatch.setattr(oai, "request", fake)
    d = disc_mod.discover(PROVIDERS["openai"], "sk-proj-FAKEx", use_cache=False)
    assert d.ok
    assert d.ids == ["gpt-5", "gpt-4o-mini"]
    assert d.raw_count == 4
    assert d.ctx["gpt-4o-mini"] == 128000


def test_discovery_degrades_on_failure(monkeypatch):
    def boom(*a, **k):
        raise ProviderError("HTTP 503: down", status=503)

    monkeypatch.setattr(oai, "request", boom)
    d = disc_mod.discover(PROVIDERS["openai"], "sk-proj-FAKEx", use_cache=False)
    assert not d.ok and d.degraded
    assert "503" in d.error


def test_anthropic_models_endpoint_shape(monkeypatch):
    seen = {}

    def fake(method, url, headers, body=None, timeout=60.0):
        seen["url"] = url
        seen["headers"] = headers
        return {"data": [{"id": "claude-opus-5-5"}, {"id": "claude-haiku-4-5"}]}

    monkeypatch.setattr(anth, "request", fake)
    d = disc_mod.discover(PROVIDERS["anthropic"], "sk-ant-FAKEkey", use_cache=False)
    assert d.ids == ["claude-opus-5-5", "claude-haiku-4-5"]
    assert seen["url"].endswith("/v1/models")
    assert seen["headers"]["x-api-key"] == "sk-ant-FAKEkey"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"


# -- T1.5 adapters ---------------------------------------------------------
def test_openai_adapter_normalizes(monkeypatch):
    def fake(method, url, headers, body=None, timeout=60.0):
        assert url.endswith("/chat/completions")
        assert headers["Authorization"].startswith("Bearer ")
        return {"model": "gpt-5", "choices": [{
            "message": {"content": "hello", "tool_calls": [
                {"id": "c1", "function": {"name": "echo",
                                          "arguments": '{"text":"ping"}'}}]},
            "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3,
                      "prompt_tokens_details": {"cached_tokens": 8}}}

    monkeypatch.setattr(oai, "request", fake)
    r = oai.chat("https://api.openai.com/v1", "sk-proj-FAKEx", "gpt-5",
                 [{"role": "user", "content": "hi"}])
    assert r.text == "hello"
    assert r.tool_calls[0].name == "echo"
    assert r.tool_calls[0].args == {"text": "ping"}
    assert (r.tokens_in, r.tokens_out, r.tokens_cached) == (12, 3, 8)


def test_anthropic_adapter_splits_system_and_marks_cache(monkeypatch):
    captured = {}

    def fake(method, url, headers, body=None, timeout=60.0):
        captured["body"] = body
        return {"model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 20, "output_tokens": 5,
                          "cache_read_input_tokens": 15}}

    monkeypatch.setattr(anth, "request", fake)
    r = anth.chat("https://api.anthropic.com", "sk-ant-x", "claude-opus-5-5",
                  [{"role": "system", "content": "sys1"},
                   {"role": "system", "content": "sys2"},
                   {"role": "user", "content": "hi"}],
                  cache_prefix=1)
    assert r.text == "ok" and r.tokens_cached == 15
    body = captured["body"]
    assert len(body["system"]) == 2
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in body["system"][1]
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_openai_malformed_tool_args_do_not_crash(monkeypatch):
    def fake(*a, **k):
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"function": {"name": "f", "arguments": "not json"}}]}}],
            "usage": {}}

    monkeypatch.setattr(oai, "request", fake)
    r = oai.chat("http://x/v1", "k", "m", [])
    assert r.tool_calls[0].args == {"_raw": "not json"}


# -- T1.6 gateway ----------------------------------------------------------
def test_gateway_retries_then_succeeds(monkeypatch, tmp_path):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 4:
            raise ProviderError("HTTP 500", status=500)
        return {"choices": [{"message": {"content": "done"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(oai, "request", flaky)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cfg = make_cfg(tmp_path, no_cache=True)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log())
    r = gw.call([{"role": "user", "content": "x"}], "gpt-5")
    assert r.text == "done" and calls["n"] == 4


def test_gateway_does_not_retry_4xx(monkeypatch, tmp_path):
    calls = {"n": 0}

    def bad(*a, **k):
        calls["n"] += 1
        raise ProviderError("HTTP 401", status=401)

    monkeypatch.setattr(oai, "request", bad)
    cfg = make_cfg(tmp_path, no_cache=True)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log())
    with pytest.raises(ProviderError):
        gw.call([{"role": "user", "content": "x"}], "gpt-5")
    assert calls["n"] == 1


def test_gateway_fails_over_primary_to_cheap(monkeypatch, tmp_path):
    seen = []

    def fake(method, url, headers, body=None, timeout=60.0):
        seen.append(body["model"])
        if body["model"] == "big":
            raise ProviderError("HTTP 500", status=500)
        return {"choices": [{"message": {"content": "small says hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(oai, "request", fake)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cfg = make_cfg(tmp_path, no_cache=True)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log())
    gw.primary = ModelSpec("big", "T2", 200000)
    gw.cheap = ModelSpec("small", "T0", 100000)
    r = gw.call([{"role": "user", "content": "x"}], "big")
    assert r.text == "small says hi"
    assert "small" in seen


def test_gateway_cache_avoids_second_call(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return {"choices": [{"message": {"content": "cached me"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

    monkeypatch.setattr(oai, "request", fake)
    cfg = make_cfg(tmp_path)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log())
    msgs = [{"role": "user", "content": "same"}]
    a = gw.call(msgs, "gpt-5")
    b = gw.call(msgs, "gpt-5")
    assert calls["n"] == 1
    assert a.text == b.text == "cached me"


def test_gateway_charges_budget(monkeypatch, tmp_path):
    from harness.budget import Budgets

    def fake(*a, **k):
        return {"choices": [{"message": {"content": "x"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}

    monkeypatch.setattr(oai, "request", fake)
    cfg = make_cfg(tmp_path, no_cache=True)
    budgets = Budgets.from_config(cfg)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log(),
                 budgets=budgets)
    gw.call([{"role": "user", "content": "x"}], "gpt-5", phase="P1")
    assert budgets.tokens.used == 120
    assert budgets.tokens.per_phase["P1"]["calls"] == 1


# -- T1.7 router -----------------------------------------------------------
def test_router_sends_judges_to_cheap_model(monkeypatch, tmp_path):
    used = []

    def fake(method, url, headers, body=None, timeout=60.0):
        used.append(body["model"])
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(oai, "request", fake)
    cfg = make_cfg(tmp_path, no_cache=True)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log())
    gw.primary = ModelSpec("big", "T2", 200000)
    gw.cheap = ModelSpec("small", "T0", 100000)
    r = Router(gw, cfg, silent_log())
    msgs = [{"role": "user", "content": "x"}]
    r.call("p1_synthesis", msgs, "P1")
    r.call("p4_judge_diff", msgs, "P4")
    r.call("p4_judge_practices", msgs, "P4")
    r.call("p3_implement", msgs, "P3")
    assert used == ["big", "small", "small", "big"]


def test_router_single_model_clamps_cheap_calls(monkeypatch, tmp_path):
    caps = []

    def fake(method, url, headers, body=None, timeout=60.0):
        caps.append(body["max_tokens"])
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(oai, "request", fake)
    cfg = make_cfg(tmp_path, no_cache=True)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log())
    gw.primary = gw.cheap = ModelSpec("only", "T1", 32000)
    r = Router(gw, cfg, silent_log())
    assert r.single_model
    r.call("p3_implement", [{"role": "user", "content": "x"}], "P3",
           max_tokens=4000)
    r.call("p4_judge_diff", [{"role": "user", "content": "x"}], "P4",
           max_tokens=4000)
    assert caps == [4000, 1000]


def test_router_samples_n_replies(monkeypatch, tmp_path):
    def fake(*a, **k):
        return {"choices": [{"message": {"content": "s"}}], "usage": {}}

    monkeypatch.setattr(oai, "request", fake)
    cfg = make_cfg(tmp_path, no_cache=True)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log())
    gw.primary = gw.cheap = ModelSpec("m", "T0", 32000)
    r = Router(gw, cfg, silent_log())
    out = r.call("p1_localize", [{"role": "user", "content": "x"}], "P1",
                 samples=5)
    assert isinstance(out, list) and len(out) == 5


# -- T1.9 probe ------------------------------------------------------------
def test_probe_two_attempts_before_declaring_incapable(monkeypatch, tmp_path):
    """One malformed reply is not proof of incapability."""
    n = {"i": 0}

    def fake(method, url, headers, body=None, timeout=60.0):
        n["i"] += 1
        if n["i"] == 1:
            return {"choices": [{"message": {"content": "sorry"}}], "usage": {}}
        return {"choices": [{"message": {"content": '{"ok": true, "n": 2}',
                "tool_calls": [{"function": {"name": "echo",
                                "arguments": '{"text":"ping"}'}}]}}],
                "usage": {}}

    monkeypatch.setattr(oai, "request", fake)
    cfg = make_cfg(tmp_path, no_cache=True)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log())
    caps = probe_mod.probe(gw, "m")
    assert n["i"] == 2
    assert caps.tool_calling and caps.strict_json


def test_probe_failure_assumes_safe_path(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise ProviderError("HTTP 500", status=500)

    monkeypatch.setattr(oai, "request", boom)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cfg = make_cfg(tmp_path, no_cache=True)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log())
    caps = probe_mod.probe(gw, "m")
    assert caps.tool_calling is False and caps.text_protocol is True
    assert caps.strict_json is False


def test_probe_extracts_json_from_prose(monkeypatch, tmp_path):
    def fake(*a, **k):
        return {"choices": [{"message": {
            "content": 'Sure! Here you go: {"ok": true, "n": 2} hope that helps'
        }}], "usage": {}}

    monkeypatch.setattr(oai, "request", fake)
    cfg = make_cfg(tmp_path, no_cache=True)
    gw = Gateway(PROVIDERS["openai"], "sk-proj-FAKEx", cfg, silent_log())
    caps = probe_mod.probe(gw, "m", attempts=1)
    assert caps.strict_json is True
    assert caps.tool_calling is False


# -- T1.8 startup block ----------------------------------------------------
def test_startup_block_contents(monkeypatch, tmp_path):
    def fake(method, url, headers, body=None, timeout=60.0):
        return {"data": [{"id": "claude-opus-5-5"},
                         {"id": "claude-sonnet-5"},
                         {"id": "claude-haiku-4-5"}]}

    monkeypatch.setattr(anth, "request", fake)
    buf = io.StringIO()
    log = Logger(stream=buf)
    cfg = make_cfg(tmp_path, no_cache=True)
    bs = boot.bring_up(cfg, log)
    boot.print_startup(bs, cfg, log, {"path": str(tmp_path),
                                      "language": "python",
                                      "test_cmd": "pytest -q"})
    out = buf.getvalue()
    assert "provider        anthropic" in out
    assert "key prefix sk-ant-api" in out
    assert "claude-opus-5-5" in out
    assert "tier T2" in out
    assert "cheap model     claude-haiku-4-5" in out
    assert "tier profile    T2" in out
    assert "test command    pytest -q" in out
    assert cfg.effective_tier == "T2"


def test_startup_survives_discovery_failure(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise ProviderError("HTTP 503", status=503)

    monkeypatch.setattr(anth, "request", boom)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    buf = io.StringIO()
    cfg = make_cfg(tmp_path, no_cache=True, model="claude-opus-5-5")
    bs = boot.bring_up(cfg, Logger(stream=buf))
    assert "no_discovery" in bs.degraded
    assert bs.primary.id == "claude-opus-5-5"


def test_no_chat_model_is_config_error(monkeypatch, tmp_path):
    def fake(*a, **k):
        return {"data": [{"id": "dall-e-3"}, {"id": "whisper-1"}]}

    monkeypatch.setattr(oai, "request", fake)
    cfg = make_cfg(tmp_path, api_key="sk-proj-abcdefghij123456", no_cache=True)
    with pytest.raises(C.ConfigError, match="no chat-capable"):
        boot.bring_up(cfg, Logger(stream=io.StringIO()))


def test_tier_override_wins(monkeypatch, tmp_path):
    def fake(*a, **k):
        return {"data": [{"id": "claude-opus-5-5"}, {"id": "claude-haiku-4-5"}]}

    monkeypatch.setattr(anth, "request", fake)
    cfg = make_cfg(tmp_path, no_cache=True, tier="T0")
    bs = boot.bring_up(cfg, Logger(stream=io.StringIO()))
    assert bs.primary.tier == "T0"
    assert cfg.effective_tier == "T0"
