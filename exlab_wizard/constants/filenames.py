"""Canonical filenames and on-disk path fragments used throughout the app.

All values are committed by the design spec. Code that needs a known filename
must import the constant from here rather than re-typing the literal -- this
keeps the spec compliance checkable at a single location.
"""

from __future__ import annotations

# Hidden cache directory written next to README.md inside every run/project
# directory. Backend Spec §11.3.
CACHE_DIR_NAME: str = ".exlab-wizard"

# Cache filename for the run/project creation snapshot. Backend Spec §11.3.
CREATION_JSON_NAME: str = "creation.json"

# Cache filename for the equipment-level README field index. Backend Spec §11.4.
README_FIELDS_JSON_NAME: str = "readme_fields.json"

# Cache filename for per-equipment static metadata. Backend Spec §11.4.1.
EQUIPMENT_JSON_NAME: str = "equipment.json"

# Cache filename for the NAS ingest state machine. Backend Spec §13.4.
INGEST_JSON_NAME: str = "ingest.json"

# Cache filename for the per-equipment test-run history. Backend Spec §11.4.2.
TEST_RUNS_JSON_NAME: str = "test_runs.json"

# Sidecar answer file written next to ``copier.yml`` when the user re-runs the
# wizard against an existing directory. Backend Spec §5.3.
ANSWERS_FILE_NAME: str = ".exlab-answers.yml"

# Per-host log filename template inside the central log dir. Backend Spec §4.5.
# Format with ``LOG_FILE_TEMPLATE.format(hostname=...)``.
LOG_FILE_TEMPLATE: str = "wizard.{hostname}.log"

# Top-level README filename inside every run/project directory. Backend Spec §10.
README_FILE_NAME: str = "README.md"

# Path (relative to the run/project root) of the integrity-checksum file
# written by the wizard after a successful creation. Backend Spec §11.3.
CHECKSUMS_RELATIVE: str = ".exlab-wizard/checksums.sha256"

# Manifest filename inside a Copier template root. Backend Spec §5.2.
COPIER_MANIFEST_NAME: str = "copier.yml"

# Manifest filename inside a plugin package. Backend Spec §6.1.2.
PLUGIN_MANIFEST_NAME: str = "manifest.yml"

# Long-lived server-state file written by the tray process. Backend Spec §4.3.2.
SERVER_STATE_FILE: str = "server.json"

# SQLite database for cached LIMS project rows. Backend Spec §7.2.4.
LIMS_CACHE_DB_NAME: str = "lims_cache.db"

# SQLite database for the NAS sync queue. Backend Spec §7.1.1.
SYNC_QUEUE_DB_NAME: str = "sync_queue.db"

# Central rotated log file written by the tray/server process. Backend Spec §4.5.
CENTRAL_LOG_FILE: str = "app.log"

# Encrypted-at-rest secrets file used when the OS keyring is unavailable.
# Backend Spec §7.4.4.
SECRETS_FILE: str = "secrets.enc"
