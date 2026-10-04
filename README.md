# Football Intelligence Lab — Phase 1

Real-data football analytics vertical slice. Provider: StatsBomb Open Data, Bundesliga 2023/24 (competition 9, season 281).

## Run on Windows

```powershell
cd football-intelligence-lab
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
py scripts\ingest_statsbomb.py
uvicorn apps.api.app.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000/docs` for the working API.

Try:
- `/players?q=wirtz`
- `/players/{id}`
- `/players/{id}/similar`
- `/matches/{match_id}/analysis`

## Data integrity
No demo/mock player statistics are included. If the StatsBomb dataset has not been ingested, endpoints return empty data rather than fabricated values.

## Similarity v0.1
Features: xG, shots, shot assists, passes, completed passes, carries, pressures, interceptions, duels. Each feature is robust-standardised as `(x - median)/(1.4826*MAD)`, clipped to [-3,3]. Distance is equal-weight Euclidean; display similarity is `100*exp(-0.55*distance)`. Responses expose shared/different features and the method. This is intentionally an auditable baseline; minutes/position peer groups and per-90 normalisation are the next milestone.

## Attribution
StatsBomb Open Data requires StatsBomb attribution when publishing/sharing/distributing analysis. Review the current StatsBomb User Agreement before deployment or commercial use.


## v0.9 Multi-League Engine
The UI now selects a competition/season globally. All Player Lab, Compare, ScoutLab, Match Lab, Team DNA and Transfer Lab API calls inherit that dataset, so peer percentiles never mix competitions by accident.

Install additional open-data presets (run from the project root):
```powershell
.\.venv\Scripts\python.exe scripts\ingest_statsbomb.py ligue1-2223
.\.venv\Scripts\python.exe scripts\ingest_statsbomb.py mls-2023
.\.venv\Scripts\python.exe scripts\ingest_statsbomb.py premier-league-1516
```
Restart/reload the app and use the dataset selector in the header. Bundesliga 2023/24 legacy data remains supported.

## v1.0 current-data provider layer
FIL can now ingest provider-normalised datasets without changing Player Lab analytics.

### 2025/26 Top-5 research baseline
`python scripts/ingest_mahadi.py`

This downloads the public m-mahadi master table and creates a FIL-normalised 2025/26 dataset. The upstream data is derived from multiple sites and is **not** covered by the repo's MIT code licence; use it for private/research prototyping unless source terms are cleared.

### 2026/27 Big Five via Understat
Examples:
`python scripts/ingest_understat.py premier-league 2026 --shots`
`python scripts/ingest_understat.py la-liga 2026 --shots`
`python scripts/ingest_understat.py bundesliga 2026 --shots`
`python scripts/ingest_understat.py serie-a 2026 --shots`
`python scripts/ingest_understat.py ligue-1 2026 --shots`

`--shots` fetches completed-match shot coordinates/xG. FIL never fabricates pass, carry, pressure or generic event coordinates when the provider does not supply them.

## v2.9.2 worldwide wonderkids
Run once after merging this patch:
`\.venv\Scripts\python.exe scripts\ingest_global_wonderkids.py`
This creates the local global age-14–19 scouting catalogue; no API key is required.


## v2.9.4 hotfix
- Player Lab now treats profile data as required but similarity/maps as optional, so research/aggregate datasets without event files no longer show false “Could not load this player”.
- Global search results always route through dataset-aware `openPlayer`, including normal Player Lab searches.
- Wonderkid and search navigation now use the selected dataset consistently.


## v2.9.4 performance hotfix
- Dataset switches no longer eagerly fetch Match Lab when Match Lab is not open.
- Stable section payloads use short-lived per-dataset caching and in-flight request deduplication.
- Match, Team, League and Wonderkids data are lazy-loaded and reused for five minutes.
- Returning to a section no longer repeats the same expensive request immediately.

## v3.0.1 — Prospect Intelligence
Wonderkids now includes prospect dossiers, age-cohort context, development-pathway evidence, discovery lenses, sorting and a saved prospect watchlist. Global-only youth records no longer need a Player Lab profile to be useful.

## v3.1.0 — Youth Scout Desk
Wonderkids now retain their exact Player Lab dataset/id route at enrichment time, eliminating the catalogue-to-analytics handoff bug. Adds a four-player Prospect Compare desk plus analytics-linked and saved-prospect discovery lenses.


## v5.0.0 — Sparse-data stability hotfix
- Null-safe percentile calculations across baseline/research datasets.
- ScoutLab ignores unavailable metrics instead of crashing on `None`.
- Player/global resolver no longer 500s when a peer metric is missing.
- Missing provider data remains missing; it is not converted to a real zero percentile.
- Frontend asset cache-bust updated to v5.0.0.


## v5.0.1 — Career Intelligence downloader hotfix
- Fixed public Transfermarkt-history ingestion failing with HTTP 403 on Cloudflare R2.
- Downloader now sends explicit browser-compatible headers, disables transparent content encoding, retries transient CDN errors, and reports a clean terminal error.
- No new dependency is required.
- Frontend cache-bust/footer updated to v5.0.1.

## v5.1.0 — Adaptive Player Intelligence
Player Lab no longer wastes the right-hand analytics column on a large empty map-coverage card for midfielders and defenders. It now falls back to role-specific aggregate intelligence and can discover genuine spatial evidence for the same player from another installed provider while preserving provenance.

## v6.0.0 — Intelligence Platform
Adds FIL Rankings, Development Lab, Wonderkids → Development integration and expands FIL's cross-dataset intelligence layer while preserving v5.1 Adaptive Player Intelligence, Recruitment Intelligence, Data Hub, Career Intelligence and null-safe analytics.

## v7.0.0 — World Football Intelligence
Adds the dedicated World Cup 2026 workspace, public-domain tournament importer, groups/results/squads/scorers, squad-to-FIL identity links and hard-stop Player Lab loading recovery. See `docs/V7.0.0.md`.

Install World Cup data once:
`\.\.venv\Scripts\python.exe scripts\ingest_world_cup_2026.py`


## v9.0.0 — World Cup Intelligence II
- Fixed the v7.0 World Cup path mismatch: importer writes to `data/worldcup2026`, and the API now reads that exact canonical location.
- Added `/world-cup/status` file-level health diagnostics with readable/missing state, byte size and resolved path.
- Added a knockout-stage browser derived from installed tournament records.
- Added nation tournament-summary API for record, goals for/against and leading scorers.
- Reworked World Cup controls so tabs/refresh/squad actions match the FIL visual system instead of browser-default buttons.
- Preserved provider provenance: World Cup tournament records remain separate from club analytics.


## v9.0.1 World Cup reliability
- Restored World Cup tab click bindings.
- Match hubs now reconcile compact/full tournament match identities instead of assuming provider `num` IDs are identical.


## v10.0.0 — Release Candidate
- Includes the complete v9.0.1 World Cup tab and match-identity reliability fixes.
- Replaces fragile one-time World Cup tab bindings with a delegated, idempotent router that survives rerenders.
- Adds `/release-readiness` diagnostics for API, installed current-era datasets and World Cup file health.
- Match Hub failures now stop safely with retry/back controls instead of leaving a dead panel.
- Provider health in the sidebar now reports release-check state without blocking startup.
- Asset cache-bust moved to v10.0.0 and footer marks this build as the release candidate.


## v1.0.0 RELEASE
Release-candidate polish: World Cup event language, faster cached navigation, optional local player cut-outs, and v11 match-evidence fixes retained.


## v1.2.0 release hardening
See `docs/V1.2.0-RELEASE.md`. This build aligns version reporting, caches World Cup source payloads, tightens optional Player Lab enrichment, preserves missing values as missing, and removes generated bytecode from the distributable.

## v1.3.0 RC1 release workflow

On Windows, after creating `.venv` and installing `requirements.txt`, double-click `START-FIL.cmd` or run the Uvicorn command below. `GET /release-audit` is the pre-release integrity gate. The universal search uses stable FIL identities and Player Lab exposes every installed season/competition record attached to that identity. Provider provenance is available from the **Sources & coverage** control. Keep real provider keys only in `.env`; `.env.example` intentionally contains no secrets.


## v1.5 RC3
Global same-role Similar Players now spans all installed current-generation FIL datasets. Live Transfers includes a public RSS reported-news fallback and keeps structured confirmed feeds separate.

## £0 public deployment

See `DEPLOY-FREE.md` and `render.yaml` for the Render Free deployment configuration. Run `PREPARE-DEPLOY.ps1` before pushing. Never commit `.env` or provider secrets, and publish only datasets/assets you have permission to redistribute.
