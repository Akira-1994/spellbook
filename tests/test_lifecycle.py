from fastapi.testclient import TestClient

from spellbook.app import create_app
from spellbook.lifecycle import Lifecycle


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make(idle=300, grace=15):
    clock = Clock()
    return Lifecycle(idle_timeout=idle, close_grace=grace, clock=clock), clock


def test_exits_shortly_after_the_last_page_closes():
    life, clock = make()
    life.ping("a")
    clock.advance(60)
    life.close("a")
    clock.advance(14)
    assert not life.should_exit()
    clock.advance(1)
    assert life.should_exit()


def test_reload_reconnects_within_the_grace_period():
    life, clock = make()
    life.ping("old")
    life.close("old")
    clock.advance(1)
    life.ping("new")  # the reloaded page
    clock.advance(60)
    assert not life.should_exit()


def test_closing_one_of_several_tabs_keeps_running():
    life, clock = make()
    life.ping("a")
    life.ping("b")
    life.close("a")
    clock.advance(59)
    assert not life.should_exit()  # tab b is still open, just throttled


def test_idle_fallback_and_pages_that_vanished_without_goodbye():
    life, clock = make()
    life.ping("crashed")
    clock.advance(299)
    assert not life.should_exit()
    clock.advance(1)
    assert life.should_exit()


def test_quit_request_exits_immediately():
    life, _ = make()
    life.ping("a")
    life.request_quit()
    assert life.should_exit()


def test_endpoints_drive_the_lifecycle(paths):
    life, clock = make()
    app = create_app(paths, life)
    with TestClient(app) as client:
        client.get("/")
        token = app.state.csrf_token
        assert client.get("/api/ping", params={"page": "p1"}).status_code == 200
        assert client.post("/api/closing", content='{"token":"wrong","page":"p1"}').status_code == 403
        assert client.post("/api/closing", content="not json").status_code == 400
        assert client.post("/api/closing", content=f'{{"token":"{token}","page":"p1"}}').status_code == 204
        clock.advance(15)
        assert life.should_exit()

    life, _ = make()
    app = create_app(paths, life)
    with TestClient(app) as client:
        client.get("/")
        assert client.post("/api/quit").status_code == 403  # CSRF header required
        assert not life.should_exit()
        assert client.post("/api/quit", headers={"X-CSRF-Token": app.state.csrf_token}).status_code == 200
        assert life.should_exit()
