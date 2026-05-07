"""Verify the canonical filename constants.

These literals appear in directory layouts the design spec calls out
verbatim. Every test pins one literal so unintended renames are caught.
"""

from __future__ import annotations

from exlab_wizard.constants import filenames


def test_cache_dir_name() -> None:
    # Backend Spec §11.3.
    assert filenames.CACHE_DIR_NAME == ".exlab-wizard"


def test_creation_json_name() -> None:
    # Backend Spec §11.3.
    assert filenames.CREATION_JSON_NAME == "creation.json"


def test_readme_fields_json_name() -> None:
    # Backend Spec §11.4.
    assert filenames.README_FIELDS_JSON_NAME == "readme_fields.json"


def test_equipment_json_name() -> None:
    # Backend Spec §11.4.1.
    assert filenames.EQUIPMENT_JSON_NAME == "equipment.json"


def test_ingest_json_name() -> None:
    # Backend Spec §13.4.
    assert filenames.INGEST_JSON_NAME == "ingest.json"


def test_test_runs_json_name() -> None:
    # Backend Spec §11.4.2.
    assert filenames.TEST_RUNS_JSON_NAME == "test_runs.json"


def test_answers_file_name() -> None:
    # Sidecar answer file used when re-running the wizard. Backend Spec §5.3.
    assert filenames.ANSWERS_FILE_NAME == ".exlab-answers.yml"


def test_log_file_template() -> None:
    # Backend Spec §4.5.
    assert filenames.LOG_FILE_TEMPLATE == "wizard.{hostname}.log"


def test_log_file_template_formats() -> None:
    # The template must format with a ``hostname`` keyword argument.
    formatted = filenames.LOG_FILE_TEMPLATE.format(hostname="lab-pc")
    assert formatted == "wizard.lab-pc.log"


def test_readme_file_name() -> None:
    # Backend Spec §10.
    assert filenames.README_FILE_NAME == "README.md"


def test_checksums_relative() -> None:
    # Backend Spec §11.3. Note this is a path relative to the run/project
    # root and must use forward slashes (cross-platform JSON value).
    assert filenames.CHECKSUMS_RELATIVE == ".exlab-wizard/checksums.sha256"


def test_copier_manifest_name() -> None:
    # Backend Spec §5.2.
    assert filenames.COPIER_MANIFEST_NAME == "copier.yml"


def test_plugin_manifest_name() -> None:
    # Backend Spec §6.1.2.
    assert filenames.PLUGIN_MANIFEST_NAME == "manifest.yml"


def test_server_state_file() -> None:
    # Backend Spec §4.3.2.
    assert filenames.SERVER_STATE_FILE == "server.json"


def test_lims_cache_db_name() -> None:
    # Backend Spec §7.2.4.
    assert filenames.LIMS_CACHE_DB_NAME == "lims_cache.db"


def test_sync_queue_db_name() -> None:
    # Backend Spec §7.1.1.
    assert filenames.SYNC_QUEUE_DB_NAME == "sync_queue.db"


def test_central_log_file() -> None:
    # Backend Spec §4.5.
    assert filenames.CENTRAL_LOG_FILE == "app.log"


def test_secrets_file() -> None:
    # Backend Spec §7.4.4.
    assert filenames.SECRETS_FILE == "secrets.enc"


def test_filenames_re_exported_from_package() -> None:
    # All filename constants must be re-exported from
    # ``exlab_wizard.constants`` so callers do not need to know which
    # submodule owns each name.
    from exlab_wizard import constants

    assert constants.CACHE_DIR_NAME == ".exlab-wizard"
    assert constants.CREATION_JSON_NAME == "creation.json"
    assert constants.README_FIELDS_JSON_NAME == "readme_fields.json"
    assert constants.EQUIPMENT_JSON_NAME == "equipment.json"
    assert constants.INGEST_JSON_NAME == "ingest.json"
    assert constants.TEST_RUNS_JSON_NAME == "test_runs.json"
    assert constants.ANSWERS_FILE_NAME == ".exlab-answers.yml"
    assert constants.LOG_FILE_TEMPLATE == "wizard.{hostname}.log"
    assert constants.README_FILE_NAME == "README.md"
    assert constants.CHECKSUMS_RELATIVE == ".exlab-wizard/checksums.sha256"
    assert constants.COPIER_MANIFEST_NAME == "copier.yml"
    assert constants.PLUGIN_MANIFEST_NAME == "manifest.yml"
    assert constants.SERVER_STATE_FILE == "server.json"
    assert constants.LIMS_CACHE_DB_NAME == "lims_cache.db"
    assert constants.SYNC_QUEUE_DB_NAME == "sync_queue.db"
    assert constants.CENTRAL_LOG_FILE == "app.log"
    assert constants.SECRETS_FILE == "secrets.enc"
