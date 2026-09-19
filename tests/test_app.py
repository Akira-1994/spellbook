from fastapi.testclient import TestClient

from spellbook.app import create_app


def client_for(paths):
    app = create_app(paths)
    client = TestClient(app)
    client.get("/")  # sets the CSRF cookie
    return client, {"X-CSRF-Token": app.state.csrf_token}


def test_index_list_and_paging(paths):
    client, _ = client_for(paths)
    with client:
        page = client.get("/")
        assert page.status_code == 200 and "法術書" in page.text
        assert client.get("/api/summary").json() == {"total": 2405, "edited": 0}
        first = client.get("/api/spells", params={"limit": 200}).json()["items"]
        second = client.get("/api/spells", params={"limit": 200, "offset": 200}).json()["items"]
        assert len(first) == len(second) == 200
        assert not {i["id"] for i in first} & {i["id"] for i in second}
        assert len(client.get("/api/spells", params={"letter": "S", "limit": 200}).json()["items"]) == 200
        assert client.get("/api/spells", params={"q": "Acid"}).json()["items"]
        assert client.get("/api/spells/spl_missing").status_code == 404


def test_edit_versions_and_rollback_round_trip(paths):
    client, headers = client_for(paths)
    with client:
        spell_id = client.get("/api/spells", params={"limit": 1}).json()["items"][0]["id"]
        spell = client.get(f"/api/spells/{spell_id}").json()
        body = {"revision_hash": spell["revision_hash"], "fields": {"duration": "1 小時"}}
        assert client.put(f"/api/spells/{spell_id}", json=body).status_code == 403  # CSRF required
        edited = client.put(f"/api/spells/{spell_id}", json=body, headers=headers)
        assert edited.status_code == 200 and edited.json()["duration"] == "1 小時"
        assert client.put(f"/api/spells/{spell_id}", json=body, headers=headers).status_code == 409  # stale
        assert client.get("/api/spells", params={"edited": True}).json()["items"][0]["id"] == spell_id

        versions = client.get(f"/api/spells/{spell_id}/versions").json()
        assert versions["max_versions"] == 5 and len(versions["items"]) == 1
        rolled = client.post(
            f"/api/spells/{spell_id}/rollback",
            json={"revision_hash": edited.json()["revision_hash"], "version_id": versions["items"][0]["id"]},
            headers=headers,
        )
        assert rolled.status_code == 200 and rolled.json()["duration"] == spell["duration"]

        restore = client.post(
            f"/api/spells/{spell_id}/restore-original",
            json={"revision_hash": rolled.json()["revision_hash"]},
            headers=headers,
        )
        assert restore.status_code == 422  # already identical to the original


def test_rejects_non_local_host(paths):
    with TestClient(create_app(paths), base_url="http://example.invalid") as client:
        assert client.get("/health").status_code == 400


def test_serves_favicon(paths):
    with TestClient(create_app(paths)) as client:
        page = client.get("/").text
        assert "favicon.svg" in page and "favicon.ico" in page
        icon = client.get("/favicon.ico")
        assert icon.status_code == 200 and icon.headers["content-type"] == "image/x-icon"
        assert icon.content[:4] == b"\x00\x00\x01\x00"  # ICO header
        assert client.get("/static/favicon.svg").status_code == 200
