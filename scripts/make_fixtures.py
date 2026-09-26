#!/usr/bin/env python3
"""Generate the fixture repositories (SPEC.md 13.1 / IMPLEMENTATION.md 13.1).

Each fixture is a real git repo with a seeded bug, a test suite, and a
reference patch.  Regenerate with:  python3 scripts/make_fixtures.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                        "HOME": str(repo),
                        "GIT_AUTHOR_NAME": "fixture",
                        "GIT_AUTHOR_EMAIL": "f@x",
                        "GIT_COMMITTER_NAME": "fixture",
                        "GIT_COMMITTER_EMAIL": "f@x"})


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.lstrip("\n"), encoding="utf-8")


def make(name: str, files: dict, meta: dict, commits: list | None = None) -> Path:
    repo = FIX / name
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")

    if commits:
        for message, batch in commits:
            for rel, text in batch.items():
                write(repo, rel, text)
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", message)
    files.setdefault("pytest.ini", PYTEST_INI)
    for rel, text in files.items():
        write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", meta.get("commit", "initial commit"))

    (FIX / name / ".fixture.json").write_text(json.dumps(meta, indent=2))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add fixture metadata")
    return repo


PYTEST_INI = """
[pytest]
testpaths = tests
"""

PYPROJECT = """
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "0.1.0"

[tool.ruff]
line-length = 88
"""

# ---------------------------------------------------------------- fixtures
def f_offbyone():
    make("py-offbyone", {
        "pyproject.toml": PYPROJECT.format(name="dateparse"),
        "src/dateparse/__init__.py": "from .parser import parse_date\n",
        "src/dateparse/parser.py": '''
"""Date parsing helpers."""


def normalize(raw):
    """Strip whitespace and lowercase a raw field."""
    return raw.strip().lower()


def parse_date(text):
    """Parse YYYY-MM-DD into a (year, month, day) tuple."""
    parts = text.split("-")
    return int(parts[0]), int(parts[1]), int(parts[2])


def format_date(parts):
    """Render a (year, month, day) tuple back to YYYY-MM-DD."""
    return "%04d-%02d-%02d" % parts
''',
        "tests/test_parser.py": '''
from dateparse.parser import format_date, normalize, parse_date


def test_normalize():
    assert normalize("  ABC ") == "abc"


def test_parse_date():
    assert parse_date("2026-01-31") == (2026, 1, 31)


def test_format_date():
    assert format_date((2026, 1, 31)) == "2026-01-31"


def test_parse_date_without_separator():
    """A date with no separator should raise ValueError, not IndexError."""
    import pytest
    with pytest.raises(ValueError):
        parse_date("20260131")
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
    }, {
        "id": "py-offbyone",
        "issue": "parse_date crashes with IndexError when the input has no "
                 "separator.\n\nRepro:\n    parse_date(\"20260131\")\n\n"
                 "Expected a ValueError with a clear message; got "
                 "IndexError: list index out of range.",
        "task_type": "BUG_FIX",
        "bug_class": "logic",
        "target_file": "src/dateparse/parser.py",
        "failing_tests": ["tests/test_parser.py::test_parse_date_without_separator"],
        "reference_diff_lines": 4,
        "success": ["tests_pass", "no_new_failures", "diff_lines <= 15"],
    })


def f_none_guard():
    make("py-none-guard", {
        "pyproject.toml": PYPROJECT.format(name="usersvc"),
        "src/usersvc/__init__.py": "",
        "src/usersvc/profile.py": '''
"""User profile helpers."""


def load_profile(store, user_id):
    """Return the stored profile dict, or None when absent."""
    return store.get(user_id)


def display_name(store, user_id):
    """Return a human-readable name for the given user."""
    profile = load_profile(store, user_id)
    return profile["name"].title()


def initials(store, user_id):
    """Return the user's initials."""
    name = display_name(store, user_id)
    return "".join(part[0] for part in name.split())
''',
        "tests/test_profile.py": '''
from usersvc.profile import display_name, initials, load_profile

STORE = {"u1": {"name": "ada lovelace"}}


def test_load_profile():
    assert load_profile(STORE, "u1")["name"] == "ada lovelace"


def test_display_name():
    assert display_name(STORE, "u1") == "Ada Lovelace"


def test_initials():
    assert initials(STORE, "u1") == "AL"


def test_display_name_for_unknown_user():
    """An unknown user should yield an empty string, not a TypeError."""
    assert display_name(STORE, "nope") == ""
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
    }, {
        "id": "py-none-guard",
        "issue": "display_name() raises TypeError when the user id is not in "
                 "the store.\n\nTypeError: 'NoneType' object is not subscriptable\n"
                 "  File \"src/usersvc/profile.py\", line 12, in display_name\n"
                 "    return profile[\"name\"].title()",
        "task_type": "BUG_FIX",
        "bug_class": "null",
        "target_file": "src/usersvc/profile.py",
        "failing_tests": ["tests/test_profile.py::test_display_name_for_unknown_user"],
        "reference_diff_lines": 4,
        "success": ["tests_pass", "no_new_failures", "diff_lines <= 12"],
    })


def f_upstream():
    make("py-upstream", {
        "pyproject.toml": PYPROJECT.format(name="importer"),
        "src/importer/__init__.py": "",
        "src/importer/rows.py": '''
"""CSV row handling."""


def split_fields(line):
    """Split a CSV line into fields."""
    return line.split(",")


def normalize_row(line):
    """Normalize a raw CSV line into a list of trimmed fields."""
    fields = split_fields(line)
    return [f.strip() for f in fields]


def import_csv(lines):
    """Import CSV lines into a list of (name, qty) pairs."""
    out = []
    for line in lines:
        name, qty = normalize_row(line)
        out.append((name, int(qty)))
    return out
''',
        "tests/test_rows.py": '''
from importer.rows import import_csv, normalize_row, split_fields


def test_split_fields():
    assert split_fields("a,b") == ["a", "b"]


def test_normalize_row():
    assert normalize_row(" a , b ") == ["a", "b"]


def test_import_csv():
    assert import_csv(["widget, 3"]) == [("widget", 3)]


def test_import_csv_ignores_trailing_blank_line():
    """A trailing empty line should be skipped, not crash the import."""
    assert import_csv(["widget, 3", ""]) == [("widget", 3)]
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
    }, {
        "id": "py-upstream",
        "issue": "Importing a CSV file that ends with a blank line fails.\n\n"
                 "ValueError: not enough values to unpack (expected 2, got 1)\n"
                 "  File \"src/importer/rows.py\", line 19, in import_csv\n"
                 "    name, qty = normalize_row(line)\n\n"
                 "The error surfaces in import_csv but the empty line is "
                 "produced further up.",
        "task_type": "BUG_FIX",
        "bug_class": "missing_case",
        "target_file": "src/importer/rows.py",
        "failing_tests": ["tests/test_rows.py::test_import_csv_ignores_trailing_blank_line"],
        "reference_diff_lines": 3,
        "success": ["tests_pass", "no_new_failures", "diff_lines <= 12"],
    })


def f_red_baseline():
    make("py-red-baseline", {
        "pyproject.toml": PYPROJECT.format(name="cart"),
        "src/cart/__init__.py": "",
        "src/cart/totals.py": '''
"""Shopping cart totals."""


def subtotal(items):
    """Sum the line totals of every item."""
    return sum(i["price"] * i["qty"] for i in items)


def apply_discount(total, percent):
    """Apply a percentage discount to a total."""
    return total - (total * percent / 100)


def grand_total(items, percent=0):
    """Subtotal with an optional percentage discount applied."""
    return apply_discount(subtotal(items), percent)
''',
        "src/cart/tax.py": '''
"""Tax helpers. Known broken: the DST and VAT cases are open bugs."""


def vat(total, rate):
    return total * rate


def dst_adjust(total):
    raise NotImplementedError("pending tax rules")
''',
        "tests/test_totals.py": '''
from cart.totals import apply_discount, grand_total, subtotal

ITEMS = [{"price": 10, "qty": 2}, {"price": 5, "qty": 1}]


def test_subtotal():
    assert subtotal(ITEMS) == 25


def test_apply_discount():
    assert apply_discount(100, 10) == 90


def test_grand_total_with_discount():
    assert grand_total(ITEMS, 20) == 20


def test_grand_total_rejects_negative_discount():
    """A negative discount must raise ValueError, not inflate the total."""
    import pytest
    with pytest.raises(ValueError):
        grand_total(ITEMS, -50)
''',
        "tests/test_tax.py": '''
"""These two have been failing on main for weeks. Do not fix them here."""
from cart.tax import dst_adjust, vat


def test_vat_rounds_to_cents():
    assert vat(10.005, 0.2) == 2.0


def test_dst_boundary():
    assert dst_adjust(100) == 100
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
    }, {
        "id": "py-red-baseline",
        "issue": "grand_total() accepts a negative discount and returns a "
                 "total larger than the subtotal.\n\n"
                 "grand_total(items, -50) should raise ValueError.",
        "task_type": "BUG_FIX",
        "bug_class": "logic",
        "target_file": "src/cart/totals.py",
        "failing_tests": ["tests/test_totals.py::test_grand_total_rejects_negative_discount"],
        "pre_existing_failures": ["tests/test_tax.py::test_vat_rounds_to_cents",
                                  "tests/test_tax.py::test_dst_boundary"],
        "reference_diff_lines": 4,
        "success": ["tests_pass", "no_new_failures", "pre_existing_untouched"],
    })


def f_lint_debt():
    """Target bug plus 40 pre-existing lint errors: the gate must not deadlock."""
    debt_lines = []
    for i in range(40):
        debt_lines.append(f"import os as _unused_{i}  # noqa-free deliberate debt")
    debt = "\n".join(debt_lines)
    make("py-lint-debt", {
        "pyproject.toml": PYPROJECT.format(name="legacy"),
        "src/legacy/__init__.py": "",
        "src/legacy/debt.py": debt + "\n\n\ndef untouched():\n    return 1\n",
        "src/legacy/calc.py": '''
"""Percentage helpers."""


def ratio(part, whole):
    """Return part/whole as a ratio."""
    return part / whole


def percentage(part, whole):
    """Return part/whole as a percentage."""
    return ratio(part, whole) * 100
''',
        "tests/test_calc.py": '''
from legacy.calc import percentage, ratio


def test_ratio():
    assert ratio(1, 4) == 0.25


def test_percentage():
    assert percentage(1, 4) == 25


def test_percentage_of_zero_whole():
    """Dividing by zero should give 0.0, not ZeroDivisionError."""
    assert percentage(0, 0) == 0.0
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
    }, {
        "id": "py-lint-debt",
        "issue": "percentage(0, 0) raises ZeroDivisionError. It should return "
                 "0.0 when the whole is zero.",
        "task_type": "BUG_FIX",
        "bug_class": "logic",
        "target_file": "src/legacy/calc.py",
        "failing_tests": ["tests/test_calc.py::test_percentage_of_zero_whole"],
        "pre_existing_lint": 40,
        "reference_diff_lines": 4,
        "success": ["tests_pass", "no_new_failures", "lint_gate_did_not_block"],
    })


def f_flaky():
    make("py-flaky", {
        "pyproject.toml": PYPROJECT.format(name="sched"),
        "src/sched/__init__.py": "",
        "src/sched/queue.py": '''
"""A tiny priority queue."""


def push(queue, item, priority):
    """Insert an item at its priority position."""
    queue.append((priority, item))
    queue.sort(key=lambda pair: pair[0])
    return queue


def pop(queue):
    """Remove and return the highest-priority item."""
    return queue.pop(0)[1]
''',
        "tests/test_queue.py": '''
from sched.queue import pop, push


def test_push_orders_by_priority():
    q = []
    push(q, "b", 2)
    push(q, "a", 1)
    assert q[0][1] == "a"


def test_pop_returns_highest_priority():
    q = []
    push(q, "b", 2)
    push(q, "a", 1)
    assert pop(q) == "a"


def test_pop_on_empty_queue():
    """Popping an empty queue should return None, not IndexError."""
    assert pop([]) is None
''',
        "tests/test_timing.py": '''
"""Timing-sensitive test that fails intermittently."""
import time


def test_tick_is_fast():
    start = time.perf_counter()
    time.sleep(0.001)
    # Deliberately tight: passes or fails depending on scheduler jitter.
    assert time.perf_counter() - start < 0.0011
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
    }, {
        "id": "py-flaky",
        "issue": "pop() raises IndexError on an empty queue. It should return "
                 "None instead.",
        "task_type": "BUG_FIX",
        "bug_class": "logic",
        "target_file": "src/sched/queue.py",
        "failing_tests": ["tests/test_queue.py::test_pop_on_empty_queue"],
        "flaky_tests": ["tests/test_timing.py::test_tick_is_fast"],
        "reference_diff_lines": 4,
        "success": ["tests_pass", "no_new_failures", "flake_not_blocking"],
    })


def f_env_missing():
    make("py-env-missing", {
        "pyproject.toml": PYPROJECT.format(name="mailer"),
        "src/mailer/__init__.py": "",
        "src/mailer/client.py": '''
"""Outbound mail client."""
import os


def api_endpoint():
    """Return the configured mail API endpoint."""
    return os.environ["MAILER_ENDPOINT"]


def send(message):
    """Send a message through the configured endpoint."""
    return {"endpoint": api_endpoint(), "body": message}
''',
        "tests/test_client.py": '''
import os

import pytest

from mailer.client import api_endpoint, send


def test_api_endpoint():
    assert api_endpoint().startswith("https://")


def test_send():
    assert send("hi")["body"] == "hi"
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
        "README.md": "Set MAILER_ENDPOINT before running the suite.\n",
    }, {
        "id": "py-env-missing",
        "issue": "The mailer test suite fails with KeyError: 'MAILER_ENDPOINT' "
                 "on a fresh checkout.",
        "task_type": "CONFIG",
        "bug_class": "config",
        "external_factor": "MAILER_ENDPOINT is not set",
        "target_file": None,
        "failing_tests": ["tests/test_client.py::test_api_endpoint",
                          "tests/test_client.py::test_send"],
        "reference_diff_lines": 0,
        "success": ["classified_as_config", "no_speculative_code_change"],
    })


def f_cross_caller():
    make("py-cross-caller", {
        "pyproject.toml": PYPROJECT.format(name="geo"),
        "src/geo/__init__.py": "",
        "src/geo/distance.py": '''
"""Distance helpers."""


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres."""
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))
''',
        "src/geo/routes.py": '''
"""Route helpers that call into distance."""
from .distance import haversine


def leg_length(a, b):
    return haversine(a[0], a[1], b[0], b[1])


def route_length(points):
    return sum(leg_length(points[i], points[i + 1])
               for i in range(len(points) - 1))
''',
        "src/geo/nearest.py": '''
"""Nearest-neighbour search."""
from .distance import haversine


def nearest(origin, candidates):
    return min(candidates,
               key=lambda c: haversine(origin[0], origin[1], c[0], c[1]))
''',
        "src/geo/report.py": '''
"""Reporting helpers."""
from .distance import haversine


def summary(a, b):
    km = haversine(a[0], a[1], b[0], b[1])
    return "%.1f km" % km
''',
        "tests/test_distance.py": '''
from geo.distance import haversine
from geo.nearest import nearest
from geo.report import summary
from geo.routes import route_length

LON = (51.5, -0.12)
PAR = (48.85, 2.35)


def test_haversine():
    assert 330 < haversine(LON[0], LON[1], PAR[0], PAR[1]) < 350


def test_route_length():
    assert 330 < route_length([LON, PAR]) < 350


def test_nearest():
    assert nearest(LON, [PAR, (55.9, -3.2)]) == PAR


def test_summary():
    assert summary(LON, PAR).endswith(" km")


def test_haversine_accepts_miles():
    """haversine should support a unit argument."""
    miles = haversine(LON[0], LON[1], PAR[0], PAR[1], unit="mi")
    assert 200 < miles < 220
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
    }, {
        "id": "py-cross-caller",
        "issue": "haversine() should accept a unit argument (\"km\" default, "
                 "\"mi\" supported) so callers can request miles.",
        "task_type": "FEATURE",
        "bug_class": "logic",
        "target_file": "src/geo/distance.py",
        "failing_tests": ["tests/test_distance.py::test_haversine_accepts_miles"],
        "callers": ["src/geo/routes.py", "src/geo/nearest.py",
                    "src/geo/report.py"],
        "reference_diff_lines": 8,
        "success": ["tests_pass", "no_new_failures", "callers_identified"],
    })


def f_vague():
    make("py-vague", {
        "pyproject.toml": PYPROJECT.format(name="slugify"),
        "src/slugify/__init__.py": "",
        "src/slugify/core.py": '''
"""URL slug helpers."""
import re

SEPARATOR = "-"


def strip_accents(text):
    """Remove combining marks from the text."""
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def slugify(text):
    """Convert arbitrary text into a URL slug."""
    text = strip_accents(text).lower()
    text = re.sub(r"[^a-z0-9]+", SEPARATOR, text)
    return text.strip(SEPARATOR)


def unique_slug(text, existing):
    """Return a slug not already present in `existing`."""
    base = slugify(text)
    slug = base
    n = 1
    while slug in existing:
        slug = base + SEPARATOR + str(n)
    return slug
''',
        "tests/test_core.py": '''
from slugify.core import slugify, strip_accents, unique_slug


def test_strip_accents():
    assert strip_accents("café") == "cafe"


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"


def test_unique_slug_when_free():
    assert unique_slug("Hello", set()) == "hello"
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
    }, {
        "id": "py-vague",
        "issue": "Sometimes saving a second post with the same title just hangs.",
        "task_type": "BUG_FIX",
        "bug_class": "logic",
        "target_file": "src/slugify/core.py",
        "failing_tests": [],
        "green_baseline": True,
        "reference_diff_lines": 2,
        "success": ["tests_pass", "no_new_failures", "diff_lines <= 10"],
    })


def f_dep_bump():
    make("py-dep-bump", {
        "pyproject.toml": PYPROJECT.format(name="httpwrap") +
        '\ndependencies = ["requests>=2.0"]\n',
        "requirements.lock": "requests==1.9.0\nurllib3==1.26.0\n",
        "src/httpwrap/__init__.py": "",
        "src/httpwrap/client.py": '''
"""Thin HTTP wrapper."""


def get_json(session, url):
    """Fetch JSON, raising on error status."""
    response = session.get(url, timeout=10)
    response.raise_for_status()
    return response.json()
''',
        "tests/test_client.py": '''
from httpwrap.client import get_json


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


class FakeSession:
    def get(self, url, timeout=None):
        return FakeResponse()


def test_get_json():
    assert get_json(FakeSession(), "http://x")["ok"] is True
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
    }, {
        "id": "py-dep-bump",
        "issue": "get_json() raises TypeError: get() got an unexpected keyword "
                 "argument 'timeout' in production, but the tests pass locally.",
        "task_type": "DEPENDENCY",
        "bug_class": "external",
        "external_factor": "requirements.lock pins requests==1.9.0 while "
                           "pyproject declares requests>=2.0",
        "target_file": None,
        "failing_tests": [],
        "reference_diff_lines": 1,
        "success": ["classified_as_external", "no_speculative_code_change"],
    })


def f_regression():
    """Bug introduced by an identifiable commit -- the bisect fixture."""
    base = {
        "pyproject.toml": PYPROJECT.format(name="pricing"),
        "src/pricing/__init__.py": "",
        "src/pricing/rules.py": '''
"""Pricing rules."""


def unit_price(base, qty):
    """Return the per-unit price after volume discounts."""
    if qty >= 100:
        return base * 0.9
    return base


def total(base, qty):
    """Return the order total."""
    return unit_price(base, qty) * qty
''',
        "tests/test_rules.py": '''
from pricing.rules import total, unit_price


def test_unit_price_small_order():
    assert unit_price(10, 1) == 10


def test_unit_price_volume_discount():
    assert unit_price(10, 100) == 9


def test_total():
    assert total(10, 2) == 20
''',
        "conftest.py": 'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))\n',
    }
    noise = dict(base)
    noise["README.md"] = "Pricing rules.\n"
    broken = dict(base)
    broken["src/pricing/rules.py"] = '''
"""Pricing rules."""


def unit_price(base, qty):
    """Return the per-unit price after volume discounts."""
    if qty > 100:
        return base * 0.9
    return base


def total(base, qty):
    """Return the order total."""
    return unit_price(base, qty) * qty
'''
    make("py-regression", broken, {
        "id": "py-regression",
        "issue": "Volume discount stopped applying at exactly 100 units. "
                 "It used to work; something changed recently.",
        "task_type": "BUG_FIX",
        "bug_class": "logic",
        "target_file": "src/pricing/rules.py",
        "failing_tests": ["tests/test_rules.py::test_unit_price_volume_discount"],
        "regression": True,
        "reference_diff_lines": 2,
        "success": ["tests_pass", "no_new_failures", "commit_identified"],
        "commit": "tighten the volume-discount boundary",
    }, commits=[
        ("initial pricing rules", base),
        ("add readme", noise),
    ])


ALL = [f_offbyone, f_none_guard, f_upstream, f_red_baseline, f_lint_debt,
       f_flaky, f_env_missing, f_cross_caller, f_vague, f_dep_bump,
       f_regression]


def main() -> int:
    FIX.mkdir(parents=True, exist_ok=True)
    for fn in ALL:
        fn()
        print(f"  built {fn.__name__[2:]}")
    print(f"{len(ALL)} fixtures in {FIX}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
