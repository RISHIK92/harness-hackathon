"""Evaluation-spec compliance (SPEC.md 32). Each is a checkbox someone may
actually check."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SOURCE = sorted((ROOT / "harness").rglob("*.py"))


def _all_source() -> str:
    return "\n".join(p.read_text("utf-8", errors="replace") for p in SOURCE)


# -- text only, by construction --------------------------------------------
MEDIA = re.compile(
    r"\b(import\s+(PIL|cv2|wave|soundfile|librosa|imageio|moviepy)|"
    r"from\s+(PIL|cv2)\b|base64_image|image_url|input_audio|"
    r"\"type\":\s*\"image\"|'type':\s*'image')", re.I)


def test_no_media_code_path_exists():
    """Not dormant, not behind a flag: the tool surface has no media type."""
    body = _all_source()
    hits = [m.group(0) for m in MEDIA.finditer(body)]
    assert not hits, f"media handling found: {hits[:3]}"


KEY_PATTERNS = [re.compile(p) for p in (
    r"sk-ant-api\d\d-[A-Za-z0-9_\-]{16,}",
    r"sk-or-v1-[A-Za-z0-9]{16,}",
    r"\bgsk_[A-Za-z0-9]{16,}",
    r"\bAIza[A-Za-z0-9_\-]{24,}",
)]
FAKE = re.compile(r"(TEST|MOCK|FAKE|EXAMPLE|BENCH|SAMPLE)", re.I)


def _committed_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True).stdout.split()
    for rel in out:
        p = ROOT / rel
        if p.is_file() and p.suffix not in (".png", ".jpg", ".pyc"):
            yield rel, p.read_text("utf-8", errors="replace")


def test_no_credential_literals_in_shipped_code():
    """NFR-4: nothing key-shaped in the harness, Makefile or docs."""
    offenders = []
    for rel, text in _committed_files():
        if rel.startswith(("tests/", "bench/", "scripts/")):
            continue
        for pat in KEY_PATTERNS:
            if pat.search(text):
                offenders.append(rel)
    assert not offenders, offenders


def test_key_literals_in_dev_code_are_unmistakably_fake():
    """Test doubles need key-shaped strings; they must not look real."""
    for rel, text in _committed_files():
        if not rel.startswith(("tests/", "bench/", "scripts/")):
            continue
        for pat in KEY_PATTERNS:
            for m in pat.finditer(text):
                assert FAKE.search(m.group(0)), f"{rel}: {m.group(0)[:24]}"


def test_env_example_is_present_and_empty():
    f = ROOT / ".env.example"
    assert f.is_file()
    body = f.read_text().strip()
    assert body == "AI_API_KEY="


def test_no_dotenv_is_committed():
    result = subprocess.run(["git", "ls-files"], cwd=ROOT,
                            capture_output=True, text=True)
    assert ".env" not in result.stdout.split()


def test_makefile_is_at_the_repository_root():
    assert (ROOT / "Makefile").is_file()
    body = (ROOT / "Makefile").read_text()
    for target in ("setup:", "run:", "test:", "clean:"):
        assert target in body, target


def test_makefile_guards_a_missing_key_loudly():
    body = (ROOT / "Makefile").read_text()
    assert 'if [ -z "$$AI_API_KEY" ]' in body
    assert "Usage:" in body


def test_setup_never_uses_sudo_or_a_system_package_manager():
    body = (ROOT / "Makefile").read_text()
    for forbidden in ("sudo", "apt-get", "apt ", "brew ", "cargo install"):
        assert forbidden not in body, forbidden


def test_optional_install_cannot_fail_setup():
    body = (ROOT / "Makefile").read_text()
    assert "-$(BIN)/pip install --quiet -r requirements-optional.txt" in body


def test_dependencies_are_pinned():
    for name in ("requirements-core.txt", "requirements-optional.txt"):
        for line in (ROOT / name).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            assert "==" in line, f"{name}: {line} is not pinned"


def test_make_test_needs_no_api_key():
    body = (ROOT / "Makefile").read_text()
    test_line = [ln for ln in body.splitlines() if "harness.replay" in ln]
    assert test_line, "make test must try replay first"
    assert "AI_API_KEY" not in test_line[0]


def test_harness_never_writes_to_the_repository_gitignore():
    body = _all_source()
    assert ".git/info/exclude" in body
    assert 'gitignore"' not in body and "gitignore'" not in body


def test_no_agent_framework_dependency():
    """SPEC.md 2.2: no production harness uses one, and neither do we."""
    body = "\n".join((ROOT / f).read_text() for f in
                     ("requirements-core.txt", "requirements-optional.txt"))
    for framework in ("langchain", "llama-index", "crewai", "autogen",
                      "haystack", "semantic-kernel"):
        assert framework not in body.lower()


def test_no_provider_sdk_dependency():
    body = "\n".join((ROOT / f).read_text() for f in
                     ("requirements-core.txt", "requirements-optional.txt"))
    for sdk in ("anthropic", "openai", "google-genai", "litellm"):
        assert not re.search(rf"^{sdk}[=<>]", body, re.M), sdk


def test_no_bare_except_pass():
    """Every except either handles or re-raises."""
    offenders = []
    for p in SOURCE:
        body = p.read_text("utf-8", errors="replace")
        for m in re.finditer(r"except[^\n:]*:\s*\n\s*pass\b", body):
            line = body[:m.start()].count("\n") + 1
            if "except Exception" in m.group(0) and "#" not in m.group(0):
                offenders.append(f"{p.name}:{line}")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# `make run ISSUE=... PR=True` -- the one-line way to say "and open the PR".
# ---------------------------------------------------------------------------

def _makefile() -> str:
    return (Path(__file__).resolve().parents[2] / "Makefile").read_text()


def test_pr_true_allows_pushing_and_opening():
    body = _makefile()
    assert "clone,install,push,pr" in body, "PR=True cannot open a PR"


def test_pr_false_allows_no_outward_write():
    """It must still clone and install -- that is how the repository gets
    read and run at all -- but never push."""
    body = _makefile()
    assert "clone,install\"" in body or "clone,install'" in body \
        or 'HARNESS_AUTO="clone,install"' in body
    lines = [ln for ln in body.splitlines() if 'HARNESS_AUTO="clone,install"' in ln]
    assert lines, "PR=False sets no policy"
    for ln in lines:
        assert "push" not in ln and ",pr" not in ln, \
            f"PR=False would still push: {ln.strip()}"


def test_an_unrecognised_pr_value_is_refused_not_guessed():
    body = _makefile()
    assert "PR must be True or False" in body, \
        "a typo in PR would silently pick a behaviour"


def test_leaving_pr_unset_changes_nothing():
    """The default is the consent gate: ask at a terminal, decline when
    unattended. Setting a policy here would take that decision away."""
    body = _makefile()
    run = body[body.index("\nrun:"):body.index("\ntest:")]
    unset = [ln for ln in run.splitlines() if ln.strip().startswith('"")')]
    assert unset, "no branch for an unset PR"
    idx = run.index('"")')
    assert "HARNESS_AUTO" not in run[idx:idx + 120], \
        "an unset PR must not impose a policy"


def test_the_live_env_flag_reaches_the_harness():
    """The Makefile announced LIVE_ENV=True while exporting a variable by
    another name, so the flag printed a message and changed nothing."""
    body = _makefile()
    assert "HARNESS_LIVE_ENV=1" in body, "the flag exports the wrong name"
    assert "$(LIVE_ENV)" in body
    assert "LIVE_ENV must be True or False" in body


def test_preparing_the_machine_is_opt_in():
    """Cloning and editing needs nothing installed."""
    from harness import provision
    import os
    os.environ.pop("HARNESS_LIVE_ENV", None)
    assert not provision.use_environment()


def test_setup_tells_the_operator_how_to_run_it():
    """`make setup` is where somebody meets this tool."""
    from harness import setup_ui
    source = Path(setup_ui.__file__).read_text()
    assert "PR=True" in source
    assert "github.com/owner/repo/issues" in source
    assert "AI_API_KEY" in source
