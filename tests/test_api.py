"""Run with:  pytest -q   (uses a temporary SQLite database, no Supabase needed)."""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.auth import current_user  # noqa: E402

USER = "user-1"


@pytest.fixture()
def client(monkeypatch):
    main.app.dependency_overrides[current_user] = lambda: USER
    monkeypatch.setattr(main, "verify_token", lambda token: USER if token == "good" else (_ for _ in ()).throw(main.AuthError("bad")))
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def pair(client):
    r = client.post("/api/devices", json={"name": "Left insole"})
    assert r.status_code == 201
    return r.json()


def walk(client, key, n=300):
    batch = []
    for i in range(n):
        heel = 2500 if i % 10 < 3 else 200
        batch.append({"heel": heel, "ball": 900, "toe": 300, "age_ms": (n - i) * 100})
    for i in range(0, n, 100):
        r = client.post("/api/ingest", json={"readings": batch[i:i + 100]}, headers={"X-Device-Key": key})
        assert r.status_code == 200


def test_full_flow(client):
    dev = pair(client)
    assert dev["device_key"].startswith("oc_")
    assert client.get("/api/devices").json()[0]["online"] is False

    assert client.post("/api/ingest", json={"heel": 1, "ball": 2, "toe": 3}, headers={"X-Device-Key": "nope"}).status_code == 401
    assert client.post("/api/ingest", json={"heel": 5000, "ball": 2, "toe": 3}, headers={"X-Device-Key": dev["device_key"]}).status_code == 422

    walk(client, dev["device_key"])
    assert client.get("/api/devices").json()[0]["online"] is True

    rd = client.get("/api/readings", params={"device_id": dev["id"], "days": 1}).json()
    assert len(rd["readings"]) == 300 and rd["truncated"] is False
    first = rd["readings"][0]
    assert set(first) == {"id", "device_id", "timestamp", "heel_pressure", "ball_pressure", "toe_pressure"}
    assert rd["readings"][0]["timestamp"] >= rd["readings"][-1]["timestamp"]  # newest first

    s = client.post("/api/sessions", json={"device_id": dev["id"]})
    assert s.status_code == 201
    assert client.post("/api/sessions", json={"device_id": dev["id"]}).status_code == 409
    stopped = client.post(f"/api/sessions/{s.json()['id']}/stop").json()
    assert stopped["ended_at"]

    assert client.put("/api/profile", json={"name": "Darshan", "daily_goal": 30}).json()["daily_goal"] == 30
    assert client.put("/api/profile", json={"name": "x", "daily_goal": 999}).status_code == 422
    assert client.get("/api/profile").json() == {"name": "Darshan", "daily_goal": 30}

    rep = client.post("/api/reports", json={"device_id": dev["id"], "period": "day"})
    assert rep.status_code == 201
    a = rep.json()["analysis"]
    assert a["device_id"] == dev["id"] and a["samples"] == 300
    assert a["observations"] and a["recommendations"] and a["disclaimer"]
    assert a["steps_estimate"] == 30

    pdf = client.get(f"/api/reports/{rep.json()['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert len(client.get("/api/reports").json()) == 1


def test_live_socket(client):
    dev = pair(client)
    with client.websocket_connect(f"/ws/live/{dev['id']}") as ws:
        ws.send_json({"token": "good"})
        hello = ws.receive_json()
        assert hello == {"online": False, "reading": None}
        client.post("/api/ingest", json={"heel": 1200, "ball": 800, "toe": 100}, headers={"X-Device-Key": dev["device_key"]})
        msg = ws.receive_json()
        assert msg["online"] is True and msg["reading"]["heel_pressure"] == 1200


def test_other_users_cannot_see_device(client):
    dev = pair(client)
    main.app.dependency_overrides[current_user] = lambda: "someone-else"
    assert client.get("/api/readings", params={"device_id": dev["id"]}).status_code == 404
    assert client.post("/api/sessions", json={"device_id": dev["id"]}).status_code == 404


def test_report_without_data(client):
    dev = pair(client)
    r = client.post("/api/reports", json={"device_id": dev["id"], "period": "week"})
    assert r.status_code == 422 and "No sensor readings" in r.json()["detail"]
