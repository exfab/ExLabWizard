"""Tests for the CLI alias entry point in ``exlab_wizard.__main__``.

The entry point is a thin shim -- it prints a one-line hint and returns 0.
Backend Spec §4.3.
"""

from __future__ import annotations

import pytest

from exlab_wizard.__main__ import main


def test_main_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main()
    assert rc == 0


def test_main_prints_hint(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    captured = capsys.readouterr()
    assert "exlab-wizard-tray" in captured.out
    assert "exlab-wizard-window" in captured.out
