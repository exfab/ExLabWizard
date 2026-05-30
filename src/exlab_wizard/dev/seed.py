"""Standalone sample-data seeder -- ``python -m exlab_wizard.dev.seed``.

Forces the ``-test`` sandbox, ensures a starter ``config.yaml`` exists, then
**wipes and regenerates** the declarative ``SAMPLES`` tree (design spec §8).
Decoupled from tray boot so the async generation runs cleanly on its own event
loop. Always wipes -- that is the only mode; the generator prints the exact
directories it will remove before removing them (guardrailed; design spec §7).
"""

from __future__ import annotations

import os


def main(argv: list[str] | None = None) -> int:
    """Regenerate the ``-test`` sample-data tree from scratch.

    Returns the process exit code (0 on success).
    """
    # Set test mode BEFORE importing any paths.py helper so the state-dir and
    # config-path lookups both resolve under the '-test' suffixed sandbox
    # (mirrors tray/main.py's ordering).
    from exlab_wizard import paths

    os.environ[paths.TEST_MODE_ENV] = "1"

    from exlab_wizard.config.test_bootstrap import write_starter_test_config
    from exlab_wizard.paths import ensure_state_dir, os_config_path
    from exlab_wizard.sample_data import generate_samples

    ensure_state_dir()
    config_path = os_config_path()
    # Ensure a base config exists (no-op if the sandbox is already set up);
    # generate_samples then loads it, merges the sample equipment, and seeds.
    write_starter_test_config(config_path)

    print(f"sample-data seed: regenerating sample tree under {config_path.parent}")
    generate_samples(config_path, wipe=True)
    print("sample-data seed: done.")
    return 0


if __name__ == "__main__":  # pragma: no cover -- script entrypoint
    raise SystemExit(main())
