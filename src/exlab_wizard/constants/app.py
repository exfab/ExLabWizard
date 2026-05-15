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
