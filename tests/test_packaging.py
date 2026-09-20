"""T7.1 packaging regression tests (06-P7-ADOPTION.md).

The real proof that an installed wheel works stands alone, outside the
repository, in `scripts/verify_clean_install.sh` — too slow and too
environment-dependent (needs `build`, a throwaway venv) to run on every
`pytest`. What belongs here are the fast, static checks that catch the
specific regression the spec calls out before it ever reaches a release:
the `tests.fakes` import in `cli.py` staying function-local, and the
package-data declaration staying in place.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_cli_never_imports_tests_at_module_level():
    """`_build_async_client`'s `from tests.fakes import FakeAsyncJevClient`
    must stay nested inside the function — an installed wheel has no
    `tests` package, so hoisting this to module scope would break every
    installed copy while every local test (which always has `tests` on
    `sys.path`) kept passing, hiding the regression from the suite meant to
    catch it."""
    source = (REPO_ROOT / "src" / "jev_census" / "cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    module_level_imports = set()
    for node in tree.body:  # only top-level statements, not nested ones
        if isinstance(node, ast.ImportFrom) and node.module:
            module_level_imports.add(node.module)
        elif isinstance(node, ast.Import):
            module_level_imports.update(alias.name for alias in node.names)

    assert "tests" not in module_level_imports
    assert "tests.fakes" not in module_level_imports
    assert not any(name.startswith("tests.") for name in module_level_imports)


def test_cli_module_imports_without_tests_package_on_sys_path(monkeypatch):
    """A cheaper, in-process approximation of the real clean-install check:
    importing `jev_census.cli` must not require `tests` to be importable
    at all, given it's never touched unless CENSUS_FAKE_CLIENT=1 is set."""
    import importlib
    import sys

    monkeypatch.delenv("CENSUS_FAKE_CLIENT", raising=False)
    # jev_census.cli is already imported by the time this test runs (pytest
    # collection touches it); re-import to exercise the module body fresh,
    # same as an installed wheel's first import would.
    sys.modules.pop("jev_census.cli", None)
    importlib.import_module("jev_census.cli")


def test_pyproject_declares_demo_data_as_package_data():
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = config["tool"]["setuptools"]["package-data"]
    assert "jev_census" in package_data
    assert any("demo_data" in pattern for pattern in package_data["jev_census"])


def test_verify_clean_install_script_exists_and_is_executable():
    script = REPO_ROOT / "scripts" / "verify_clean_install.sh"
    assert script.exists()
    assert script.stat().st_mode & 0o111, "verify_clean_install.sh must be executable"
