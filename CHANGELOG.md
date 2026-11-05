# Changelog

All notable changes to the TON GRAM paper worker and its document set.

Document versions are independent of the Python package version.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 2026-11-05

### Added

- Lean document index, per-document versioning, and this changelog.
- Closed learning loop: `python -m ton_gram learn` scores a paper day and writes advisory lessons. No auto-apply, no 25 bps retune.

### Changed

- Live `flat` cycles stay `flat`: size-matched USD₮ mid, `mid_spread_max_bps=200`, feed-down keeps `HARD_HALT`.

### Documents
- `document-system` v1 added: initial catalog
- `learning-loop` v1 added: initial catalog
- `paper-policy` v1 added: initial catalog
- `pinned-route` v1 added: initial catalog

## 2026-11-04

### Added

- Daily Telegram card at 06:00 UTC (08:00 Europe/Vilnius in winter).
- Measured gas profile in the image; isolated recovery drills.

### Fixed

- `counts()` binding, Telegram 404 burning the daily key, SMA reset on deploy, first-cycle missed heartbeat.
