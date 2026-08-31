"""Assert-based smoke check for the AI agent's date resolver and 4 tools.
Not a unit-test suite -- one runnable check per src/dates.py and src/tools.py,
matching tests/test_invariants.py's style.

Run: python tests/test_tools.py  (or `make test`)
Requires data/openmeteo.duckdb to already exist (`make pipeline` first).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import dates  # noqa: E402
import tools  # noqa: E402


def main():
    dates.demo()
    tools.demo()
    print("OK -- src/dates.py and src/tools.py self-checks passed.")


if __name__ == "__main__":
    main()
