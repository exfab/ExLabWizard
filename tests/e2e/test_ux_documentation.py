"""Automated UX-interaction documentation + coverage checks.

Drives :mod:`tests.e2e.ux_catalog` three ways:

* :func:`test_ux_interactions_doc_is_current` regenerates
  ``docs/UX_INTERACTIONS.md`` from the catalog and fails if the
  committed file is stale -- the human-readable interaction reference
  is therefore *generated*, never hand-maintained.
* :func:`test_ux_interaction_testids_exist_in_source` asserts every
  cataloged ``data-testid`` is present in the ``exlab_wizard/ui``
  source -- the affordance really exists.
* :func:`test_ux_interactions_are_e2e_covered` asserts every cataloged
  ``data-testid`` is exercised by a ``tests/e2e/test_flow_*.py`` file
  -- the catalog is verified, not aspirational.

This module is pure file I/O (no Playwright), so it runs in every
environment.
"""

from __future__ import annotations

from pathlib import Path

from tests.e2e.ux_catalog import UX_INTERACTIONS, UXInteraction

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC_PATH = _REPO_ROOT / "docs" / "UX_INTERACTIONS.md"
_UI_SOURCE_ROOT = _REPO_ROOT / "src" / "exlab_wizard" / "ui"
_E2E_ROOT = _REPO_ROOT / "tests" / "e2e"

_DOC_HEADER = """# UX Interaction Reference

<!-- GENERATED FILE -- do not edit by hand.
     Regenerated from tests/e2e/ux_catalog.py by
     tests/e2e/test_ux_documentation.py. Edit the catalog and re-run
     the test suite to update this file. -->

Every operator-facing affordance in the ExLab-Wizard UI, grouped by
flow. Each row is verified by the e2e suite: the `data-testid` exists
in the `exlab_wizard/ui` source and is driven by a `tests/e2e`
flow test.
"""


def _render_doc(interactions: tuple[UXInteraction, ...]) -> str:
    """Render the catalog into the ``UX_INTERACTIONS.md`` markdown body."""
    lines: list[str] = [_DOC_HEADER]
    flows: list[str] = []
    for entry in interactions:
        if entry.flow not in flows:
            flows.append(entry.flow)
    for flow in flows:
        lines.append(f"\n## {flow}\n")
        lines.append("| Route | Test ID | Element | Action | Outcome |")
        lines.append("|---|---|---|---|---|")
        for entry in interactions:
            if entry.flow != flow:
                continue
            lines.append(
                f"| `{entry.route}` | `{entry.testid}` | {entry.element} "
                f"| {entry.action} | {entry.outcome} |"
            )
    return "\n".join(lines) + "\n"


def test_ux_interactions_doc_is_current() -> None:
    """``docs/UX_INTERACTIONS.md`` is regenerated from the catalog.

    Regenerates the doc and fails if the committed copy was stale -- so
    a catalog change without a doc refresh is caught in CI. The fresh
    content is written either way, so re-running the suite makes the
    test pass.
    """
    expected = _render_doc(UX_INTERACTIONS)
    current = _DOC_PATH.read_text(encoding="utf-8") if _DOC_PATH.exists() else None
    if current != expected:
        _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DOC_PATH.write_text(expected, encoding="utf-8")
        msg = (
            f"{_DOC_PATH.relative_to(_REPO_ROOT)} was stale and has been "
            "regenerated from tests/e2e/ux_catalog.py -- commit the update."
        )
        raise AssertionError(msg)


def test_ux_catalog_is_non_trivial() -> None:
    """Guard against an empty catalog silently passing the other checks."""
    assert len(UX_INTERACTIONS) >= 20
    # No duplicate (testid, route) pairs.
    seen: set[tuple[str, str]] = set()
    for entry in UX_INTERACTIONS:
        key = (entry.testid, entry.route)
        assert key not in seen, f"duplicate catalog entry: {key}"
        seen.add(key)


def _references(testid: str, haystack: str) -> bool:
    """Return ``True`` when ``haystack`` references ``testid``.

    Tolerates testids that the source / tests build with an f-string
    (``f'data-testid="settings-nav-{section}"'``, a page-object helper
    like ``f"settings-nav-{section}"``, or the dynamic copier-variable
    fields ``f"{testid_prefix}-{key}"``): such a testid counts as
    referenced when its stem -- everything up to the last ``-`` --
    appears in the haystack (as the f-string prefix literal).
    """
    if testid in haystack:
        return True
    if "-" in testid:
        stem = testid.rsplit("-", 1)[0]
        if f"{stem}-{{" in haystack or stem in haystack:
            return True
    return False


def test_ux_interaction_testids_exist_in_source() -> None:
    """Every cataloged ``data-testid`` is present in the UI source."""
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(_UI_SOURCE_ROOT.rglob("*.py"))
    )
    missing = sorted(
        {entry.testid for entry in UX_INTERACTIONS if not _references(entry.testid, source)}
    )
    assert not missing, f"testids absent from exlab_wizard/ui source: {missing}"


def test_ux_interactions_are_e2e_covered() -> None:
    """Every cataloged ``data-testid`` is driven by an e2e test.

    Scans the whole ``tests/e2e`` tree -- flow tests, page objects and
    helpers -- since flow tests routinely reach affordances through a
    page-object attribute rather than the literal testid.
    """
    e2e_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(_E2E_ROOT.rglob("*.py"))
        if path.name != "test_ux_documentation.py" and path.name != "ux_catalog.py"
    )
    uncovered = sorted(
        {entry.testid for entry in UX_INTERACTIONS if not _references(entry.testid, e2e_text)}
    )
    assert not uncovered, f"testids not exercised by any tests/e2e test: {uncovered}"
