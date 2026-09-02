# GCP Radar Development Guide

## Project Structure

```
gcp_radar/
├── src/gcp_radar/
│   ├── __init__.py           # Package version and metadata
│   ├── __main__.py           # CLI entry point
│   ├── inventory.py          # GCP resource collectors
│   └── drawio.py             # Architecture diagram generator
├── tests/
│   ├── __init__.py
│   └── test_gcp_radar.py     # Unit tests
├── pyproject.toml            # Package config
├── README.md                 # User guide
├── CHANGELOG.md              # Release notes
└── LICENSE                   # MIT License
```

## Development Setup

```bash
# Clone and install in editable mode
git clone https://github.com/gomorsmi/gcp-radar.git
cd gcp-radar
pip install -e ".[dev]"

# Run tests
pytest

# Generate inventory
gcp-radar inventory --export test.csv

# Generate diagram
gcp-radar diagram --input test.csv --output test.drawio
```

## Architecture

### Inventory Module (`inventory.py`)

Functions following the pattern `collect_<service>()`:

- `collect_compute_instances()` — VM instances
- `collect_cloud_sql()` — Managed SQL databases
- `collect_cloud_functions()` — Serverless functions
- `collect_gke_clusters()` — Kubernetes clusters
- `collect_cloud_storage()` — Object storage
- `collect_cloud_run()` — Managed containers
- `collect_firestore()` — NoSQL database
- `collect_pubsub()` — Message broker

Each returns a list of `row()` dicts with standard columns.

### Diagram Module (`drawio.py`)

- `build_drawio()` — Creates XML from inventory rows
- `export_drawio()` — Writes `.drawio` file
- Organizes resources into swim lanes by category
- Auto-generates logical connections

## Adding New GCP Services

1. Create collector function in `inventory.py`:

```python
def collect_my_service(project_id):
    client = ...
    rows = []
    try:
        for resource in ...:
            rows.append(row(project_id, "My Service",
                name, id, type_size, status, region, extra))
    except GoogleAPIError as e:
        print(f"  [!] Error: {e}")
    return rows
```

2. Add to `run_inventory()` collectors list

3. Add style to `SERVICE_STYLES` in `drawio.py`

4. Add category mapping to `SERVICE_CATEGORY` in `drawio.py`

5. Add unit test in `test_gcp_radar.py`

## Testing

Run all tests:

```bash
pytest -v
```

With coverage:

```bash
pytest --cov=gcp_radar --cov-report=html
```

## Publishing to PyPI

```bash
# Build
python -m build

# Upload (requires PyPI credentials)
twine upload dist/*
```

## FAQ

**Q: How do I use a custom GCP project?**
A: Use `--project my-project` or set `export GOOGLE_CLOUD_PROJECT=my-project`

**Q: Can I scan multiple projects?**
A: Currently scans one at a time. Parallel scanning coming in v0.2.

**Q: Where is my service account key?**
A: Set `GOOGLE_APPLICATION_CREDENTIALS` environment variable to the key file path.

## Contributing

1. Fork the repo
2. Create a feature branch
3. Add tests
4. Submit a pull request
