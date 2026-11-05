"""Lean document index, per-document versions, and CHANGELOG.md.

Filesystem only. No LLM. Does not touch paper fills, signing, or the 25 bps gate.
Current bodies live in docs/*.md. Snapshots live in docs/versions/<id>/vN.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ton_gram.clock import Clock, format_utc

REGISTRY_NAME = "registry.json"
INDEX_NAME = "INDEX.md"
VERSIONS_DIR = "versions"
MANAGED_SKIP = {REGISTRY_NAME, INDEX_NAME, "CHANGELOG.md"}
DOC_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
SCHEMA_VERSION = "1.0"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_docs_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path("docs")


class DocStore:
    def __init__(
        self,
        root: Path,
        clock: Clock,
        changelog_path: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.clock = clock
        self.changelog_path = Path(changelog_path) if changelog_path else self.root.parent / "CHANGELOG.md"

    @property
    def registry_path(self) -> Path:
        return self.root / REGISTRY_NAME

    @property
    def index_path(self) -> Path:
        return self.root / INDEX_NAME

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / VERSIONS_DIR).mkdir(parents=True, exist_ok=True)

    def load_registry(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            return {
                "schema_version": SCHEMA_VERSION,
                "updated_utc": None,
                "documents": [],
            }
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def save_registry(self, registry: dict[str, Any]) -> None:
        self.ensure_root()
        registry["schema_version"] = SCHEMA_VERSION
        registry["updated_utc"] = format_utc(self.clock.now())
        registry["documents"] = sorted(registry.get("documents") or [], key=lambda d: d["id"])
        self.registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")

    def _entry(self, registry: dict[str, Any], doc_id: str) -> dict[str, Any] | None:
        for item in registry.get("documents") or []:
            if item.get("id") == doc_id:
                return item
        return None

    def discover(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        found: list[Path] = []
        for path in sorted(self.root.glob("*.md")):
            if path.name in MANAGED_SKIP:
                continue
            found.append(path)
        return found

    def read_body(self, relative: str) -> str:
        path = self.root / relative
        if not path.is_file():
            raise FileNotFoundError(relative)
        return path.read_text(encoding="utf-8")

    def title_of(self, body: str, fallback: str) -> str:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return fallback.replace("-", " ").title()

    def version_path(self, doc_id: str, version: int) -> Path:
        return self.root / VERSIONS_DIR / doc_id / f"v{version}.md"

    def list_versions(self, doc_id: str) -> list[int]:
        folder = self.root / VERSIONS_DIR / doc_id
        if not folder.is_dir():
            return []
        versions: list[int] = []
        for path in folder.glob("v*.md"):
            try:
                versions.append(int(path.stem[1:]))
            except ValueError:
                continue
        return sorted(versions)

    def snapshot(self, doc_id: str, version: int, body: str) -> Path:
        path = self.version_path(doc_id, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def publish(self, relative: str, *, note: str = "") -> dict[str, Any]:
        """Register or bump one current markdown file. No-op when the hash is unchanged."""
        self.ensure_root()
        path = self.root / relative
        if not path.is_file():
            raise FileNotFoundError(relative)
        doc_id = path.stem
        if not DOC_ID_RE.match(doc_id):
            raise ValueError(f"invalid document id: {doc_id}")
        body = path.read_text(encoding="utf-8")
        digest = sha256_text(body)
        title = self.title_of(body, doc_id)
        registry = self.load_registry()
        entry = self._entry(registry, doc_id)
        now = format_utc(self.clock.now())
        if entry and entry.get("sha256") == digest:
            return {"id": doc_id, "changed": False, "version": int(entry["version"]), "sha256": digest}
        if entry:
            version = int(entry["version"]) + 1
            action = "changed"
            entry["title"] = title
            entry["path"] = relative
            entry["version"] = version
            entry["sha256"] = digest
            entry["updated_utc"] = now
            entry["status"] = "current"
        else:
            version = 1
            action = "added"
            registry.setdefault("documents", []).append(
                {
                    "id": doc_id,
                    "title": title,
                    "path": relative,
                    "version": version,
                    "sha256": digest,
                    "updated_utc": now,
                    "status": "current",
                }
            )
        self.snapshot(doc_id, version, body)
        self.save_registry(registry)
        self.append_changelog(doc_id, version, action, note or title)
        return {
            "id": doc_id,
            "changed": True,
            "action": action,
            "version": version,
            "sha256": digest,
            "title": title,
        }

    def sync(self, *, note: str = "") -> dict[str, Any]:
        results = [self.publish(path.name, note=note) for path in self.discover()]
        self.write_index()
        return {
            "updated_utc": format_utc(self.clock.now()),
            "documents": self.load_registry().get("documents") or [],
            "results": results,
        }

    def index(self) -> dict[str, Any]:
        registry = self.load_registry()
        return {
            "schema_version": registry.get("schema_version") or SCHEMA_VERSION,
            "updated_utc": registry.get("updated_utc"),
            "count": len(registry.get("documents") or []),
            "documents": registry.get("documents") or [],
        }

    def get(self, doc_id: str, version: int | None = None) -> dict[str, Any]:
        if not DOC_ID_RE.match(doc_id):
            raise FileNotFoundError(doc_id)
        registry = self.load_registry()
        entry = self._entry(registry, doc_id)
        if entry is None:
            raise FileNotFoundError(doc_id)
        if version is None:
            body = self.read_body(entry["path"])
            resolved = int(entry["version"])
        else:
            path = self.version_path(doc_id, version)
            if not path.is_file():
                raise FileNotFoundError(f"{doc_id} v{version}")
            body = path.read_text(encoding="utf-8")
            resolved = version
        return {
            **entry,
            "version": resolved,
            "versions": self.list_versions(doc_id),
            "body": body,
        }

    def write_index(self) -> Path:
        registry = self.load_registry()
        docs = registry.get("documents") or []
        lines = [
            "# Document index",
            "",
            "Lean catalog of operator documents for the TON GRAM paper worker.",
            "Current files live in this directory. Snapshots live in `versions/<id>/vN.md`.",
            "",
            f"Updated: {registry.get('updated_utc') or 'never'}",
            "",
            "| ID | Title | Version | Updated (UTC) | SHA256 |",
            "| --- | --- | ---: | --- | --- |",
        ]
        for item in docs:
            digest = str(item.get("sha256") or "")
            short = digest[:12] if digest else ""
            lines.append(
                f"| `{item['id']}` | {item.get('title') or item['id']} | v{item.get('version')} | "
                f"{item.get('updated_utc') or ''} | `{short}` |"
            )
        if not docs:
            lines.append("| — | No documents registered | — | — | — |")
        lines.extend(
            [
                "",
                "Rebuild with `python -m ton_gram docs sync`.",
                "Read a version with `python -m ton_gram docs show <id> --version N`.",
                "",
            ]
        )
        self.ensure_root()
        self.index_path.write_text("\n".join(lines), encoding="utf-8")
        return self.index_path

    def append_changelog(self, doc_id: str, version: int, action: str, note: str) -> None:
        marker = f"`{doc_id}` v{version}"
        heading = f"## {format_utc(self.clock.now())[:10]}"
        bullet = f"- {marker} {action}: {note}"
        if self.changelog_path.is_file():
            text = self.changelog_path.read_text(encoding="utf-8")
            if marker in text:
                return
        else:
            text = (
                "# Changelog\n\n"
                "All notable changes to the TON GRAM paper worker and its document set.\n\n"
                "Document versions are independent of the Python package version.\n"
            )
        if heading in text:
            head, tail = text.split(heading, 1)
            nxt = tail.find("\n## ")
            section = tail if nxt < 0 else tail[:nxt]
            remainder = "" if nxt < 0 else tail[nxt:]
            if "### Documents" not in section:
                section = section.rstrip() + "\n\n### Documents\n"
            section = section.rstrip() + f"\n{bullet}\n"
            text = head + heading + section + remainder
        else:
            text = text.rstrip() + f"\n\n{heading}\n\n### Documents\n\n{bullet}\n"
        self.changelog_path.parent.mkdir(parents=True, exist_ok=True)
        if not text.endswith("\n"):
            text += "\n"
        self.changelog_path.write_text(text, encoding="utf-8")
