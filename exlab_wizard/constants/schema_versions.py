"""Schema version pins for every JSON/YAML artifact the wizard reads or writes.

Every value here is committed by the design spec and must NOT be made
configurable. Bumping any of these is a coordinated change requiring matching
updates to readers, writers, migrations, and history-table entries.
"""

from __future__ import annotations

# Version of the per-run ``creation.json`` cache file. See Backend Spec
# section 11.3 (history table) for the schema and migration rules.
CREATION_JSON_VERSION: str = "1.8"

# Version of the per-equipment ``readme_fields.json`` cache. Backend Spec §11.4.
README_FIELDS_JSON_VERSION: str = "1.1"

# Version of the per-run ``ingest.json`` cache produced during NAS ingest.
# Backend Spec §13.4.
INGEST_JSON_VERSION: str = "1.1"

# Version of the per-equipment ``equipment.json`` cache. Backend Spec §11.4.1.
EQUIPMENT_JSON_VERSION: str = "1.0"

# Version of the per-equipment ``test_runs.json`` cache. Backend Spec §11.4.2.
TEST_RUNS_JSON_VERSION: str = "1.0"

# Version of the offline LIMS-project catalogue artifact distributed with
# releases. Backend Spec §7.2.9.
OFFLINE_CATALOGUE_VERSION: str = "1.0"

# Version of the YAML front-matter block written into README.md by the
# wizard. Backend Spec §10.
README_FRONT_MATTER_SCHEMA_VERSION: str = "1.1"
