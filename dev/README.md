# ReportPortal Dev Environment

Local ReportPortal v5.15 instance with pre-loaded VoNR test data for evaluating the `rp-fetch` CLI.

## Prerequisites

- Docker & Docker Compose
- ~4 GB RAM available for containers
- `uv` installed (for running the seed script)

## Quick Start

```bash
# 1. Start all services
docker compose up -d

# 2. Wait for healthy + seed test data (auto-waits up to 120s)
uv run python seed.py --wait 120

# 3. The seed script outputs an API key and launch UUIDs
```

## Services

| Service | URL | Notes |
|---|---|---|
| UI | http://localhost:8080/ui | Login: `superadmin` / `erebus` |
| API | http://localhost:8080/api/v1 | Bearer token auth |
| Traefik Dashboard | http://localhost:8081 | Service routing overview |

## Seeded Test Data

The `seed.py` script creates:

**Project:** `vonr_launch`

**Launch 1 — `VoNR_Regression_v2.0` (PASSED)**
- 3 suites, 7 test cases — all passing
- Gherkin-style SIP/IMS registration, call, and handover logs
- PCAP and screenshot attachments on key tests

**Launch 2 — `VoNR_Regression_v2.1` (FAILED)**
- Same 3 suites, 7 test cases — 3 failures:
  - `TC001_InitialRegistration` — 403 from S-CSCF (HSS config error)
  - `TC010_MOCall_Success` — 408 timeout from S-CSCF
  - `TC020_SRVCC_DuringCall` — call dropped during handover
- Error logs, failure screenshots, PCAP captures, and Appium session logs attached

### Attachment Types

| Type | Content Type | Description |
|---|---|---|
| `.pcap` | `application/vnd.tcpdump.pcap` | Valid PCAP with fake SIP/UDP packets |
| `.png` | `image/png` | Valid PNG screenshot (640x480 red) |
| `.log` | `text/plain` | Fake Appium session log |

## Tear Down

```bash
# Stop and remove containers + volumes
docker compose down -v
```

## Re-seeding

The seed script is idempotent for the project (skips if exists) but creates new launches each run. To start fresh:

```bash
docker compose down -v
docker compose up -d
uv run python seed.py --wait 120
```

## Troubleshooting

**Services not starting?**
```bash
docker compose ps     # check status
docker compose logs api   # check API service logs
```

**API returning 404?**
- Wait longer — the API service can take 60-90s on first start
- Check: `curl http://localhost:8080/api/health`

**Seed script failing with connection error?**
- Use `--wait 180` for slower machines
- Ensure all services show "healthy": `docker compose ps`
