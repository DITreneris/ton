"""Keep the Railway image buildable: COPY sources must survive .dockerignore."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dockerignore_excludes() -> set[str]:
    excluded: set[str] = set()
    for raw in (ROOT / ".dockerignore").read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("!"):
            continue
        excluded.add(line.rstrip("/"))
    return excluded


def _dockerfile_copy_sources() -> list[str]:
    sources: list[str] = []
    for raw in (ROOT / "Dockerfile").read_text().splitlines():
        line = raw.strip()
        if not line.startswith("COPY "):
            continue
        parts = line.split()
        args = [p for p in parts[1:] if not p.startswith("--")]
        if len(args) >= 2:
            sources.extend(args[:-1])
    return sources


def test_dockerfile_copy_sources_are_not_dockerignored():
    excluded = _dockerignore_excludes()
    for source in _dockerfile_copy_sources():
        name = source.rstrip("/")
        assert name not in excluded, f"Dockerfile COPY {source} is excluded by .dockerignore"
        assert (ROOT / name).exists(), f"Dockerfile COPY {source} is missing from the repo"


def test_dockerfile_does_not_copy_artifacts():
    copies = _dockerfile_copy_sources()
    assert "artifacts" not in copies
    assert "./artifacts" not in copies


def test_bundled_profile_is_under_src_package_data():
    path = ROOT / "src" / "ton_gram" / "data" / "gas_profile.json"
    assert path.is_file()
    assert "measured_net_cost_p90_nano" in path.read_text()
