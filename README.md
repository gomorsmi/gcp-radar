# GCP Radar

**GCP Resource Inventory Scanner & Architecture Diagram Generator**

A Python CLI tool that inventories all your GCP resources and generates beautiful draw.io architecture diagrams.

## Features

- 🔍 **Inventory Scanning**: Scans Compute Engine, Cloud SQL, Cloud Functions, GKE, Cloud Storage, Cloud Run, Firestore, Pub/Sub, and more
- 📊 **CSV Export**: Export complete inventory to CSV for further analysis
- 🏗️ **Architecture Diagrams**: Auto-generate draw.io `.drawio` files with logical service connections
- ☁️ **Multi-Project Support**: Scan multiple GCP projects in one go
- 🔐 **Service Account Auth**: Use gcloud default credentials or specify service account key

## Installation

```bash
pip install gcp-radar
```

Or from source:

```bash
git clone https://github.com/gomorsmi/gcp-radar.git
cd gcp-radar
pip install -e .
```

## Quick Start

### 1. Inventory Your GCP Resources

```bash
# Scan default project
gcp-radar inventory --export inventory.csv

# Scan specific project
gcp-radar inventory --project my-project --export inventory.csv

# Export to specific region
gcp-radar inventory --region us-central1 --export inventory.csv
```

### 2. Generate Architecture Diagram

```bash
gcp-radar diagram --input inventory.csv --output architecture.drawio

# Then open architecture.drawio in app.diagrams.net or the desktop app
```

### 3. One-Shot: Scan + Diagram

```bash
gcp-radar run --project my-project \
  --output-csv inventory.csv \
  --output-diagram architecture.drawio
```

## Supported GCP Services

| Service | Collected | Diagram |
|---------|-----------|---------|
| Compute Engine | ✓ | ✓ |
| Cloud SQL | ✓ | ✓ |
| Cloud Functions | ✓ | ✓ |
| GKE (Kubernetes Engine) | ✓ | ✓ |
| Cloud Storage | ✓ | ✓ |
| Cloud Run | ✓ | ✓ |
| Firestore | ✓ | ✓ |
| Pub/Sub | ✓ | ✓ |

## Requirements

- Python 3.9+
- GCP credentials (via `gcloud auth` or service account key)
- Permissions: `compute:Read`, `sql:Read`, `container:Read`, `storage:Read`, `pubsub:Read`, `firestore:Read`, etc.

## Output Format

### CSV Inventory

| ProjectID | Service | Name | ID | Type/Size | Status | Region | Extra |
|-----------|---------|------|----|-----------|---------|---------|----|
| my-project | Compute Engine | web-01 | abc123 | n1-standard-2 | RUNNING | us-central1 | ... |

### Architecture Diagram

Auto-generated `.drawio` file with:
- **Swim lanes** by service category (Compute, Database, Storage, Messaging)
- **Icons** for each resource
- **Logical connections** between services

## Configuration

Create a `.env` file to override defaults:

```bash
export GCP_PROJECT=my-project
export GCP_REGION=us-central1
```

Or pass flags:

```bash
gcp-radar inventory --project my-project --region us-central1
```

## License

MIT

## Contributing

Pull requests welcome! Please open an issue first to discuss changes.

## Support

GitHub Issues: https://github.com/gomorsmi/gcp-radar/issues
