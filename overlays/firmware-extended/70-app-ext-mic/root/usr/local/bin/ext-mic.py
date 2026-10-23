#!/usr/bin/env python3
"""Small dependency-free phone microphone service for acoustic measurements.

The service owns session files and raw PCM.  It does not execute G-code.  A
printer-side controller records phase markers and, if desired, asks for one of
the small set of named motion actions through /v1/run.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import ssl
import struct
import sys
import threading
import time
import uuid
from array import array
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = 1
SERVICE_INSTANCE_ID = secrets.token_hex(8)
SUPPORTED_SAMPLE_RATES = (44100, 48000)
MAX_JSON_BYTES = 64 * 1024
MAX_CHUNK_BYTES = 4 * 1024 * 1024
DEFAULT_IDLE_TIMEOUT_S = 600.0
SESSION_ID_RE = re.compile(r"^[0-9a-f]{12,32}$")
PHASE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
ALLOWED_ACTIONS = {
    "idle": {"duration_s": (0.0, 120.0)},
    "circle_probe": {
        "radius_mm": (5.0, 120.0),
        "speed_mm_s": (1.0, 600.0),
        "accel_mm_s2": (50.0, 22000.0),
        "turns": (1.0, 20.0),
    },
    "line_x": {
        "distance_mm": (1.0, 240.0),
        "speed_mm_s": (1.0, 600.0),
        "accel_mm_s2": (50.0, 22000.0),
    },
    "line_y": {
        "distance_mm": (1.0, 240.0),
        "speed_mm_s": (1.0, 600.0),
        "accel_mm_s2": (50.0, 22000.0),
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_seconds(value: Any) -> float | None:
    """Parse a persisted UTC timestamp for the stale-session check."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def write_json(path: Path, value: Any) -> None:
    """Replace a JSON file atomically so a power loss cannot leave half JSON."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def as_number(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} is below {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} is above {maximum}")
    return number


def as_int(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} is below {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} is above {maximum}")
    return value


def settings_incomplete(metadata: dict[str, Any]) -> bool:
    """Missing or enabled browser DSP settings make amplitude comparisons weak."""
    track = metadata.get("track_settings")
    context_rate = metadata.get("audio_context_sample_rate")
    if not isinstance(track, dict) or context_rate not in SUPPORTED_SAMPLE_RATES:
        return True
    capture = metadata.get("capture_settings")
    if isinstance(capture, dict):
        if capture.get("sample_rate") != context_rate or capture.get("channels") != 1:
            return True
        dsp = capture.get("dsp")
        constraints = metadata.get("constraints")
        requested_off = isinstance(constraints, dict) and all(constraints.get(name) is False for name in ("echoCancellation", "noiseSuppression", "autoGainControl"))
        if requested_off and capture.get("capabilities_available") is True and isinstance(dsp, dict):
            if all(dsp.get(name) is False or dsp.get(name) == "unsupported" for name in ("echoCancellation", "noiseSuppression", "autoGainControl")):
                return False
    if track.get("sampleRate") not in SUPPORTED_SAMPLE_RATES:
        return True
    if track.get("channelCount") != 1:
        return True
    return any(track.get(name) is not False for name in ("echoCancellation", "noiseSuppression", "autoGainControl"))


def dbfs(sum_squares: int, samples: int) -> float:
    if samples <= 0 or sum_squares <= 0:
        return -120.0
    rms = math.sqrt(sum_squares / samples) / 32768.0
    return max(-120.0, 20.0 * math.log10(rms))


def quality_from(status: dict[str, Any]) -> dict[str, Any]:
    chunks = int(status.get("chunks_accepted", 0))
    gaps = int(status.get("gap_count", 0))
    samples = int(status.get("sample_count", 0))
    clip_samples = int(status.get("clip_samples", 0))
    packets = chunks + gaps
    if samples == 0:
        quality_status = "WAITING"
    elif status.get("sample_rate") not in SUPPORTED_SAMPLE_RATES or status.get("channels") != 1:
        quality_status = "INVALID"
    elif status.get("settings_incomplete") or (gaps / packets if packets else 0.0) >= 0.01 or (clip_samples / samples if samples else 0.0) >= 0.005:
        quality_status = "LOW_CONFIDENCE"
    else:
        quality_status = "OK"
    return {
        "status": quality_status,
        "settings_incomplete": bool(status.get("settings_incomplete", False)),
        "packet_loss": (gaps / packets) if packets else 0.0,
        "clip_ratio": (clip_samples / samples) if samples else 0.0,
        "rms_dbfs": dbfs(int(status.get("sum_squares", 0)), samples),
        "sample_rate": status.get("sample_rate"),
        "channels": status.get("channels"),
    }


class ServiceError(Exception):
    def __init__(self, status: int, message: str, *, details: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details


class Session:
    def __init__(self, root: Path, metadata: dict[str, Any], token_hash: str, idle_timeout_s: float):
        self.root = root
        self.metadata = metadata
        self.token_hash = token_hash
        self.idle_timeout_s = idle_timeout_s
        self.lock = threading.RLock()
        self.status: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session_id": metadata["session_id"],
            "state": "OPEN",
            "created_utc": metadata["created_utc"],
            "last_activity_utc": metadata["created_utc"],
            "chunks_accepted": 0,
            "gap_count": 0,
            "expected_seq": 0,
            "last_seq": None,
            "last_received_utc": None,
            "receive_rate_bps": 0.0,
            "byte_count": 0,
            "sample_count": 0,
            "clip_samples": 0,
            "sum_squares": 0,
            "last_chunk_rms_dbfs": -120.0,
            "last_chunk_clip_ratio": 0.0,
            "sample_rate": None,
            "channels": None,
            "phase_id": None,
            "gap_ranges": [],
            "packet_loss": 0.0,
            "clip_ratio": 0.0,
            "rms_dbfs": -120.0,
            "settings_incomplete": bool(metadata.get("settings_incomplete", False)),
        }
        self.phases: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self._last_received_monotonic_ns: int | None = None

    @classmethod
    def load(cls, session_dir: Path, idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S) -> "Session":
        metadata = read_json(session_dir / "session.json")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("session_id"), str):
            raise ServiceError(404, "session not found")
        token_hash = metadata.get("token_hash")
        if not isinstance(token_hash, str):
            raise ServiceError(500, "session token hash is missing")
        session = cls(session_dir, metadata, token_hash, idle_timeout_s)
        stored = read_json(session_dir / "status.json", {})
        if isinstance(stored, dict):
            session.status.update(stored)
        phases = read_json(session_dir / "phases.json", [])
        if isinstance(phases, list):
            session.phases = phases
        runs = read_json(session_dir / "runs.json", [])
        if isinstance(runs, list):
            session.runs = runs
        return session

    @property
    def session_id(self) -> str:
        return self.metadata["session_id"]

    def check_token(self, token: str | None) -> None:
        if not token or not hmac.compare_digest(hashlib.sha256(token.encode("utf-8")).hexdigest(), self.token_hash):
            raise ServiceError(401, "invalid session token")

    def save_status(self) -> None:
        status = dict(self.status)
        status.update(quality_from(status))
        self.status.update(status)
        write_json(self.root / "status.json", status)

    def _touch(self) -> None:
        self.status["last_activity_utc"] = utc_now()

    def expire_if_idle(self, now: float | None = None) -> bool:
        """Finalize a disconnected OPEN session without deleting its capture."""
        with self.lock:
            if self.status.get("state") != "OPEN":
                return False
            timestamp = (
                self.status.get("last_activity_utc")
                or self.status.get("last_received_utc")
                or self.metadata.get("created_utc")
            )
            last = utc_seconds(timestamp)
            if last is None:
                last = self.root.joinpath("status.json").stat().st_mtime
            current = time.time() if now is None else now
            if current - last <= self.idle_timeout_s:
                return False
            self.status["state"] = "EXPIRED"
            self.status["expired_utc"] = utc_now()
            self.status["expiration_reason"] = "idle_timeout"
            self.save_status()
            self._write_manifest()
            return True

    def append_chunk(self, seq: int, sample_count: int, sample_rate: int, channels: int, payload: bytes) -> dict[str, Any]:
        with self.lock:
            if self.status["state"] != "OPEN":
                raise ServiceError(409, f"session is {self.status['state'].lower()}")
            expected_bytes = sample_count * channels * 2
            if expected_bytes != len(payload):
                raise ServiceError(400, "sample_count, channels and body length do not agree", details={
                    "expected_bytes": expected_bytes,
                    "actual_bytes": len(payload),
                })
            if seq < self.status["expected_seq"]:
                raise ServiceError(409, "duplicate or late sequence number", details={"expected_seq": self.status["expected_seq"]})
            if sample_rate not in SUPPORTED_SAMPLE_RATES:
                raise ServiceError(400, "sample_rate must be 44100 or 48000")
            if channels != 1:
                raise ServiceError(400, "channels must be mono (1)")
            previous_rate = self.status["sample_rate"]
            previous_channels = self.status["channels"]
            if previous_rate is not None and (sample_rate != previous_rate or channels != previous_channels):
                raise ServiceError(409, "sample rate and channels must stay constant within a session")

            gap = seq - self.status["expected_seq"]
            byte_offset = self.status["byte_count"]
            values = array("h")
            values.frombytes(payload)
            if sys.byteorder != "little":
                values.byteswap()
            clip_samples = sum(1 for value in values if value in (-32768, 32767))
            sum_squares = sum(value * value for value in values)
            with (self.root / "audio.raw").open("ab") as audio:
                audio.write(payload)
                audio.flush()
                os.fsync(audio.fileno())

            if gap:
                self.status["gap_ranges"].append({
                    "start_seq": self.status["expected_seq"],
                    "end_seq": seq - 1,
                    "count": gap,
                })

            self.status["chunks_accepted"] += 1
            self.status["gap_count"] += gap
            self.status["expected_seq"] = seq + 1
            self.status["last_seq"] = seq
            self.status["byte_count"] += len(payload)
            self.status["sample_count"] += sample_count
            self.status["clip_samples"] += clip_samples
            self.status["sum_squares"] += sum_squares
            self.status["last_chunk_rms_dbfs"] = dbfs(sum_squares, len(values))
            self.status["last_chunk_clip_ratio"] = clip_samples / len(values) if values else 0.0
            self.status["sample_rate"] = sample_rate
            self.status["channels"] = channels
            received_ns = time.monotonic_ns()
            previous_received_ns = self._last_received_monotonic_ns
            self._last_received_monotonic_ns = received_ns
            self.status["last_received_utc"] = utc_now()
            self.status["last_activity_utc"] = self.status["last_received_utc"]
            if previous_received_ns is not None and received_ns > previous_received_ns:
                self.status["receive_rate_bps"] = len(payload) * 8 / ((received_ns - previous_received_ns) / 1_000_000_000)
            else:
                self.status["receive_rate_bps"] = 0.0
            chunk_record = {
                "seq": seq,
                "sample_count": sample_count,
                "byte_offset": byte_offset,
                "byte_count": len(payload),
                "received_monotonic_ns": time.monotonic_ns(),
                "sample_rate": sample_rate,
                "channels": channels,
            }
            with (self.root / "chunks.jsonl").open("a", encoding="utf-8", newline="\n") as chunks:
                chunks.write(json.dumps(chunk_record, separators=(",", ":")) + "\n")
                chunks.flush()
                os.fsync(chunks.fileno())
            self.save_status()
            return {
                "accepted": True,
                "seq": seq,
                "gap_count": self.status["gap_count"],
                "byte_count": self.status["byte_count"],
                "sample_count": self.status["sample_count"],
                "quality": quality_from(self.status),
            }

    def add_phase(self, phase_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.status["state"] != "OPEN":
                raise ServiceError(409, f"session is {self.status['state'].lower()}")
            if not PHASE_ID_RE.fullmatch(phase_id):
                raise ServiceError(400, "phase_id contains unsupported characters")
            marker = {
                "phase_id": phase_id,
                "server_time_utc": utc_now(),
                "server_time_ns": time.monotonic_ns(),
                "byte_offset": self.status["byte_count"],
                "sample_offset": self.status["sample_count"],
                "metadata": metadata,
            }
            self.phases.append(marker)
            self.status["phase_id"] = phase_id
            self._touch()
            write_json(self.root / "phases.json", self.phases)
            self.save_status()
            return marker

    def add_run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.status["state"] != "OPEN":
                raise ServiceError(409, f"session is {self.status['state'].lower()}")
            if action not in ALLOWED_ACTIONS:
                raise ServiceError(400, "action is not in the printer motion allowlist", details={"allowed": sorted(ALLOWED_ACTIONS)})
            if not isinstance(params, dict):
                raise ServiceError(400, "params must be an object")
            rules = ALLOWED_ACTIONS[action]
            unknown = sorted(set(params) - set(rules))
            if unknown:
                raise ServiceError(400, "unknown motion parameter", details={"unknown": unknown})
            normalized: dict[str, float] = {}
            for name, bounds in rules.items():
                if name in params:
                    normalized[name] = as_number(params[name], name, minimum=bounds[0], maximum=bounds[1])
            run = {
                "run_id": uuid.uuid4().hex[:16],
                "action": action,
                "params": normalized,
                "requested_utc": utc_now(),
                "status": "RECORDED_ONLY",
                "execution": "recorded_only",
            }
            self.runs.append(run)
            self._touch()
            write_json(self.root / "runs.json", self.runs)
            self.save_status()
            return run

    def _write_manifest(self) -> None:
        manifest = dict(self.metadata)
        manifest.pop("token_hash", None)
        manifest["status"] = self.status
        manifest["phases"] = self.phases
        manifest["runs"] = self.runs
        manifest["files"] = {
            "audio": "audio.raw",
            "status": "status.json",
            "phases": "phases.json",
            "runs": "runs.json",
            "chunks": "chunks.jsonl",
            "manifest": "manifest.json",
        }
        write_json(self.root / "manifest.json", manifest)

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if self.status["state"] == "OPEN":
                self.status["state"] = "STOPPED"
                self.status["stopped_utc"] = utc_now()
                self.save_status()
                self._write_manifest()
            return {"state": self.status["state"], "manifest": str(self.root / "manifest.json")}

    def public_status(self) -> dict[str, Any]:
        with self.lock:
            result = dict(self.status)
            result.pop("sum_squares", None)
            result["quality"] = quality_from(self.status)
            return result


class SessionStore:
    def __init__(self, root: Path, idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S):
        self.root = root
        self.idle_timeout_s = idle_timeout_s
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.create_lock = threading.Lock()
        self.sessions: dict[str, Session] = {}
        self._stop_sessions_left_open_by_previous_process()

    def _stop_sessions_left_open_by_previous_process(self) -> None:
        """Finalize sessions that belonged to a process which has restarted."""
        for child in self.root.iterdir():
            if not child.is_dir() or not SESSION_ID_RE.fullmatch(child.name):
                continue
            try:
                session = Session.load(child, self.idle_timeout_s)
            except ServiceError:
                continue
            if session.status.get("state") != "OPEN":
                continue
            session.status["stop_reason"] = "service_restart"
            session.stop()

    def create(self, metadata: dict[str, Any]) -> tuple[Session, str]:
        with self.create_lock:
            return self._create(metadata)

    def _create(self, metadata: dict[str, Any]) -> tuple[Session, str]:
        if not isinstance(metadata, dict):
            raise ServiceError(400, "request body must be a JSON object")
        with self.lock:
            active = self.active()
            replace_existing = metadata.get("replace_existing") is True
            if active is not None:
                if not replace_existing:
                    raise ServiceError(409, "another session is already active", details={"session_id": active.session_id})
                active.stop()
            session_id = uuid.uuid4().hex[:16]
            token = secrets.token_urlsafe(24)
            now = utc_now()
            clean = dict(metadata)
            clean.pop("replace_existing", None)
            clean.update({
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "created_utc": now,
                "source": "phone_internal",
                "transport": "pcm_chunk",
                "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "settings_incomplete": settings_incomplete(metadata),
            })
            session_dir = self.root / session_id
            session_dir.mkdir(mode=0o700)
            write_json(session_dir / "session.json", clean)
            session = Session(session_dir, clean, clean["token_hash"], self.idle_timeout_s)
            write_json(session_dir / "phases.json", [])
            write_json(session_dir / "runs.json", [])
            session.save_status()
            self.sessions[session_id] = session
            return session, token

    def active(self) -> Session | None:
        """Return the one live OPEN session."""
        for child in self.root.iterdir():
            if not child.is_dir() or not SESSION_ID_RE.fullmatch(child.name):
                continue
            try:
                session = self.sessions.get(child.name) or Session.load(child, self.idle_timeout_s)
            except ServiceError:
                continue
            self.sessions[child.name] = session
            if session.expire_if_idle():
                continue
            if session.status.get("state") == "OPEN":
                return session
        return None

    def get(self, session_id: str) -> Session:
        if not SESSION_ID_RE.fullmatch(session_id):
            raise ServiceError(404, "session not found")
        with self.lock:
            if session_id in self.sessions:
                session = self.sessions[session_id]
            else:
                session = Session.load(self.root / session_id, self.idle_timeout_s)
                self.sessions[session_id] = session
            session.expire_if_idle()
            return session


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "ExtMicService/0.1"

    @property
    def store(self) -> SessionStore:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, status: int, payload: Any, *, content_type: str = "application/json; charset=utf-8") -> None:
        if content_type.startswith("application/json"):
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        else:
            body = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, error: ServiceError) -> None:
        payload: dict[str, Any] = {"error": error.message}
        if error.details is not None:
            payload["details"] = error.details
        self._send(error.status, payload)

    def _body(self, limit: int) -> bytes:
        value = self.headers.get("Content-Length")
        try:
            length = int(value) if value is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            raise ServiceError(411, "Content-Length is required")
        if length > limit:
            raise ServiceError(413, "request body is too large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ServiceError(400, "request body ended early")
        return body

    def _json(self, limit: int = MAX_JSON_BYTES) -> dict[str, Any]:
        body = self._body(limit)
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError(400, "request body must be valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ServiceError(400, "request body must be a JSON object")
        return value

    def _token(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def _phone_revision(self) -> str:
        """Return the current page fingerprint so a phone can reload safely."""
        try:
            page = self.server.phone_html.read_bytes()  # type: ignore[attr-defined]
        except OSError:
            return "unavailable"
        return hashlib.sha256(page).hexdigest()[:16]

    def _session(self, session_id: str) -> Session:
        session = self.store.get(session_id)
        session.check_token(self._token())
        return session

    def _active_controller_session(self) -> Session:
        """Return the phone-owned session to the printer-side controller.

        The controller runs beside this service and uses an explicit header so
        it can add phase markers without learning the phone's bearer token.
        The endpoint never returns that token and cannot create a session.
        """
        if self.headers.get("X-Printer-Controller") != "local":
            raise ServiceError(403, "printer controller header is required")
        session = self.store.active()
        if session is None:
            raise ServiceError(404, "no active phone session")
        return session

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path in ("/", "/ext-mic"):
                page_path = self.server.phone_html  # type: ignore[attr-defined]
                if not page_path.is_file():
                    raise ServiceError(404, "phone page not found")
                page = page_path.read_bytes()
                self._send(200, page, content_type="text/html; charset=utf-8")
                return
            if path == "/v1/status":
                self._send(200, {
                    "service": "ext-mic",
                    "schema_version": SCHEMA_VERSION,
                    "instance_id": SERVICE_INSTANCE_ID,
                    "phone_revision": self._phone_revision(),
                    "time_utc": utc_now(),
                })
                return
            if path == "/healthz":
                self._send(200, {
                    "status": "ok",
                    "service": "ext-mic",
                    "schema_version": SCHEMA_VERSION,
                    "instance_id": SERVICE_INSTANCE_ID,
                    "phone_revision": self._phone_revision(),
                })
                return
            if path == "/v1/mic/active/status":
                self._send(200, self._active_controller_session().public_status())
                return
            match = re.fullmatch(r"/v1/mic/([0-9a-f]{12,32})/status", path)
            if match:
                session = self._session(match.group(1))
                self._send(200, session.public_status())
                return
            raise ServiceError(404, "not found")
        except ServiceError as error:
            self._error(error)
        except Exception as exc:  # keep the service alive on one bad request
            self._error(ServiceError(500, "internal server error", details=str(exc)))

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path == "/v1/mic/session":
                body = self._json()
                session, token = self.store.create(body)
                self._send(201, {
                    "session_id": session.session_id,
                    "token": token,
                    "source": "phone_internal",
                    "transport": "pcm_chunk",
                    "status_url": f"/v1/mic/{session.session_id}/status",
                    "chunk_url": f"/v1/mic/{session.session_id}/chunk",
                    "phase_url": f"/v1/mic/{session.session_id}/phase",
                })
                return
            if path in ("/v1/mic/active/phase", "/v1/mic/active/run"):
                session = self._active_controller_session()
                operation = path.rsplit("/", 1)[-1]
                body = self._json()
                if operation == "phase":
                    allowed = {"phase_id", "metadata"}
                    unknown = sorted(set(body) - allowed)
                    if unknown:
                        raise ServiceError(400, "unknown phase field", details={"unknown": unknown})
                    phase_id = body.get("phase_id")
                    metadata = body.get("metadata", {})
                    if not isinstance(phase_id, str) or not isinstance(metadata, dict):
                        raise ServiceError(400, "phase_id and metadata are required")
                    if len(json.dumps(metadata, ensure_ascii=False)) > 16 * 1024:
                        raise ServiceError(413, "phase metadata is too large")
                    self._send(200, session.add_phase(phase_id, metadata))
                    return
                allowed = {"action", "params"}
                unknown = sorted(set(body) - allowed)
                if unknown:
                    raise ServiceError(400, "unknown run field", details={"unknown": unknown})
                action = body.get("action")
                if not isinstance(action, str):
                    raise ServiceError(400, "action is required")
                self._send(200, session.add_run(action, body.get("params", {})))
                return
            match = re.fullmatch(r"/v1/mic/([0-9a-f]{12,32})/(chunk|phase)", path)
            if match:
                session = self._session(match.group(1))
                operation = match.group(2)
                if operation == "chunk":
                    self._handle_chunk(session)
                elif operation == "phase":
                    body = self._json()
                    allowed = {"phase_id", "metadata"}
                    unknown = sorted(set(body) - allowed)
                    if unknown:
                        raise ServiceError(400, "unknown phase field", details={"unknown": unknown})
                    phase_id = body.get("phase_id")
                    if not isinstance(phase_id, str):
                        raise ServiceError(400, "phase_id is required")
                    metadata = body.get("metadata", {})
                    if not isinstance(metadata, dict):
                        raise ServiceError(400, "metadata must be an object")
                    if len(json.dumps(metadata, ensure_ascii=False)) > 16 * 1024:
                        raise ServiceError(413, "phase metadata is too large")
                    self._send(200, session.add_phase(phase_id, metadata))
                return
            if path == "/v1/run":
                body = self._json()
                allowed = {"session_id", "action", "params"}
                unknown = sorted(set(body) - allowed)
                if unknown:
                    raise ServiceError(400, "unknown run field", details={"unknown": unknown})
                session_id = body.get("session_id")
                if not isinstance(session_id, str):
                    raise ServiceError(400, "session_id is required")
                session = self._session(session_id)
                action = body.get("action")
                if not isinstance(action, str):
                    raise ServiceError(400, "action is required")
                self._send(200, session.add_run(action, body.get("params", {})))
                return
            raise ServiceError(404, "not found")
        except ServiceError as error:
            self._error(error)
        except Exception as exc:
            self._error(ServiceError(500, "internal server error", details=str(exc)))

    def _handle_chunk(self, session: Session) -> None:
        def header_int(name: str, *, minimum: int, maximum: int) -> int:
            raw = self.headers.get(name)
            try:
                value = int(raw) if raw is not None else -1
            except ValueError:
                value = -1
            try:
                return as_int(value, name, minimum=minimum, maximum=maximum)
            except ValueError as exc:
                raise ServiceError(400, str(exc)) from exc

        seq = header_int("X-Seq", minimum=0, maximum=2**53 - 1)
        sample_count = header_int("X-Sample-Count", minimum=1, maximum=2**24)
        sample_rate = header_int("X-Sample-Rate", minimum=44100, maximum=48000)
        channels = header_int("X-Channels", minimum=1, maximum=1)
        payload = self._body(MAX_CHUNK_BYTES)
        result = session.append_chunk(seq, sample_count, sample_rate, channels, payload)
        self._send(200, result)


def make_server(host: str, port: int, root: Path, certfile: str | None = None, keyfile: str | None = None, phone_html: str | None = None, idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S) -> ThreadingHTTPServer:
    if not math.isfinite(idle_timeout_s) or idle_timeout_s <= 0:
        raise ValueError("idle_timeout_s must be positive")
    server = ThreadingHTTPServer((host, port), RequestHandler)
    server.store = SessionStore(root, idle_timeout_s)  # type: ignore[attr-defined]
    server.phone_html = Path(phone_html) if phone_html else Path(__file__).with_name("phone.html")  # type: ignore[attr-defined]
    if bool(certfile) != bool(keyfile):
        server.server_close()
        raise ValueError("certfile and keyfile must be supplied together")
    if certfile and keyfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="bind address; use 0.0.0.0 on the printer LAN")
    parser.add_argument("--port", type=int, default=9443)
    parser.add_argument("--captures", type=Path, default=Path("/userdata/ext-mic/runs"))
    parser.add_argument("--certfile", type=Path, default=Path("/home/lava/printer_data/config/extended/ext-mic/server.crt"))
    parser.add_argument("--keyfile", type=Path, default=Path("/home/lava/printer_data/config/extended/ext-mic/server.key"))
    parser.add_argument("--phone-html", type=Path, help="phone page to serve; defaults to phone.html beside server.py")
    parser.add_argument("--idle-timeout-s", type=float, default=DEFAULT_IDLE_TIMEOUT_S, help="expire disconnected sessions after this many seconds")
    parser.add_argument("--insecure-http", action="store_true", help="development only; do not use this on the printer LAN")
    args = parser.parse_args(argv)
    certfile = None if args.insecure_http else str(args.certfile)
    keyfile = None if args.insecure_http else str(args.keyfile)
    server = make_server(args.host, args.port, args.captures, certfile, keyfile, str(args.phone_html) if args.phone_html else None, args.idle_timeout_s)
    scheme = "http" if args.insecure_http else "https"
    print(f"Ext Mic listening at {scheme}://{args.host}:{args.port}/ext-mic", flush=True)
    print(f"Capture root: {args.captures.resolve()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping", flush=True)
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
