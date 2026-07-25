# Changelog

## [0.1.12] - 2026-06-24
### Fixed
- `gcp-radar run`: drop the invalid `project_id=` argument to `build_drawio()` that crashed the one-shot command with `TypeError: build_drawio() got an unexpected keyword argument 'project_id'`

## [0.1.11] - 2026-06-24
### Changed
- Set real package author metadata (Mor Michaeli) and point project URLs to the PyPI page, replacing placeholder values

## [0.1.10] - 2026-06-24
### Added
- Author note (Mor Michaeli, pypi:gcp-radar) embedded in generated `architecture.drawio` (XML comment) and `inventory.csv` (`#` comment header)
### Changed
- `drawio.read_csv` now skips leading `#` comment lines so the author header in `inventory.csv` doesn't break diagram generation

## [0.1.9] - 2026-05-13
### Fixed
- `collect_pubsub`: replace removed `publisher.project_path()` helper with `f"projects/{project_id}"` directly

## [0.1.8] - 2026-05-13
### Fixed
- Fix project auto-detection in `__main__.py` (the actual CLI entry point); fall back to `DEVSHELL_PROJECT_ID`/`GOOGLE_CLOUD_PROJECT` env vars when `gcloud config get-value project` is empty

## [0.1.7] - 2026-05-13
### Fixed
- Fall back to `DEVSHELL_PROJECT_ID` / `GOOGLE_CLOUD_PROJECT` env vars when `gcloud config get-value project` returns empty (fixes Cloud Shell default project detection)

## [0.1.6] - 2026-05-13
### Fixed
- Replace non-existent `google-cloud-sqladmin` dependency with `google-api-python-client`
- Rewrite `collect_cloud_sql` to use the `sqladmin` discovery API instead of non-existent `sql_v1` client

## [0.1.5] - 2026-05-13
### Fixed
- Replace non-existent `google-cloud-sql` dependency with correct `google-cloud-sqladmin`

## [0.1.4] - 2026-05-13
### Fixed
- `drawio.py`: wrap ID values in `str()` before slicing to prevent crash on non-string IDs
- `inventory.py`: fix `ListFunctionsRequest` to use `parent=` instead of `location=`

## [0.1.0] - 2026-05-07
### Added
- `gcp-radar inventory` — scan Compute Engine, Cloud SQL, Cloud Functions, GKE, Cloud Storage, Cloud Run, Firestore, Pub/Sub
- `gcp-radar diagram` — generate draw.io XML from inventory CSV
- `gcp-radar run` — one-shot inventory + diagram
- Multi-project support via `--project`
- Region-specific scanning via `--region`
- CSV export for all resources
- Auto-generated architecture diagrams with service categories and logical connections
