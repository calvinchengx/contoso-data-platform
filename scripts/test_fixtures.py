"""Run the tests that genuinely need a fixture wheel, and say so when there are none.

The `fixtures` marker is an ESCAPE HATCH, not a suite. RULES.md forbids any
test under tests/ from reaching a fixture wheel, and marking one is how a test
that genuinely cannot avoid it stays runnable. So an empty selection is the
state that rule drives toward, not a fault: the wheel-dependent tests moved out
to the product repository, and nothing here has needed the hatch since.

Bare pytest cannot express that. It exits 5 for "no tests collected", which is
indistinguishable from a collection error at the exit-code level and turned
`make test-fixtures` into a step that could only fail. Every other exit code is
passed through untouched -- a marked test that FAILS still fails here.

Printing the outcome rather than exiting quietly is the point: "no test needs a
wheel" is a statement about the suite, and a step that succeeds in silence
reads the same whether it ran everything or nothing.
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NO_TESTS_COLLECTED = 5


def main():
    r = subprocess.run(
        ["uv", "run", "--frozen", "pytest", "-q", "tests", "-m", "fixtures"],
        cwd=ROOT,
    )
    if r.returncode == NO_TESTS_COLLECTED:
        print(
            "no test is marked `fixtures`, so there is nothing here that needs a "
            "wheel.\nThat is the state RULES.md asks for; `make verify` is what "
            "exercises the wheels."
        )
        return 0
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
