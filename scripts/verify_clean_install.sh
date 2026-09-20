#!/usr/bin/env bash
# T7.1 acceptance (06-P7-ADOPTION.md): builds the wheel, installs it into a
# throwaway venv OUTSIDE the repo, and proves the installed package works
# on its own -- no source tree, no `tests` package, no accidental relative
# import saving it. Run this from anywhere; it does not need to be run from
# the repo root.
#
# Usage: scripts/verify_clean_install.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d /tmp/jev-census-clean-install.XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

# A venv for both building and installing -- many modern systems (Homebrew
# Python on macOS, most current Linux distros) refuse a bare `pip install`
# against the system interpreter at all (PEP 668, "externally-managed-
# environment"), so this needs a venv even just to get the `build` tool.
echo "== creating build/install venv =="
cd "$WORK_DIR"
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install --quiet --upgrade pip build

echo "== building wheel =="
cd "$REPO_ROOT"
rm -rf dist/
python3 -m build --wheel --outdir "$WORK_DIR/dist"
WHEEL="$(ls "$WORK_DIR"/dist/*.whl)"
echo "built: $WHEEL"

echo "== installing into the clean venv, away from the repo =="
cd "$WORK_DIR"
pip install --quiet "$WHEEL"

echo "== proving the source tree and the tests package are not on sys.path =="
python3 - <<'PYEOF'
import sys
assert "tests" not in sys.modules
try:
    import tests  # noqa: F401
    raise SystemExit("FAIL: 'tests' package importable from an installed wheel -- it should not exist here")
except ModuleNotFoundError:
    pass
import jev_census
print(f"jev_census imported from: {jev_census.__file__}")
assert "site-packages" in jev_census.__file__, "jev_census did not import from site-packages -- source tree leaking onto sys.path"
PYEOF

echo "== census --help =="
census --help > /dev/null

echo "== census estimate on the bundled demo corpus =="
python3 - <<'PYEOF'
import jev_census
from pathlib import Path
demo_dir = Path(jev_census.__file__).parent / "demo_data"
assert demo_dir.exists(), f"demo_data missing from installed package: {demo_dir}"
assert (demo_dir / "stories.parquet").exists(), "bundled demo corpus missing from installed package"
assert (demo_dir / "questions.yaml").exists(), "bundled demo question set missing from installed package"
print(f"demo_data present at: {demo_dir}")
PYEOF
census estimate --input "$(python3 -c 'import jev_census, pathlib; print(pathlib.Path(jev_census.__file__).parent / "demo_data" / "stories.parquet")')" \
  --questions "$(python3 -c 'import jev_census, pathlib; print(pathlib.Path(jev_census.__file__).parent / "demo_data" / "questions.yaml")')" \
  --id-field id > /dev/null

deactivate
echo ""
echo "PASS: clean install works from outside the repository, no source tree required."
