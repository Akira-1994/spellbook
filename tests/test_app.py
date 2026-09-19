import shutil

from fastapi.testclient import TestClient

from spellbook_reviewer.app import create_app


def make_project(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "migrations").mkdir()
    shutil.copy2("data/spellbook.sqlite", tmp_path / "data" / "spellbook.sqlite")
    shutil.copy2("migrations/002_review_workflow.sql", tmp_path / "migrations" / "002_review_workflow.sql")
    (tmp_path / "spellbook_doc_v1.1.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return tmp_path


def test_health_index_and_spell_query(tmp_path, monkeypatch):
    app = create_app(make_project(tmp_path, monkeypatch))
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        page = client.get("/")
        assert page.status_code == 200
        assert "法術校勘工作台" in page.text
        result = client.get("/api/spells", params={"q": "Acid", "limit": 5}).json()
        assert result["items"]


def test_write_routes_require_csrf_and_confirmed_editor(tmp_path, monkeypatch):
    app = create_app(make_project(tmp_path, monkeypatch))
    with TestClient(app) as client:
        assert client.post("/api/settings/editor", json={"editor_name": "校對者"}).status_code == 403
        client.get("/")
        headers = {"X-CSRF-Token": app.state.csrf_token}
        assert client.post("/api/settings/editor", json={"editor_name": "校對者"}, headers=headers).status_code == 200
        item = client.get("/api/spells", params={"limit": 1}).json()["items"][0]
        spell = client.get(f"/api/spells/{item['id']}").json()
        response = client.put(
            f"/api/spells/{item['id']}/draft",
            json={"revision_hash": spell["revision_hash"], "fields": {"spell.name_zh": spell["name_zh"]}},
            headers=headers,
        )
        assert response.status_code == 200


def test_rejects_non_local_host(tmp_path, monkeypatch):
    app = create_app(make_project(tmp_path, monkeypatch))
    with TestClient(app, base_url="http://example.invalid") as client:
        assert client.get("/health").status_code == 400


def test_source_pdf_is_served_inline(tmp_path, monkeypatch):
    app = create_app(make_project(tmp_path, monkeypatch))
    with TestClient(app) as client:
        response = client.get("/source/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"].startswith("inline")
