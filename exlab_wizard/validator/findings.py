"""Validator finding dataclass and serialization helpers.

Backend Spec §11.8 (finding shape contract) and §8.1 (rule catalog).

Every validator finding (creation-time or audit mode) is materialised as
a frozen :class:`Finding` instance. Frozen so findings can be put in
sets / sorted / hashed for the delta computation in the audit pub-sub
channel (Backend Spec §11.8 -- the 30-second background refresh diffs
two snapshots and emits adds/removes; that diff requires hashable
elements).

The on-the-wire JSON shape lives in §11.8 (verbatim copy below)::

    {
      "rule": "unresolved_placeholder_token",
      "tier": "hard",
      "run_path": "/data/lab/CONFOCAL_01/PROJ-0042/Run_<run_date>",
      "offending_path": "/data/lab/CONFOCAL_01/PROJ-0042/Run_<run_date>",
      "offending_kind": "directory_segment",
      "matched_token": "<run_date>",
      "rule_detail": "Angle-bracket identifier token <run_date>...",
      "synced_under_prior_policy": false,
      "override_active": false
    }

:meth:`Finding.to_dict` and :meth:`Finding.from_dict` round-trip this
shape; both modes (creation-time and audit) produce dictionaries that
are byte-identical given byte-identical inputs (the §11.8 determinism
contract).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

__all__ = ["Finding"]


@dataclass(frozen=True, slots=True)
class Finding:
    """One validator finding. Backend Spec §8.1, §11.8.

    Frozen so findings can be put in sets / sorted / hashed for the
    delta computation in the audit pub-sub channel (§11.8). ``slots``
    is set so that the dataclass does not allocate a per-instance
    ``__dict__`` -- audit mode can produce thousands of findings on a
    large tree and the per-instance overhead matters.

    Field semantics (§11.8):

    - ``rule`` -- the §8.1 rule that fired. One of the
      :class:`~exlab_wizard.constants.ProblemClass` values; kept as a
      plain string here so the dataclass does not depend on the closed
      enum at the typing surface (the enum is the canonical truth, but
      a string field keeps round-tripping via ``msgspec``/JSON cheap).
    - ``tier`` -- ``"hard"`` or ``"soft"`` per the §8.1.6 tier mapping.
    - ``run_path`` -- the run-level directory ancestor (or
      project/equipment level for orphans at those levels).
    - ``offending_path`` -- the absolute path of the artefact that
      tripped the rule. May equal ``run_path`` when the rule applies
      to the run-level directory itself.
    - ``offending_kind`` -- one of ``directory_segment``,
      ``file_name``, ``file_content`` (§11.8).
    - ``matched_token`` -- the substring (e.g. ``"<run_date>"``) or
      reserved name (e.g. ``"CON"``) that triggered the rule. ``None``
      for rules that don't have a single matched token (e.g. orphan).
    - ``rule_detail`` -- a short human-readable description suitable
      for the Problems-tab row. Defaults to ``""`` so the field is
      always present in the JSON shape.
    - ``synced_under_prior_policy`` -- set to ``True`` when audit mode
      finds a hard-tier finding on a run whose ``creation.json``
      ``sync_status`` is already ``"synced"`` (Backend Spec §7.3).
      Defaults to ``False`` for creation-time findings.
    - ``override_active`` -- set to ``True`` when the run's
      ``validation_overrides`` contains a non-revoked entry whose
      ``problem_class`` matches ``rule``. Defaults to ``False``.
    """

    rule: str
    tier: str
    run_path: str
    offending_path: str
    offending_kind: str
    matched_token: str | None = None
    rule_detail: str = ""
    synced_under_prior_policy: bool = False
    override_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the §11.8 JSON shape for this finding.

        The dictionary is suitable for ``msgspec.json.encode`` /
        ``json.dumps``. Field order matches the §11.8 example so the
        output is reproducible byte-for-byte across hosts (the
        determinism contract).
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Finding:
        """Reconstruct a :class:`Finding` from its §11.8 JSON shape.

        The inverse of :meth:`to_dict`. Unknown fields are ignored so
        that a future minor schema bump does not break older readers
        (per the §11.9 reader policy spirit, applied to in-flight
        finding payloads).
        """
        return cls(
            rule=payload["rule"],
            tier=payload["tier"],
            run_path=payload["run_path"],
            offending_path=payload["offending_path"],
            offending_kind=payload["offending_kind"],
            matched_token=payload.get("matched_token"),
            rule_detail=payload.get("rule_detail", ""),
            synced_under_prior_policy=payload.get(
                "synced_under_prior_policy",
                False,
            ),
            override_active=payload.get("override_active", False),
        )
