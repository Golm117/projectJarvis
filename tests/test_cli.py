"""CLI smoke tests — guard the argument parser builds and the no-arg path runs.

These exist because a T-501 regression (`const=` on a `store_true` action) crashed
*every* `python -m jarvis` invocation — including `--help` — with a `TypeError` at
parser-construction time, yet the whole suite stayed green because nothing here
exercised the argparse path. `main(["--help"])` forces the parser to be built, so a
malformed `add_argument` fails this test instead of the user's terminal.

Model-free: the no-arg path runs the scripted mock demo (no mic, model, or network).
"""

from __future__ import annotations

import pytest

from jarvis.__main__ import main


def test_help_builds_parser_and_exits_zero():
    # --help builds the full parser then exits 0. A bad add_argument kwarg (e.g.
    # const= on store_true) raises TypeError *before* the exit — this catches it.
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_no_args_runs_mock_demo_and_returns_zero():
    # `python -m jarvis` with no flags runs the model-free mock demo and returns 0.
    assert main([]) == 0
