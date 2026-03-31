# rp-fetch

CLI tool to bulk-download ReportPortal launch content — logs, screenshots, PCAPs, and Appium attachments — without touching the UI.

## Installation

### From source (development)

```bash
git clone https://github.com/b-yond/dt-report-portal-cli
cd dt-report-portal-cli
uv sync
```

### Pre-built executable

Download the latest release for your platform from [GitHub Releases](https://github.com/b-yond/dt-report-portal-cli/releases). The executable is self-contained — no Python installation required.

| Platform | Asset |
|---|---|
| macOS (Apple Silicon) | `rp-fetch-macos-arm64` |
| macOS (Intel) | `rp-fetch-macos-x86_64` |
| Linux (x86_64) | `rp-fetch-linux-x86_64` |
| Windows (x86_64) | `rp-fetch-windows-x86_64.exe` |

After downloading, make it executable (macOS/Linux):

```bash
chmod +x rp-fetch-macos-arm64
./rp-fetch-macos-arm64 --help
```

## Quick Start

```bash
# 1. Configure your ReportPortal connection
uv run rp-fetch config init

# 2. List recent launches
uv run rp-fetch launch list

# 3. Download a launch
uv run rp-fetch download <LAUNCH_UUID>
```

> **Note:** If using a pre-built executable, replace `uv run rp-fetch` with `./rp-fetch` (or the path to the binary).

## Commands

### Configuration

```bash
uv run rp-fetch config init          # Interactive first-time setup
uv run rp-fetch config show          # Print current config (API key masked)
uv run rp-fetch config test          # Test connection and auth
uv run rp-fetch config set KEY VALUE # Set a single config value
```

### Launch Discovery

```bash
# List launches with filters
uv run rp-fetch launch list --name "VoNR_Regression" --status failed --from 2026-03-01

# Interactive search and selection
uv run rp-fetch launch search --name "VoNR"
```

### Download

```bash
# Download everything for a launch
uv run rp-fetch download 96d1bc02-6a3f-451e-b706-719149d51ce4

# Download only error logs and attachments, 8 parallel workers
uv run rp-fetch download 96d1bc02-6a3f-451e-b706-719149d51ce4 \
  --include logs \
  --include attachments \
  --level error \
  --parallel 8

# Dry run to preview before committing
uv run rp-fetch download 96d1bc02-6a3f-451e-b706-719149d51ce4 --dry-run

# Flatten output into a single directory
uv run rp-fetch download 96d1bc02-6a3f-451e-b706-719149d51ce4 --flat

# Search and download in one step
uv run rp-fetch search-and-download --name "VoNR" --out ./data/march-campaign
```

## Configuration

On first run, `uv run rp-fetch config init` creates `~/.rp-fetch/config.toml`:

```toml
[default]
base_url = "https://reportportal.example.com"
api_key  = "your-api-key-here"
project  = "tessa_project"

[output]
directory = "./rp-downloads"
```

**Priority order:** CLI flags > environment variables > config file > defaults

| Environment Variable | Maps to |
|---|---|
| `RP_BASE_URL` | `base_url` |
| `RP_API_KEY` | `api_key` |
| `RP_PROJECT` | `project` |

## Output Structure

```
rp-downloads/
└── VoNR_Regression_v2.1_2026-03-18/
    ├── manifest.json
    ├── launch_metadata.json
    └── items/
        └── Services/
            └── VoNR_RegistrationTest/
                ├── item_metadata.json
                ├── logs.txt
                └── attachments/
                    ├── attachment_000.pcap
                    ├── attachment_001.png
                    └── attachment_002.log
```

With `--flat`, all files land in a single directory with path-prefixed names.

## Authentication

`rp-fetch` uses API key (Bearer token) authentication. To generate one:

1. Log into your ReportPortal instance
2. Navigate to **Profile > API Keys**
3. Click **Generate API Key**
4. Copy the key — it is not shown again

The key is stored in `~/.rp-fetch/config.toml` with `600` permissions. For CI/CD, use the `RP_API_KEY` environment variable instead.

## Development

```bash
# Install with dev dependencies
uv sync

# Run tests
uv run pytest -v

# Run the CLI
uv run rp-fetch --help
```

### Local ReportPortal Dev Environment

A full ReportPortal instance with pre-loaded test data is available for local testing:

```bash
# Start ReportPortal (first run takes a few minutes to pull images)
cd dev/
docker compose up -d

# Wait ~90s for services to be healthy, then seed test data
uv run python seed.py --wait 120

# The seed script prints the API key and launch UUIDs — use them:
cd ..
uv run rp-fetch config init
# base_url: http://localhost:8080
# project:  tessa_vonr
# api_key:  <from seed output>

uv run rp-fetch launch list
uv run rp-fetch download <LAUNCH_UUID>
```

UI is at http://localhost:8080/ui (login: `superadmin` / `erebus`).

See [`dev/README.md`](dev/README.md) for details on the seeded data.

### Building Self-Contained Executables

You can build a standalone executable using [PyInstaller](https://pyinstaller.org/) that bundles Python and all dependencies into a single binary — no Python installation needed on the target machine.

```bash
# Install PyInstaller
uv run pip install pyinstaller

# Build the executable
uv run pyinstaller rp-fetch.spec

# The binary is at dist/rp-fetch (or dist/rp-fetch.exe on Windows)
./dist/rp-fetch --help
```

The `rp-fetch.spec` file at the project root is pre-configured for a clean one-file build. To build manually without the spec file:

```bash
uv run pyinstaller \
  --onefile \
  --name rp-fetch \
  --hidden-import rp_fetch \
  --collect-submodules rp_fetch \
  src/rp_fetch/cli.py
```

### CI/CD Releases

Push a version tag to trigger the multi-platform build workflow:

```bash
git tag v0.1.0
git push origin v0.1.0
```

This runs the `build-release.yml` GitHub Actions workflow, which builds executables for macOS (arm64 + x86_64), Linux (x86_64), and Windows (x86_64), then uploads them as release assets.

## Tech Stack

| Component | Choice |
|---|---|
| Language | Python 3.11+ |
| CLI framework | Typer |
| HTTP client | httpx (async) |
| Terminal UI | Rich |
| Config | TOML + Pydantic v2 |
| Packaging | uv / pyproject.toml |
| Executable builds | PyInstaller |
| CI/CD | GitHub Actions |
