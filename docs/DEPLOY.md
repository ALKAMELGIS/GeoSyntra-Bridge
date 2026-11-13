# Running GeoSyntra Bridge for many users

One server, everyone uses the browser. Nothing to install on the users' computers.

## 1. Server
* Linux (Docker) or Windows. 4+ CPU cores, 8–16 GB RAM, disk for 72 h of jobs (≈ 3 × the drawings uploaded per day).
* **Docker:** copy `deploy/commercial-settings.example` to `deploy/.env`, fill it in, then
  `docker compose -f deploy/docker-compose.yml up -d --build`.
  Add the free ODA File Converter for the best DWG reading: `--build-arg ODA_DEB_URL=…` (see the Dockerfile).
* **Windows:** `start_windows_commercial.bat` (the LibreDWG DWG converter ships in `tools/libredwg`).
* Put HTTPS in front (nginx, Caddy, IIS) and point `GSB_PUBLIC_URL` at it.

## 2. Settings (environment variables)
| Variable | Meaning | Default |
|---|---|---|
| `GSB_AUTH_MODE` | `accounts` (sign-in, credits, owner console), `token`, `none` (single user) | `none` |
| `GSB_WORKERS` | conversions processed at the same time | 2 |
| `GSB_MAX_UPLOAD_MB` | largest drawing (or ZIP) accepted | 500 |
| `GSB_JOB_TTL_HOURS` | jobs and outputs are deleted after | 72 |
| `GSB_DATA_DIR` | jobs, accounts, project memory | `./data` |
| `GSB_OWNER_EMAILS` | accounts that get the owner console | — |

## 3. Monitoring
`GET /api/health` (no sign-in): status, version, File-GDB writer, DWG converter, workers, running jobs.

## 4. What users can upload
DWG, DXF, MicroStation DGN V7, or a ZIP with the drawing and its xrefs / sheets. Civil 3D objects are read
through their drawn geometry; anything that cannot be read is listed on the Upload step with the fix
(EXPLODE / Export to AutoCAD).
