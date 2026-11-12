"""OsteoCare AI backend: the API the website at osteocare-ai-mobility-monitor calls."""
import asyncio
import hashlib
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import analysis
from .auth import AuthError, current_user, verify_token
from .config import settings
from .db import Device, Profile, Reading, Report, SessionLocal, WalkSession, as_utc, get_db, init_db, iso, utcnow
from .live import hub
from .pdf import render_report_pdf


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="OsteoCare AI API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Device-Key"],
)


# ---------- helpers ----------

def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_key() -> str:
    return "oc_" + secrets.token_urlsafe(24)


def is_online(device: Device) -> bool:
    seen = hub.last_seen(device.id)
    if seen is None and device.last_seen_at:
        seen = as_utc(device.last_seen_at).timestamp()
    return seen is not None and time.time() - seen <= settings.online_window_seconds


def reading_out(r: Reading) -> dict:
    return {
        "id": r.id,
        "device_id": r.device_id,
        "timestamp": iso(r.timestamp),
        "heel_pressure": r.heel,
        "ball_pressure": r.ball,
        "toe_pressure": r.toe,
    }


def device_out(d: Device) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "board": d.board or "esp8266",
        "created_at": iso(d.created_at),
        "last_seen_at": iso(d.last_seen_at),
        "online": is_online(d),
    }


def session_out(s: WalkSession) -> dict:
    return {"id": s.id, "device_id": s.device_id, "started_at": iso(s.started_at), "ended_at": iso(s.ended_at)}


def report_out(r: Report) -> dict:
    return {"id": r.id, "device_id": r.device_id, "period": r.period, "created_at": iso(r.created_at),
            "analysis": r.analysis}


def own_device(db: Session, user: str, device_id: str) -> Device:
    d = db.get(Device, device_id)
    if not d or d.owner_id != user:
        raise HTTPException(404, "Insole not found. Pair it with your account first.")
    return d


def get_profile(db: Session, user: str) -> Profile:
    p = db.get(Profile, user)
    if not p:
        p = Profile(user_id=user, name="", daily_goal=20)
        db.add(p)
        db.commit()
    return p


# ---------- schemas ----------

Board = Literal["esp32", "esp8266"]


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    board: Board = "esp8266"


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    board: Board | None = None


class SessionIn(BaseModel):
    device_id: str


class ReportIn(BaseModel):
    device_id: str
    period: Literal["day", "week", "month"]


class ProfileIn(BaseModel):
    name: str = Field(default="", max_length=80)
    daily_goal: int = Field(ge=1, le=240)


class Sample(BaseModel):
    heel: int = Field(ge=0, le=analysis.ADC_MAX)
    ball: int = Field(ge=0, le=analysis.ADC_MAX)
    toe: int = Field(ge=0, le=analysis.ADC_MAX)
    age_ms: int = Field(default=0, ge=0, le=600_000, description="How long ago the sample was taken")


class IngestIn(BaseModel):
    """Either one sample ({heel, ball, toe}) or a batch ({readings: [...]})."""
    heel: int | None = Field(default=None, ge=0, le=analysis.ADC_MAX)
    ball: int | None = Field(default=None, ge=0, le=analysis.ADC_MAX)
    toe: int | None = Field(default=None, ge=0, le=analysis.ADC_MAX)
    readings: list[Sample] | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _one_form(self):
        if self.readings is None and None in (self.heel, self.ball, self.toe):
            raise ValueError("Send heel, ball and toe, or a readings list.")
        return self

    def samples(self) -> list[Sample]:
        return self.readings if self.readings is not None else [Sample(heel=self.heel, ball=self.ball, toe=self.toe)]


# ---------- routes ----------

@app.get("/")
def health():
    return {"status": "ok", "service": "OsteoCare AI API", "manage_insoles": "/manage"}


MANAGE_PAGE = Path(__file__).resolve().parent / "static" / "manage.html"


@app.get("/manage", include_in_schema=False)
def manage_page():
    """Page to add, view, rename, re-key and remove insoles (ESP32 / ESP8266)."""
    return FileResponse(MANAGE_PAGE, media_type="text/html")


@app.get("/api/public-config")
def public_config():
    """Public Supabase settings the manage page needs to sign in (the anon key is public by design)."""
    return {"supabase_url": settings.supabase_url, "supabase_anon_key": settings.supabase_anon_key}


@app.get("/api/devices")
def list_devices(user: str = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Device).where(Device.owner_id == user).order_by(Device.created_at)).all()
    return [device_out(d) for d in rows]


@app.post("/api/devices", status_code=201)
def create_device(body: DeviceIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    """Pairs a new insole. The device_key is shown only once: put it in the ESP32 firmware."""
    key = new_key()
    d = Device(id="insole-" + secrets.token_hex(4), owner_id=user, name=body.name.strip(),
               board=body.board, key_hash=hash_key(key))
    db.add(d)
    db.commit()
    return {**device_out(d), "device_key": key}


@app.patch("/api/devices/{device_id}")
def update_device(device_id: str, body: DeviceUpdate, user: str = Depends(current_user), db: Session = Depends(get_db)):
    """Rename an insole or change its board type (ESP32 / ESP8266)."""
    d = own_device(db, user, device_id)
    if body.name is not None:
        d.name = body.name.strip()
    if body.board is not None:
        d.board = body.board
    db.commit()
    return device_out(d)


@app.post("/api/devices/{device_id}/key")
def rotate_device_key(device_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    """Makes a new device key. The old key stops working immediately; flash the new one."""
    d = own_device(db, user, device_id)
    key = new_key()
    d.key_hash = hash_key(key)
    db.commit()
    return {**device_out(d), "device_key": key}


@app.delete("/api/devices/{device_id}", status_code=204)
def delete_device(device_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    d = own_device(db, user, device_id)
    for model in (Reading, WalkSession, Report):
        db.query(model).filter(model.device_id == d.id).delete()
    db.delete(d)
    db.commit()
    return Response(status_code=204)


def _store_samples(device_key: str, samples: list[Sample]) -> tuple[str, list[dict]]:
    with SessionLocal() as db:
        d = db.scalar(select(Device).where(Device.key_hash == hash_key(device_key)))
        if not d:
            raise HTTPException(401, "Unknown device key")
        now = utcnow()
        rows = [
            Reading(device_id=d.id, timestamp=now - timedelta(milliseconds=s.age_ms),
                    heel=s.heel, ball=s.ball, toe=s.toe)
            for s in sorted(samples, key=lambda s: -s.age_ms)  # oldest first
        ]
        db.add_all(rows)
        d.last_seen_at = now
        db.commit()
        return d.id, [reading_out(r) for r in rows]


@app.post("/api/ingest")
async def ingest(body: IngestIn, x_device_key: str = Header(..., alias="X-Device-Key")):
    """Called by the ESP32. Header: X-Device-Key. Values are raw ADC (0–4095)."""
    device_id, stored = await run_in_threadpool(_store_samples, x_device_key, body.samples())
    hub.mark_seen(device_id)
    for r in stored:
        hub.publish(device_id, {"online": True, "reading": r})
    return {"stored": len(stored)}


@app.get("/api/readings")
def list_readings(
    device_id: str,
    days: int = Query(1, ge=1, le=30),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    own_device(db, user, device_id)
    since = utcnow() - timedelta(days=days)
    rows = db.scalars(
        select(Reading)
        .where(Reading.device_id == device_id, Reading.timestamp >= since)
        .order_by(Reading.timestamp.desc(), Reading.id.desc())
        .limit(settings.max_readings + 1)
    ).all()
    truncated = len(rows) > settings.max_readings
    # Newest first; the website reverses the list itself.
    return {"readings": [reading_out(r) for r in rows[: settings.max_readings]], "truncated": truncated}


@app.get("/api/sessions")
def list_sessions(user: str = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(WalkSession).where(WalkSession.owner_id == user).order_by(WalkSession.started_at.desc()).limit(500)
    ).all()
    return [session_out(s) for s in rows]


@app.post("/api/sessions", status_code=201)
def start_session(body: SessionIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    d = own_device(db, user, body.device_id)
    if not is_online(d):
        raise HTTPException(409, "Your insole must be online to start a session.")
    active = db.scalar(select(WalkSession).where(WalkSession.owner_id == user, WalkSession.ended_at.is_(None)))
    if active:
        raise HTTPException(409, "A walking session is already running. Stop it first.")
    s = WalkSession(id=str(uuid.uuid4()), owner_id=user, device_id=d.id, started_at=utcnow())
    db.add(s)
    db.commit()
    return session_out(s)


@app.post("/api/sessions/{session_id}/stop")
def stop_session(session_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(WalkSession, session_id)
    if not s or s.owner_id != user:
        raise HTTPException(404, "Session not found.")
    if s.ended_at is None:
        s.ended_at = utcnow()
        db.commit()
    return session_out(s)


@app.get("/api/reports")
def list_reports(user: str = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Report).where(Report.owner_id == user).order_by(Report.created_at.desc()).limit(200)
    ).all()
    return [report_out(r) for r in rows]


@app.post("/api/reports", status_code=201)
def create_report(body: ReportIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    d = own_device(db, user, body.device_id)
    since = utcnow() - timedelta(days=analysis.PERIOD_DAYS[body.period])
    rows = db.execute(
        select(Reading.timestamp, Reading.heel, Reading.ball, Reading.toe)
        .where(Reading.device_id == d.id, Reading.timestamp >= since)
        .order_by(Reading.timestamp.desc(), Reading.id.desc())
        .limit(settings.max_readings + 1)
    ).all()
    truncated = len(rows) > settings.max_readings
    samples = [(as_utc(t), h, b, to) for t, h, b, to in reversed(rows[: settings.max_readings])]
    sessions = [
        (as_utc(s.started_at), as_utc(s.ended_at))
        for s in db.scalars(
            select(WalkSession).where(
                WalkSession.owner_id == user,
                WalkSession.device_id == d.id,
                WalkSession.started_at >= since,
                WalkSession.ended_at.is_not(None),
            )
        )
    ]
    try:
        result = analysis.analyse(
            samples, device_id=d.id, period=body.period, truncated=truncated,
            sessions=sessions, daily_goal=get_profile(db, user).daily_goal,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    r = Report(id=str(uuid.uuid4()), owner_id=user, device_id=d.id, period=body.period,
               created_at=utcnow(), analysis=result)
    db.add(r)
    db.commit()
    return report_out(r)


@app.get("/api/reports/{report_id}/pdf")
def report_pdf(report_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(Report, report_id)
    if not r or r.owner_id != user:
        raise HTTPException(404, "Report not found.")
    d = db.get(Device, r.device_id)
    created = as_utc(r.created_at).strftime("%d %b %Y, %H:%M UTC")
    pdf = render_report_pdf(report_out(r), d.name if d else r.device_id, created)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="osteocare-{r.period}-report.pdf"'},
    )


@app.get("/api/profile")
def read_profile(user: str = Depends(current_user), db: Session = Depends(get_db)):
    p = get_profile(db, user)
    return {"name": p.name, "daily_goal": p.daily_goal}


@app.put("/api/profile")
def update_profile(body: ProfileIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    p = get_profile(db, user)
    p.name = body.name.strip()
    p.daily_goal = body.daily_goal
    db.commit()
    return {"name": p.name, "daily_goal": p.daily_goal}


# ---------- live stream ----------

def _ws_setup(user: str, device_id: str) -> tuple[bool, dict | None]:
    with SessionLocal() as db:
        d = db.get(Device, device_id)
        if not d or d.owner_id != user:
            raise HTTPException(404)
        if d.last_seen_at:
            hub.mark_seen(d.id, as_utc(d.last_seen_at).timestamp())
        last = db.scalar(
            select(Reading).where(Reading.device_id == device_id).order_by(Reading.timestamp.desc()).limit(1)
        )
        return is_online(d), (reading_out(last) if last else None)


@app.websocket("/ws/live/{device_id}")
async def live(ws: WebSocket, device_id: str):
    """The website connects here, sends {"token": "<supabase access token>"} and then
    receives {"online": bool, "reading": {...} | null} messages (heartbeat every 3 s)."""
    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=10)
        user = await run_in_threadpool(verify_token, first.get("token"))
        online, last = await run_in_threadpool(_ws_setup, user, device_id)
    except (AuthError, HTTPException, asyncio.TimeoutError, ValueError, KeyError, AttributeError):
        await ws.close(code=4401)
        return
    except WebSocketDisconnect:
        return

    q = hub.subscribe(device_id)
    try:
        await ws.send_json({"online": online, "reading": last if online else None})
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=3)
            except asyncio.TimeoutError:
                seen = hub.last_seen(device_id)
                payload = {"online": bool(seen and time.time() - seen <= settings.online_window_seconds),
                           "reading": None}
            await ws.send_json(payload)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(device_id, q)
