# v1.5.0 RC3 — Global Similarity + Live Transfer QA

See `docs/V1.5.0-RC3.md`.

# Football Intelligence Lab v1.4.0 RC2 — Design System + Live Transfers

- Unified premium sports-app navigation and visual hierarchy.
- Player Lab hero/dashboard visual overhaul without changing transparent analytics.
- New Live Transfers top-level module with confirmed/reported/rumour states.
- Provider-neutral transfer adapter: local `data/transfers/live.json` or explicitly configured permitted JSON feed via `FIL_TRANSFER_FEED_URL`.
- Five-minute feed cache, component-only retry and no fabricated transfer items.
- Transfer cards can resolve directly into Player Lab.
- Responsive desktop/mobile design pass.
- Existing analytics, global identity, World Cup and lazy-loading architecture preserved.

# Football Intelligence Lab v1.3.0 RC1 — Release Candidate

This candidate freezes major feature work and focuses on identity, reliability, provenance and packaging.

## Headline changes
- Stable FIL global player identity index across installed 2025/26 and 2026/27 datasets.
- Player Lab season/competition switch generated from that identity index.
- Universal search across players, clubs and World Cup nations.
- Non-blocking Player Lab enrichment: images, maps, similarity and identity metadata cannot prevent the core profile rendering.
- Source & coverage inspector with provider, season, local update time and methodology notes.
- Release audit endpoint for datasets, global identity, World Cup sources, null safety and secret hygiene.
- World Cup fixes from the previous release line retained: source-aware lineups, event badges, nation-scoped scorers, cached tournament data and optional match-stat layer.
- Windows START-FIL.cmd launcher and secret-free .env.example.

## Data rules
FIL never turns missing provider data into zero, never silently blends unlike providers/seasons, and does not fabricate formations, assists or spatial events. Market-value dates remain attached to sourced valuations.

## v1.6.1 FINAL RC2
- Live Transfers now uses a resilient chain: permitted structured JSON, local verified feed, public RSS reports, then last-success disk cache.
- RSS/news items are always `REPORTED`; FIL never upgrades headlines to `CONFIRMED`.
- Global Similar Players now scans all installed current-generation datasets and uses canonical football-role archetypes rather than provider-specific raw position strings.
- Same player/provider duplicates are deduplicated and missing metrics remain excluded, never zero-filled.
- Added `scripts/release_qa.py` and final cache-busted frontend shell.
