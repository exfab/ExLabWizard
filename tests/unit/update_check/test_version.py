"""Unit tests for update-notifier semver comparison. Design Spec §15.6 / §15.8 item 3."""

from __future__ import annotations

import pytest

from exlab_wizard.update_check.version import is_newer


@pytest.mark.parametrize(
    ("current", "latest_tag", "expected"),
    [
        # Newer tag (with the conventional GitHub ``v`` prefix) -> prompt.
        ("0.1.0", "v0.2.0", True),
        # Same version -> no prompt.
        ("0.1.0", "0.1.0", False),
        # Older tag -> no prompt.
        ("0.2.0", "v0.1.0", False),
        # ``v`` prefix tolerated on either / both sides.
        ("v0.1.0", "v0.2.0", True),
        ("v0.2.0", "0.1.0", False),
        # Pre-release ordering: a final == its own ``v`` tag (no upgrade).
        ("1.0.0", "v1.0.0", False),
        # A pre-release is older than the matching final, so the final prompts.
        ("1.0.0a1", "1.0.0", True),
        # Malformed tag never falsely prompts (and never raises).
        ("0.1.0", "not-a-version", False),
        ("0.1.0", "", False),
    ],
)
def test_is_newer_truth_table(current: str, latest_tag: str, expected: bool) -> None:
    assert is_newer(current, latest_tag) is expected


def test_malformed_does_not_raise() -> None:
    # Both sides malformed: still a quiet False rather than an exception.
    assert is_newer("garbage", "also-garbage") is False
