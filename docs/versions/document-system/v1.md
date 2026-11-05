# Document system

Lean index, per-document versions, one changelog. No CMS, no database, no model overlay.

## Layout

- `docs/*.md` — current bodies (this file is one of them)
- `docs/registry.json` — machine index: id, title, version, sha256, updated_utc
- `docs/INDEX.md` — generated human index
- `docs/versions/<id>/vN.md` — immutable snapshot of that version
- `CHANGELOG.md` — dated document (and project) history

## Commands

```bash
python -m ton_gram docs sync
python -m ton_gram docs index
python -m ton_gram docs show paper-policy
python -m ton_gram docs show paper-policy --version 1
```

`docs sync` hashes every current markdown file. Unchanged files are no-ops. A changed hash bumps the version, writes `versions/<id>/vN.md`, updates the index, and appends a line to `CHANGELOG.md`.

`GET /v1/docs` and `GET /v1/docs/{id}` expose the same catalog (snapshot bearer).
