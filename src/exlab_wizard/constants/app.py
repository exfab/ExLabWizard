"""App-level identifiers used as path / keyring / branding fragments.

Single source of truth for the wizard's identifier string. Backend Spec
§7.4.1 (keyring service), §9 (config dir), §15.7 (state dir). The cache
directory name (``.exlab-wizard``) is a derived hidden-directory form
and lives in ``filenames.py`` for clarity at the use site.
"""

from __future__ import annotations

# Stable identifier used everywhere the wizard names a path, keyring
# entry, or process. Renaming requires changing every entry in
# constants/app.py (this file) and keeping in sync with the cache
# directory name in constants/filenames.py.
APP_NAME: str = "exlab-wizard"

# Runtime opt-in flag: when set to a truthy value the config loader
# prefixes every ``equipment[i].id`` with :data:`TEST_MODE_PREFIX` so
# the resulting on-disk + NAS run directories sort under a single
# ``TEST_<original>/...`` namespace that operators can identify (and
# later delete) without sifting individual run leaves. The wizard's
# downstream code paths (path construction, run discovery, NAS sync
# targets) only ever see the prefixed ID, so no other module needs to
# know about test mode. Truthy values are ``"1"`` / ``"true"`` /
# ``"yes"`` / ``"on"`` (case-insensitive); any other value (including
# unset / empty) leaves IDs unchanged.
TEST_MODE_ENV: str = "EXLAB_WIZARD_TEST_MODE"

# Equipment-ID prefix applied by :func:`exlab_wizard.config.loader.apply_test_mode_prefix`
# when :data:`TEST_MODE_ENV` is set to a truthy value. Must satisfy the
# equipment-ID regex (``^[A-Z][A-Z0-9_]*$``) so the prefixed value
# round-trips through the model's id validator.
TEST_MODE_PREFIX: str = "TEST_"
