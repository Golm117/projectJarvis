"""Smoke test: the package imports cleanly and exposes a version.

This is the zero-logic floor that proves the scaffold (T-001) works. Real
behavioral tests for the six core modules arrive with T-002 onward, built on
qa-tuning's simulated-clock + fakes harness (T-009).
"""

from __future__ import annotations

import jarvis


def test_package_imports() -> None:
    assert jarvis is not None


def test_version_is_a_string() -> None:
    assert isinstance(jarvis.__version__, str)
    assert jarvis.__version__
