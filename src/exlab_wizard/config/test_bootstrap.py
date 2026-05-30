"""Starter ``config.yaml`` bootstrap for the ``-test`` sandbox.

Shared by the tray's ``--test`` flag (:mod:`exlab_wizard.tray.main`) and the
standalone dev seeder (:mod:`exlab_wizard.dev.seed`). Lives in the light
``config`` package so the dev command can reuse it without importing the heavy
``tray`` package (whose ``__init__`` pulls in pystray and the server stack).
"""

from __future__ import annotations

from pathlib import Path

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


def write_starter_test_config(config_path: Path) -> None:
    """Write a starter test ``config.yaml`` if one does not already exist.

    Preseeds every path-typed field under the test sandbox
    (``config_path.parent``) so the wizard runs without manual Settings entry;
    the LIMS endpoint and email are intentionally left blank so the operator
    still wires that integration through the live Settings UI. Equipment is
    left empty -- sample equipment is seeded separately by
    :func:`exlab_wizard.sample_data.generate_samples`.

    Idempotent: an existing config is never overwritten. The sandbox is
    persistent across launches and the operator resets it by deleting the
    suffixed directory.
    """
    if config_path.exists():
        return

    # Lazy imports keep this module's import graph light for callers that only
    # need the function lazily (the tray test path and the dev command).
    from exlab_wizard.config.loader import save_config
    from exlab_wizard.config.models import Config, OrchestratorConfig, PathsConfig
    from exlab_wizard.paths import ensure_dir

    sandbox = config_path.parent  # e.g. ~/Library/Application Support/exlab-wizard-test
    paths_cfg = PathsConfig(
        templates_dir=str(sandbox / "templates"),
        plugin_dir=str(sandbox / "plugins"),
        local_root=str(sandbox / "local"),
    )
    orchestrator_cfg = OrchestratorConfig(
        label="test-workstation",
        staging_root=str(sandbox / "staging"),
    )
    cfg = Config(paths=paths_cfg, orchestrator=orchestrator_cfg, equipment=[])
    save_config(config_path, cfg)

    # Pre-create the preseeded sub-directories so first-launch path lookups
    # (template scans, plugin discovery) do not fail on a missing tree.
    for sub in (
        paths_cfg.templates_dir,
        paths_cfg.plugin_dir,
        paths_cfg.local_root,
        orchestrator_cfg.staging_root,
    ):
        if sub:
            ensure_dir(Path(sub))

    _log.info("test mode: wrote starter config [path=%s]", str(config_path))


def bootstrap_test_config(config_path: Path, *, include_samples: bool) -> None:
    """Tray ``--test`` bootstrap: write the starter config, optionally seed samples.

    No-op when a config already exists -- the sandbox is persistent, so a
    repeat ``--test`` boot must neither overwrite the config nor re-seed (and
    never wipes). On a fresh sandbox, writes the starter config and, when
    ``include_samples`` (``--add-test-samples``), expands the declarative
    ``SAMPLES`` tree through the shared generator in non-destructive mode.
    """
    if config_path.exists():
        return
    write_starter_test_config(config_path)
    if include_samples:
        from exlab_wizard.sample_data import generate_samples

        generate_samples(config_path, wipe=False)
