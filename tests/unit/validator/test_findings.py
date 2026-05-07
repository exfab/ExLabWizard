"""Unit tests for ``exlab_wizard.validator.findings``.

The :class:`Finding` dataclass is the wire-shape carrier for every
validator finding (Backend Spec §11.8). These tests pin:

* the §11.8 JSON shape (``to_dict`` / ``from_dict`` round-trip),
* the hashable / sortable behaviour required by the audit pub-sub
  channel's delta computation (frozen dataclass contract),
* equality semantics (two findings with identical fields are equal).
"""

from __future__ import annotations

import pytest

from exlab_wizard.validator.findings import Finding

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_finding(**overrides: object) -> Finding:
    """Return a canonical Finding matching the §11.8 example payload."""
    base: dict[str, object] = {
        "rule": "unresolved_placeholder_token",
        "tier": "hard",
        "run_path": "/data/lab/CONFOCAL_01/PROJ-0042/Run_<run_date>",
        "offending_path": "/data/lab/CONFOCAL_01/PROJ-0042/Run_<run_date>",
        "offending_kind": "directory_segment",
        "matched_token": "<run_date>",
        "rule_detail": "Angle-bracket identifier token <run_date> survived templating.",
        "synced_under_prior_policy": False,
        "override_active": False,
    }
    base.update(overrides)
    return Finding(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


def test_finding_instantiation_with_all_fields() -> None:
    finding = _make_finding()
    assert finding.rule == "unresolved_placeholder_token"
    assert finding.tier == "hard"
    assert finding.run_path == "/data/lab/CONFOCAL_01/PROJ-0042/Run_<run_date>"
    assert finding.offending_path == finding.run_path
    assert finding.offending_kind == "directory_segment"
    assert finding.matched_token == "<run_date>"
    assert "<run_date>" in finding.rule_detail
    assert finding.synced_under_prior_policy is False
    assert finding.override_active is False


def test_finding_minimum_required_fields() -> None:
    """The five non-default fields are required; the rest default."""
    finding = Finding(
        rule="orphan",
        tier="soft",
        run_path="/data/lab/EQ/PROJ-0001/Run_X",
        offending_path="/data/lab/EQ/PROJ-0001/Run_X",
        offending_kind="directory_segment",
    )
    # Defaults from §11.8 finding shape.
    assert finding.matched_token is None
    assert finding.rule_detail == ""
    assert finding.synced_under_prior_policy is False
    assert finding.override_active is False


def test_finding_is_frozen() -> None:
    """Findings are frozen so they can be hashed / put in sets."""
    finding = _make_finding()
    with pytest.raises(AttributeError):
        finding.tier = "soft"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_to_dict_contains_all_spec_keys() -> None:
    finding = _make_finding()
    payload = finding.to_dict()
    expected_keys = {
        "rule",
        "tier",
        "run_path",
        "offending_path",
        "offending_kind",
        "matched_token",
        "rule_detail",
        "synced_under_prior_policy",
        "override_active",
    }
    assert set(payload) == expected_keys


def test_to_dict_values_match_field_values() -> None:
    finding = _make_finding(matched_token="{{ project }}")
    payload = finding.to_dict()
    assert payload["rule"] == "unresolved_placeholder_token"
    assert payload["tier"] == "hard"
    assert payload["matched_token"] == "{{ project }}"
    assert payload["synced_under_prior_policy"] is False
    assert payload["override_active"] is False


def test_to_dict_from_dict_round_trip_identity() -> None:
    finding = _make_finding()
    rebuilt = Finding.from_dict(finding.to_dict())
    assert rebuilt == finding


def test_from_dict_accepts_minimal_payload() -> None:
    """Optional fields default if missing from the payload."""
    payload = {
        "rule": "orphan",
        "tier": "soft",
        "run_path": "/data/lab/EQ/PROJ-0001/Run_X",
        "offending_path": "/data/lab/EQ/PROJ-0001/Run_X",
        "offending_kind": "directory_segment",
    }
    finding = Finding.from_dict(payload)
    assert finding.matched_token is None
    assert finding.rule_detail == ""
    assert finding.synced_under_prior_policy is False
    assert finding.override_active is False


def test_from_dict_ignores_unknown_keys() -> None:
    """Unknown keys in a future-minor payload are not lethal."""
    payload = {
        "rule": "orphan",
        "tier": "soft",
        "run_path": "/data/lab/EQ/PROJ-0001/Run_X",
        "offending_path": "/data/lab/EQ/PROJ-0001/Run_X",
        "offending_kind": "directory_segment",
        "future_field_added_in_v2": "value the v1 reader does not know",
    }
    finding = Finding.from_dict(payload)
    assert finding.rule == "orphan"


def test_to_dict_preserves_none_matched_token() -> None:
    """``matched_token`` of ``None`` survives the round-trip as ``None``."""
    finding = Finding(
        rule="orphan",
        tier="soft",
        run_path="/x",
        offending_path="/x",
        offending_kind="directory_segment",
        matched_token=None,
    )
    payload = finding.to_dict()
    assert payload["matched_token"] is None
    assert Finding.from_dict(payload).matched_token is None


# ---------------------------------------------------------------------------
# Hash / set behaviour
# ---------------------------------------------------------------------------


def test_finding_is_hashable() -> None:
    finding = _make_finding()
    # The fact that the call returns is the assertion -- a frozen
    # dataclass without ``unsafe_hash`` would raise TypeError here.
    assert isinstance(hash(finding), int)


def test_findings_in_set_dedupe_by_value() -> None:
    """Two findings with identical field values collapse in a set."""
    finding_a = _make_finding()
    finding_b = _make_finding()
    bag = {finding_a, finding_b}
    assert len(bag) == 1


def test_findings_in_set_distinguish_by_value() -> None:
    """Different field values produce distinct set entries."""
    bag = {
        _make_finding(rule="unresolved_placeholder_token"),
        _make_finding(rule="leftover_jinja_marker"),
    }
    assert len(bag) == 2


# ---------------------------------------------------------------------------
# Sort behaviour
# ---------------------------------------------------------------------------


def test_findings_can_be_sorted_by_to_dict_key() -> None:
    """Findings sort lexicographically when keyed by their dict shape."""
    a = _make_finding(rule="aaa", offending_path="/a")
    b = _make_finding(rule="bbb", offending_path="/b")
    by_rule = sorted([b, a], key=lambda f: f.rule)
    assert by_rule == [a, b]


def test_findings_sort_by_tier_then_rule_then_path() -> None:
    """The expected three-key sort produces hard-first ordering."""
    soft = _make_finding(tier="soft", rule="orphan", offending_path="/z")
    hard_a = _make_finding(tier="hard", rule="aaa", offending_path="/a")
    hard_b = _make_finding(tier="hard", rule="bbb", offending_path="/b")

    def _key(f: Finding) -> tuple[int, str, str]:
        return (0 if f.tier == "hard" else 1, f.rule, f.offending_path)

    sorted_findings = sorted([soft, hard_b, hard_a], key=_key)
    assert sorted_findings == [hard_a, hard_b, soft]


# ---------------------------------------------------------------------------
# Equality semantics
# ---------------------------------------------------------------------------


def test_equality_with_same_field_values() -> None:
    a = _make_finding()
    b = _make_finding()
    assert a == b


def test_inequality_when_one_field_differs() -> None:
    a = _make_finding()
    b = _make_finding(matched_token="<other>")
    assert a != b


def test_finding_equality_is_not_identity() -> None:
    a = _make_finding()
    b = _make_finding()
    assert a is not b
    assert a == b


def test_audit_mode_flags_round_trip() -> None:
    """The audit-mode-only flags (synced_under_prior_policy /
    override_active) survive a to_dict / from_dict round-trip and
    distinguish two otherwise-identical findings."""
    a = _make_finding(synced_under_prior_policy=True, override_active=True)
    b = Finding.from_dict(a.to_dict())
    assert b.synced_under_prior_policy is True
    assert b.override_active is True
    assert a == b
    assert a != _make_finding()
