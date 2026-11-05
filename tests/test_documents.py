from pathlib import Path

from fastapi.testclient import TestClient

from ton_gram.api import create_app
from ton_gram.clock import FrozenClock
from ton_gram.config import Settings
from ton_gram.documents import DocStore


def _store(tmp_path: Path, clock: FrozenClock) -> DocStore:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "paper-policy.md").write_text("# Paper policy\n\nDo not sign.\n", encoding="utf-8")
    (root / "learning-loop.md").write_text("# Learning loop\n\nAdvisory only.\n", encoding="utf-8")
    return DocStore(root, clock, changelog_path=tmp_path / "CHANGELOG.md")


def test_sync_indexes_and_versions_once(tmp_path: Path, clock: FrozenClock):
    store = _store(tmp_path, clock)
    first = store.sync(note="initial")
    assert len(first["documents"]) == 2
    assert all(item["version"] == 1 for item in first["documents"])
    assert (store.root / "INDEX.md").is_file()
    assert "paper-policy" in (store.root / "INDEX.md").read_text(encoding="utf-8")
    assert store.version_path("paper-policy", 1).is_file()
    changelog = store.changelog_path.read_text(encoding="utf-8")
    assert "`paper-policy` v1" in changelog
    assert "`learning-loop` v1" in changelog

    second = store.sync()
    assert all(row["changed"] is False for row in second["results"])
    assert all(item["version"] == 1 for item in second["documents"])
    assert changelog == store.changelog_path.read_text(encoding="utf-8")


def test_publish_bumps_only_changed_hash(tmp_path: Path, clock: FrozenClock):
    store = _store(tmp_path, clock)
    store.sync()
    (store.root / "paper-policy.md").write_text("# Paper policy\n\nStill no signing.\n", encoding="utf-8")
    bumped = store.publish("paper-policy.md", note="clarify signing")
    assert bumped["changed"] is True
    assert bumped["version"] == 2
    assert store.get("paper-policy", 1)["body"] == "# Paper policy\n\nDo not sign.\n"
    assert "Still no signing" in store.get("paper-policy")["body"]
    assert store.get("paper-policy")["versions"] == [1, 2]
    assert store.get("learning-loop")["version"] == 1
    log = store.changelog_path.read_text(encoding="utf-8")
    assert "`paper-policy` v2" in log
    assert log.count("`paper-policy` v1") == 1


def test_docs_http_roundtrip(tmp_path: Path, settings: Settings, clock: FrozenClock, ledger):
    settings.docs_dir = tmp_path / "docs"
    store = _store(tmp_path, clock)
    store.sync()
    app = create_app(settings, ledger, clock)
    client = TestClient(app)
    assert client.get("/v1/docs").status_code == 401
    listed = client.get("/v1/docs", headers={"Authorization": "Bearer test-token"})
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()["documents"]}
    assert ids == {"paper-policy", "learning-loop"}
    one = client.get("/v1/docs/paper-policy", headers={"Authorization": "Bearer test-token"})
    assert one.status_code == 200
    assert one.json()["version"] == 1
    missing = client.get("/v1/docs/no-such-doc", headers={"Authorization": "Bearer test-token"})
    assert missing.status_code == 404
