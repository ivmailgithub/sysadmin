#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wyze_server.py
==============
Standalone Wyze Multi-Cam Viewer for Windows
Features:
  • Direct RTSP and RTSPS (over TLS) stream ingestion for Wyze cameras
  • Multi-camera Birdseye view with customizable grid layouts
  • Independent subwindow / pop-out browser windows for multi-monitor tiling
  • Automatic credential injection & percent-encoding for passwords with '@'
  • Low-latency OpenCV FFmpeg MJPEG streaming proxy
  • Camera reachability test and discovery helper
  • Persistent configuration in wyze_config.yaml (auto-imports from fleet_config.yaml)
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from flask import Flask, Response, jsonify, render_template_string, request

# ── Logging Configuration ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [WyzeViewer] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("WyzeViewer")

# ── Global Constants & Defaults ──────────────────────────────────────────────
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "wyze_config.yaml"
FLEET_CONFIG_PATH = Path(__file__).resolve().parent / "fleet_config.yaml"
DEFAULT_PORT = 5005
DEFAULT_HOST = "0.0.0.0"

# ── RTSP URL Formatter ───────────────────────────────────────────────────────
def format_wyze_rtsp_url(url: str, default_user: str = "", default_password: str = "") -> str:
    """
    Sanitizes and formats a Wyze RTSP/RTSPS URL:
    - Normalizes scheme typos like 'rstps://' or 'rstp://' -> 'rtsps://' / 'rtsp://'
    - Injects default credentials if none provided in the URL
    - Percent-encodes special characters in username & password (e.g. '@' -> '%40')
    - Ensures valid authority and path for OpenCV/FFmpeg
    """
    if not url:
        return ""
    url = url.strip()
    # Normalize common transposed scheme typos
    url = re.sub(r"^rstp(s)?://", r"rtsp\1://", url, flags=re.IGNORECASE)
    if not url.startswith(("rtsp://", "rtsps://", "http://", "https://")):
        url = "rtsps://" + url

    m = re.match(r"^(rtsps?://)(?:([^:]+):(.*)@)?([^/@:]+(?::\d+)?)(/.*)?$", url)
    if m:
        scheme, u, p, host_port, path = m.groups()
        path = path or "/stream0"
        if u and p is not None:
            p_decoded = urllib.parse.unquote(p)
            p_encoded = urllib.parse.quote(p_decoded, safe="")
            u_decoded = urllib.parse.unquote(u)
            u_encoded = urllib.parse.quote(u_decoded, safe="")
            return f"{scheme}{u_encoded}:{p_encoded}@{host_port}{path}"
        elif default_user and default_password:
            p_encoded = urllib.parse.quote(default_password, safe="")
            u_encoded = urllib.parse.quote(default_user, safe="")
            return f"{scheme}{u_encoded}:{p_encoded}@{host_port}{path}"
    return url


# ── Configuration Manager ────────────────────────────────────────────────────
class WyzeConfigManager:
    def __init__(self, config_file: Path = DEFAULT_CONFIG_PATH):
        self.config_file = Path(config_file)
        self.lock = threading.Lock()
        self.server_port = DEFAULT_PORT
        self.server_host = DEFAULT_HOST
        self.auto_open_browser = True
        self.default_user = "myw"
        self.default_password = ""
        self.cameras: List[Dict[str, Any]] = []
        self.load()

    def load(self):
        with self.lock:
            if not self.config_file.exists():
                self._import_from_fleet_or_defaults()
                self._save_unlocked()
                return

            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                server_sec = data.get("server", {})
                self.server_port = int(server_sec.get("port", DEFAULT_PORT))
                self.server_host = str(server_sec.get("host", DEFAULT_HOST))
                self.auto_open_browser = bool(server_sec.get("auto_open_browser", True))

                creds_sec = data.get("credentials", {})
                self.default_user = str(creds_sec.get("default_user", "myw"))
                self.default_password = str(creds_sec.get("default_password", ""))

                self.cameras = []
                for c in data.get("cameras", []):
                    if isinstance(c, dict) and c.get("host"):
                        cam_id = str(c.get("id") or f"cam_{len(self.cameras) + 1}")
                        self.cameras.append({
                            "id": cam_id,
                            "label": str(c.get("label", f"Wyze Cam {len(self.cameras) + 1}")),
                            "host": str(c.get("host", "")).strip(),
                            "port": int(c.get("port", 322)),
                            "path": str(c.get("path", "/stream0")).strip(),
                            "scheme": str(c.get("scheme", "rtsps")).strip().lower(),
                            "rtsp_url": str(c.get("rtsp_url", "")).strip(),
                            "user": str(c.get("user", "")).strip(),
                            "password": str(c.get("password", "")).strip(),
                            "enabled": bool(c.get("enabled", True)),
                        })
                log.info("Loaded config from %s (%d cameras configured)", self.config_file.name, len(self.cameras))
            except Exception as e:
                log.error("Failed to load %s: %s", self.config_file, e)
                self._import_from_fleet_or_defaults()

    def _import_from_fleet_or_defaults(self):
        log.info("Initializing Wyze config from fleet_config.yaml / defaults...")
        imported_user = "myw"
        imported_pass = ""
        imported_cams = []

        if FLEET_CONFIG_PATH.exists():
            try:
                with open(FLEET_CONFIG_PATH, "r", encoding="utf-8") as f:
                    fleet_data = yaml.safe_load(f) or {}
                imported_user = fleet_data.get("wyze_user", imported_user)
                imported_pass = fleet_data.get("wyze_password", imported_pass)
                for s in fleet_data.get("scopes", []):
                    if isinstance(s, dict):
                        dev = str(s.get("device_type", "")).lower()
                        rtsp = str(s.get("rtsp_url", ""))
                        if dev in ("wyze", "rtsp") or "stream0" in rtsp or ":322" in rtsp:
                            host = s.get("host", "192.168.1.174")
                            m_port = re.search(r":(\d+)", rtsp)
                            port = int(m_port.group(1)) if m_port else 322
                            m_path = re.search(r":\d+(/.*)$", rtsp)
                            path = m_path.group(1) if m_path else "/stream0"
                            scheme = "rtsps" if "rtsps" in rtsp else "rtsp"
                            imported_cams.append({
                                "id": "cam_1",
                                "label": s.get("label", "Wyze Cam 174"),
                                "host": host,
                                "port": port,
                                "path": path,
                                "scheme": scheme,
                                "rtsp_url": rtsp or f"{scheme}://{host}:{port}{path}",
                                "user": "",
                                "password": "",
                                "enabled": True,
                            })
            except Exception as e:
                log.warning("Could not read fleet_config.yaml: %s", e)

        self.default_user = imported_user
        self.default_password = imported_pass
        if not imported_cams:
            imported_cams.append({
                "id": "cam_1",
                "label": "Wyze Cam (174)",
                "host": "192.168.1.174",
                "port": 322,
                "path": "/stream0",
                "scheme": "rtsps",
                "rtsp_url": "rtsps://192.168.1.174:322/stream0",
                "user": "",
                "password": "",
                "enabled": True,
            })
        self.cameras = imported_cams

    def _save_unlocked(self):
        data = {
            "server": {
                "port": self.server_port,
                "host": self.server_host,
                "auto_open_browser": self.auto_open_browser,
            },
            "credentials": {
                "default_user": self.default_user,
                "default_password": self.default_password,
            },
            "cameras": self.cameras,
        }
        tmp_file = self.config_file.with_suffix(".yaml.tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            if tmp_file.exists():
                tmp_file.replace(self.config_file)
            log.info("Saved configuration to %s (%d cameras)", self.config_file.name, len(self.cameras))
        except Exception as e:
            log.error("Failed to save config: %s", e)

    def save(self):
        with self.lock:
            self._save_unlocked()

    def get_camera(self, cam_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            for c in self.cameras:
                if c["id"] == cam_id:
                    return dict(c)
            return None

    def upsert_camera(self, cam_data: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            cam_id = str(cam_data.get("id") or "").strip()
            if not cam_id:
                cam_id = f"cam_{int(time.time()*1000)%100000}"
                cam_data["id"] = cam_id

            host = str(cam_data.get("host", "")).strip()
            port = int(cam_data.get("port", 322))
            path = str(cam_data.get("path", "/stream0")).strip()
            if not path.startswith("/"):
                path = "/" + path
            scheme = str(cam_data.get("scheme", "rtsps")).strip().lower()
            if scheme not in ("rtsp", "rtsps"):
                scheme = "rtsps"

            raw_url = str(cam_data.get("rtsp_url", "")).strip()
            if not raw_url:
                raw_url = f"{scheme}://{host}:{port}{path}"

            item = {
                "id": cam_id,
                "label": str(cam_data.get("label", f"Wyze Cam {cam_id}")).strip(),
                "host": host,
                "port": port,
                "path": path,
                "scheme": scheme,
                "rtsp_url": raw_url,
                "user": str(cam_data.get("user", "")).strip(),
                "password": str(cam_data.get("password", "")).strip(),
                "enabled": bool(cam_data.get("enabled", True)),
            }

            for i, c in enumerate(self.cameras):
                if c["id"] == cam_id:
                    self.cameras[i] = item
                    self._save_unlocked()
                    return item

            self.cameras.append(item)
            self._save_unlocked()
            return item

    def delete_camera(self, cam_id: str) -> bool:
        with self.lock:
            orig_len = len(self.cameras)
            self.cameras = [c for c in self.cameras if c["id"] != cam_id]
            if len(self.cameras) < orig_len:
                self._save_unlocked()
                return True
            return False

    def build_effective_url(self, cam: Dict[str, Any]) -> str:
        raw = cam.get("rtsp_url")
        if not raw:
            scheme = cam.get("scheme", "rtsps")
            host = cam.get("host", "")
            port = cam.get("port", 322)
            path = cam.get("path", "/stream0")
            raw = f"{scheme}://{host}:{port}{path}"
        user = cam.get("user") or self.default_user
        pwd = cam.get("password") or self.default_password
        return format_wyze_rtsp_url(raw, user, pwd)


# ── High-Performance Stream Worker ───────────────────────────────────────────
class WyzeStreamWorker:
    def __init__(self, cam_id: str, label: str, effective_url: str, max_width: int = 1280):
        self.cam_id = cam_id
        self.label = label
        self.url = effective_url
        self.max_width = max_width

        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        self.status = "standby"
        self.fps = 0.0
        self.width = 0
        self.height = 0
        self.frames_received = 0
        self.latest_time = time.time()
        self.last_accessed = time.time()
        self.subscribers = 0
        self.error_message = ""
        self.latest_jpeg = self._create_placeholder("Connecting to Wyze...", f"Target: {self._safe_display_url()}")

    def _safe_display_url(self) -> str:
        # Hide password in logs and UI overlay
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:••••••••@", self.url)

    def _create_placeholder(self, status_text: str, sub_text: str = "", width: int = 640, height: int = 360) -> bytes:
        try:
            import cv2
            import numpy as np

            img = np.zeros((height, width, 3), dtype=np.uint8)
            img[:] = (18, 14, 11)  # Deep sleek dark-slate background

            # Subtle border and header
            cv2.rectangle(img, (2, 2), (width - 3, height - 3), (55, 38, 22), 1)
            cv2.rectangle(img, (2, 2), (width - 3, 36), (36, 22, 14), -1)

            cv2.putText(
                img,
                f"WYZE CAM :: {self.label.upper()}",
                (14, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (230, 235, 240),
                1,
                cv2.LINE_AA,
            )
            ts = time.strftime("%H:%M:%S")
            cv2.putText(
                img,
                f"[{ts}]",
                (width - 95, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (56, 189, 248),
                1,
                cv2.LINE_AA,
            )

            # Main Status Message
            cv2.putText(
                img,
                status_text,
                (24, height // 2 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.68,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            if sub_text:
                cv2.putText(
                    img,
                    sub_text,
                    (24, height // 2 + 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    (160, 175, 190),
                    1,
                    cv2.LINE_AA,
                )

            # Footer Tip
            tip = "Wyze RTSP: Check IP, Port (322/554), & Credentials in Settings"
            cv2.putText(
                img,
                tip,
                (24, height - 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.36,
                (110, 125, 140),
                1,
                cv2.LINE_AA,
            )

            ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                return jpg.tobytes()
        except Exception as e:
            log.debug("Placeholder gen error: %s", e)

        # 1x1 fallback GIF/JPEG bytes
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
            b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19"
            b"\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9"
            b"=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00"
            b"\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03"
            b"\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        )

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True, name=f"wyze-{self.cam_id}")
            self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.5)

    def touch(self):
        self.last_accessed = time.time()
        self.start()

    def get_latest_frame(self) -> bytes:
        self.touch()
        with self.lock:
            return self.latest_jpeg

    def get_status(self) -> dict:
        with self.lock:
            return {
                "cam_id": self.cam_id,
                "label": self.label,
                "status": self.status,
                "fps": round(self.fps, 1),
                "width": self.width,
                "height": self.height,
                "frames": self.frames_received,
                "display_url": self._safe_display_url(),
                "error": self.error_message,
                "updated_sec_ago": round(time.time() - self.latest_time, 1) if self.latest_time > 0 else 0,
            }

    def _run_loop(self):
        # Configure FFmpeg low-latency RTSP options for OpenCV
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|stimeout;3000000|analyzeduration;1000000|probesize;1000000|fflags;nobuffer|flags;low_delay"
        )
        os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"

        try:
            import cv2
        except ImportError:
            with self.lock:
                self.status = "error"
                self.error_message = "OpenCV not installed in current Python environment"
                self.latest_jpeg = self._create_placeholder("OpenCV Not Found", "Install opencv-python")
            return

        cap = None
        consecutive_errors = 0
        fps_timer = time.time()
        fps_counter = 0

        while self.running:
            # Sleep if idle for > 120s with no browser clients
            if time.time() - self.last_accessed > 120 and self.subscribers <= 0:
                self.status = "standby"
                if cap:
                    cap.release()
                    cap = None
                time.sleep(1.0)
                continue

            try:
                if cap is None or not cap.isOpened():
                    self.status = "connecting"
                    self.error_message = ""
                    with self.lock:
                        self.latest_jpeg = self._create_placeholder("Connecting to Wyze RTSP...", f"Target: {self._safe_display_url()}")
                        self.latest_time = time.time()

                    log.info("[%s] Opening stream: %s", self.label, self._safe_display_url())
                    cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                    if not cap.isOpened():
                        consecutive_errors += 1
                        self.status = "offline"
                        self.error_message = f"Could not open RTSP stream at {self._safe_display_url()}"
                        with self.lock:
                            self.latest_jpeg = self._create_placeholder(
                                "Camera Offline or Standby",
                                f"Failed to connect to {self._safe_display_url()}",
                            )
                            self.latest_time = time.time()
                        time.sleep(3.0)
                        continue

                    log.info("[%s] Connected to stream successfully!", self.label)

                ret, frame = cap.read()
                if not ret or frame is None:
                    consecutive_errors += 1
                    if consecutive_errors > 4:
                        if cap:
                            cap.release()
                            cap = None
                        consecutive_errors = 0
                        self.status = "reconnecting"
                        with self.lock:
                            self.latest_jpeg = self._create_placeholder("Reconnecting to Wyze...", f"Stream dropped, retrying...")
                            self.latest_time = time.time()
                        time.sleep(1.5)
                    else:
                        time.sleep(0.06)
                    continue

                consecutive_errors = 0
                h, w = frame.shape[:2]
                if w > self.max_width:
                    frame = cv2.resize(frame, (self.max_width, int(h * self.max_width / w)))
                    h, w = frame.shape[:2]

                ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    jpg_bytes = jpg.tobytes()
                    now = time.time()
                    fps_counter += 1
                    if now - fps_timer >= 1.0:
                        self.fps = fps_counter / (now - fps_timer)
                        fps_counter = 0
                        fps_timer = now

                    with self.lock:
                        self.latest_jpeg = jpg_bytes
                        self.latest_time = now
                        self.status = "live"
                        self.width = w
                        self.height = h
                        self.frames_received += 1

                time.sleep(0.035)  # Max ~28-30 FPS per worker

            except Exception as e:
                log.warning("[%s] Stream worker exception: %s", self.label, e)
                if cap:
                    cap.release()
                    cap = None
                self.status = "error"
                self.error_message = str(e)
                with self.lock:
                    self.latest_jpeg = self._create_placeholder("Stream Error", str(e)[:60])
                    self.latest_time = time.time()
                time.sleep(3.0)

        if cap:
            cap.release()


# ── Stream Hub for All Cameras ────────────────────────────────────────────────
class WyzeStreamHub:
    def __init__(self, config_manager: WyzeConfigManager):
        self.config_manager = config_manager
        self.workers: Dict[str, WyzeStreamWorker] = {}
        self.lock = threading.Lock()

    def sync_workers(self):
        """Starts workers for newly added cameras, updates modified, stops deleted."""
        with self.lock:
            active_ids = set()
            for cam in self.config_manager.cameras:
                if not cam.get("enabled", True):
                    continue
                cam_id = cam["id"]
                active_ids.add(cam_id)
                eff_url = self.config_manager.build_effective_url(cam)

                if cam_id in self.workers:
                    worker = self.workers[cam_id]
                    if worker.url != eff_url or worker.label != cam["label"]:
                        log.info("Updating worker for %s", cam["label"])
                        worker.stop()
                        worker = WyzeStreamWorker(cam_id, cam["label"], eff_url)
                        worker.start()
                        self.workers[cam_id] = worker
                else:
                    log.info("Starting worker for %s (%s)", cam["label"], cam_id)
                    worker = WyzeStreamWorker(cam_id, cam["label"], eff_url)
                    worker.start()
                    self.workers[cam_id] = worker

            # Remove deactivated
            for cid in list(self.workers.keys()):
                if cid not in active_ids:
                    log.info("Stopping worker %s", cid)
                    self.workers[cid].stop()
                    del self.workers[cid]

    def get_worker(self, cam_id: str) -> Optional[WyzeStreamWorker]:
        with self.lock:
            if cam_id not in self.workers:
                cam = self.config_manager.get_camera(cam_id)
                if cam and cam.get("enabled", True):
                    eff_url = self.config_manager.build_effective_url(cam)
                    worker = WyzeStreamWorker(cam_id, cam["label"], eff_url)
                    worker.start()
                    self.workers[cam_id] = worker
                    return worker
                return None
            return self.workers.get(cam_id)

    def restart_worker(self, cam_id: str):
        with self.lock:
            worker = self.workers.pop(cam_id, None)
            if worker:
                worker.stop()
        return self.get_worker(cam_id)

    def generate_mjpeg(self, cam_id: str):
        worker = self.get_worker(cam_id)
        if not worker:
            return
        worker.subscribers += 1
        try:
            while True:
                frame_bytes = worker.get_latest_frame()
                header = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame_bytes)).encode() + b"\r\n\r\n"
                )
                yield header + frame_bytes + b"\r\n"
                time.sleep(0.05)  # 20 FPS delivery
        finally:
            worker.subscribers = max(0, worker.subscribers - 1)

    def get_all_statuses(self) -> List[dict]:
        with self.lock:
            results = []
            for cam in self.config_manager.cameras:
                cid = cam["id"]
                worker = self.workers.get(cid)
                if worker:
                    st = worker.get_status()
                else:
                    st = {
                        "cam_id": cid,
                        "label": cam["label"],
                        "status": "disabled" if not cam.get("enabled", True) else "standby",
                        "fps": 0,
                        "width": 0,
                        "height": 0,
                        "frames": 0,
                        "display_url": cam.get("host", ""),
                        "error": "",
                        "updated_sec_ago": 0,
                    }
                st["host"] = cam.get("host", "")
                st["port"] = cam.get("port", 322)
                st["scheme"] = cam.get("scheme", "rtsps")
                st["enabled"] = cam.get("enabled", True)
                results.append(st)
            return results


# ── Web App & Frontend Templates ─────────────────────────────────────────────
app = Flask(__name__)
config_manager = WyzeConfigManager()
stream_hub = WyzeStreamHub(config_manager)

INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Wyze Multi-Cam Viewer • Birdseye NOC</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2338bdf8'><path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14.5v-9l6 4.5-6 4.5z'/></svg>">
  <style>
    :root {
      --bg-dark: #07090e;
      --bg-card: #0f141f;
      --bg-card-hover: #161d2e;
      --border: #1e293b;
      --accent-cyan: #38bdf8;
      --accent-blue: #0284c7;
      --accent-green: #10b981;
      --accent-yellow: #f59e0b;
      --accent-red: #ef4444;
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-dark);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      background: rgba(15, 20, 31, 0.95);
      border-bottom: 1px solid var(--border);
      padding: 10px 18px;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      position: sticky;
      top: 0;
      z-index: 100;
      backdrop-filter: blur(10px);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-icon {
      width: 32px;
      height: 32px;
      background: linear-gradient(135deg, #0284c7, #38bdf8);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
      box-shadow: 0 0 16px rgba(56, 189, 248, 0.35);
    }
    .brand h1 {
      font-size: 16px;
      font-weight: 700;
      letter-spacing: 0.5px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .badge-noc {
      font-size: 10px;
      font-weight: 600;
      background: rgba(56, 189, 248, 0.15);
      color: var(--accent-cyan);
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
    }
    .controls-bar {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .btn {
      background: #1e293b;
      color: var(--text-main);
      border: 1px solid var(--border);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
      text-decoration: none;
    }
    .btn:hover { background: #334155; border-color: var(--accent-cyan); }
    .btn-primary {
      background: linear-gradient(135deg, #0284c7, #0369a1);
      border-color: #38bdf8;
      color: #fff;
    }
    .btn-primary:hover { background: #0284c7; box-shadow: 0 0 12px rgba(56, 189, 248, 0.4); }
    .btn-subwin {
      background: rgba(16, 185, 129, 0.15);
      border-color: rgba(16, 185, 129, 0.4);
      color: #34d399;
    }
    .btn-subwin:hover { background: rgba(16, 185, 129, 0.3); border-color: #10b981; }
    .select-layout {
      background: #1e293b;
      color: var(--text-main);
      border: 1px solid var(--border);
      padding: 6px 10px;
      border-radius: 6px;
      font-size: 12px;
      cursor: pointer;
      outline: none;
    }

    /* Grid Layouts */
    main {
      padding: 16px;
      flex: 1;
    }
    .cam-grid {
      display: grid;
      gap: 16px;
      width: 100%;
      transition: all 0.2s ease;
    }
    .grid-auto { grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); }
    .grid-1    { grid-template-columns: 1fr; }
    .grid-2    { grid-template-columns: repeat(2, 1fr); }
    .grid-3    { grid-template-columns: repeat(3, 1fr); }
    .grid-4    { grid-template-columns: repeat(2, 1fr); }
    .grid-6    { grid-template-columns: repeat(3, 1fr); }
    .grid-9    { grid-template-columns: repeat(3, 1fr); }

    .cam-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      box-shadow: 0 4px 16px rgba(0,0,0,0.4);
      transition: transform 0.15s, border-color 0.15s;
    }
    .cam-card:hover {
      border-color: rgba(56, 189, 248, 0.4);
    }
    .cam-header {
      padding: 8px 12px;
      background: #131b2a;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
    }
    .cam-title-box {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .status-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--accent-red);
      display: inline-block;
    }
    .status-dot.live { background: var(--accent-green); box-shadow: 0 0 8px var(--accent-green); }
    .status-dot.connecting { background: var(--accent-yellow); animation: pulse 1s infinite alternate; }
    .status-dot.reconnecting { background: var(--accent-yellow); }
    @keyframes pulse { from { opacity: 0.4; } to { opacity: 1; } }

    .cam-name { font-size: 13px; font-weight: 600; }
    .cam-ip { font-size: 11px; color: var(--text-muted); font-family: monospace; }
    .scheme-badge {
      font-size: 10px;
      background: rgba(255,255,255,0.08);
      padding: 1px 5px;
      border-radius: 3px;
      text-transform: uppercase;
      font-family: monospace;
    }

    .video-viewport {
      position: relative;
      background: #000;
      aspect-ratio: 16 / 9;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }
    .video-viewport img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }
    .overlay-stats {
      position: absolute;
      bottom: 8px;
      left: 8px;
      background: rgba(0,0,0,0.65);
      border: 1px solid rgba(255,255,255,0.1);
      padding: 2px 7px;
      border-radius: 4px;
      font-size: 10px;
      font-family: monospace;
      color: #38bdf8;
      pointer-events: none;
    }
    .overlay-live {
      position: absolute;
      top: 8px;
      right: 8px;
      background: rgba(16, 185, 129, 0.85);
      color: #fff;
      font-size: 9px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 3px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .cam-footer {
      padding: 8px 10px;
      background: #0e131d;
      border-top: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 6px;
    }
    .cam-actions {
      display: flex;
      gap: 6px;
    }
    .btn-icon {
      padding: 4px 8px;
      font-size: 11px;
    }

    /* Modal */
    .modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.75);
      backdrop-filter: blur(4px);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 16px;
    }
    .modal-content {
      background: #0f141f;
      border: 1px solid var(--border);
      border-radius: 10px;
      width: 100%;
      max-width: 500px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.6);
      overflow: hidden;
    }
    .modal-header {
      padding: 14px 18px;
      background: #131b2a;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .modal-body {
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      max-height: 80vh;
      overflow-y: auto;
    }
    .form-group {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .form-row {
      display: flex;
      gap: 10px;
    }
    .form-row .form-group { flex: 1; }
    label { font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; }
    input, select {
      background: #1a2233;
      border: 1px solid var(--border);
      color: var(--text-main);
      padding: 8px 10px;
      border-radius: 6px;
      font-size: 13px;
      outline: none;
    }
    input:focus, select:focus { border-color: var(--accent-cyan); }
    .modal-footer {
      padding: 12px 18px;
      background: #0e131d;
      border-top: 1px solid var(--border);
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }

    /* Fullscreen Wall */
    body.wall-mode header { display: none; }
    body.wall-mode main { padding: 4px; }
    body.wall-mode .cam-header, body.wall-mode .cam-footer { display: none; }
    body.wall-mode .cam-card { border-radius: 0; border: 1px solid #111; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-icon">📹</div>
      <div>
        <h1>Wyze Multi-Cam Viewer <span class="badge-noc">Birdseye NOC</span></h1>
        <div style="font-size: 10px; color: var(--text-muted);">Direct Wyze RTSP / RTSPS Stream Hub • Port {{ server_port }}</div>
      </div>
    </div>

    <div class="controls-bar">
      <button class="btn btn-subwin" onclick="openAllInSubwindows()" title="Open separate browser windows for each camera for multi-monitor tiling">
        ⧉ Open Subwindows (All Cams)
      </button>

      <select class="select-layout" id="layoutSelect" onchange="changeLayout(this.value)">
        <option value="grid-auto">Layout: Auto Responsive</option>
        <option value="grid-1">Layout: 1 Focused View</option>
        <option value="grid-2">Layout: 2 Columns</option>
        <option value="grid-4">Layout: 4 Grid (2×2)</option>
        <option value="grid-6">Layout: 6 Grid (3×2)</option>
        <option value="grid-9">Layout: 9 Grid (3×3)</option>
      </select>

      <button class="btn btn-primary" onclick="openAddCamModal()">
        ＋ Add Camera
      </button>

      <button class="btn" onclick="openSettingsModal()">
        ⚙ Credentials
      </button>

      <button class="btn" onclick="toggleWallMode()" id="wallBtn" title="Toggle Fullscreen CCTV Wall Mode">
        ⛶ Wall Mode
      </button>
    </div>
  </header>

  <main>
    <div class="cam-grid grid-auto" id="camGrid">
      <!-- Dynamically Populated by JavaScript -->
    </div>
  </main>

  <!-- Add/Edit Camera Modal -->
  <div class="modal-overlay" id="camModal">
    <div class="modal-content">
      <div class="modal-header">
        <h3 id="modalTitle">Add Wyze Camera</h3>
        <button class="btn" onclick="closeModal('camModal')" style="padding: 2px 6px;">✕</button>
      </div>
      <div class="modal-body">
        <input type="hidden" id="editCamId" value="">
        <div class="form-group">
          <label>Camera Friendly Name / Location</label>
          <input type="text" id="camLabel" placeholder="e.g. Driveway, Pier Cam, South Horizon">
        </div>
        <div class="form-row">
          <div class="form-group" style="flex: 2;">
            <label>Camera IP Address</label>
            <input type="text" id="camHost" placeholder="e.g. 192.168.1.174" style="font-family: monospace;">
          </div>
          <div class="form-group" style="flex: 1;">
            <label>RTSP Port</label>
            <input type="number" id="camPort" value="322" style="font-family: monospace;">
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Protocol / Scheme</label>
            <select id="camScheme">
              <option value="rtsps" selected>rtsps:// (Secure TLS - Port 322 default)</option>
              <option value="rtsp">rtsp:// (Standard RTSP - Port 554 default)</option>
            </select>
          </div>
          <div class="form-group">
            <label>Stream Path</label>
            <input type="text" id="camPath" value="/stream0" style="font-family: monospace;">
          </div>
        </div>
        <div class="form-group">
          <label>Direct Stream URL Override (Optional)</label>
          <input type="text" id="camRtspUrl" placeholder="Leave blank to auto-generate from host/port" style="font-family: monospace; font-size: 11px;">
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Specific User (Leave blank for default)</label>
            <input type="text" id="camUser" placeholder="Inherit global user">
          </div>
          <div class="form-group">
            <label>Specific Password (Leave blank for default)</label>
            <input type="password" id="camPass" placeholder="Inherit global pass">
          </div>
        </div>
        <div style="margin-top: 4px;">
          <button type="button" class="btn btn-icon" onclick="testConnectionFromModal()" id="btnTestConn">
            ⚡ Test Reachability
          </button>
          <span id="testConnResult" style="font-size: 11px; margin-left: 8px;"></span>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn" onclick="closeModal('camModal')">Cancel</button>
        <button class="btn btn-primary" onclick="saveCamFromModal()">Save Camera</button>
      </div>
    </div>
  </div>

  <!-- Global Settings Modal -->
  <div class="modal-overlay" id="settingsModal">
    <div class="modal-content">
      <div class="modal-header">
        <h3>Wyze Credentials & Settings</h3>
        <button class="btn" onclick="closeModal('settingsModal')" style="padding: 2px 6px;">✕</button>
      </div>
      <div class="modal-body">
        <div style="background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 6px; padding: 10px 12px; font-size: 11px; line-height: 1.5;">
          <b>Wyze RTSP Authentication:</b> Newer Wyze cams require authentication on port 322 (RTSPS) or 554 (RTSP). Special characters like <code>@</code> in passwords are auto percent-encoded to prevent stream truncation.
        </div>
        <div class="form-group">
          <label>Default Wyze RTSP Username</label>
          <input type="text" id="globalUser" value="{{ default_user }}">
        </div>
        <div class="form-group">
          <label>Default Wyze RTSP Password</label>
          <input type="password" id="globalPass" value="{{ default_password }}">
        </div>
        <div class="form-group">
          <label>Server Web Port (Requires restart if changed)</label>
          <input type="number" id="globalPort" value="{{ server_port }}">
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn" onclick="closeModal('settingsModal')">Cancel</button>
        <button class="btn btn-primary" onclick="saveGlobalSettings()">Save to YAML</button>
      </div>
    </div>
  </div>

  <script>
    let activeCameras = [];
    let telemetryTimer = null;

    async function loadCameras() {
      try {
        const res = await fetch('/api/cams');
        activeCameras = await res.json();
        renderGrid();
      } catch (e) {
        console.error("Failed to load cameras:", e);
      }
    }

    function renderGrid() {
      const grid = document.getElementById('camGrid');
      if (!activeCameras.length) {
        grid.innerHTML = `
          <div style="grid-column: 1 / -1; text-align: center; padding: 60px 20px; color: var(--text-muted);">
            <div style="font-size: 40px; margin-bottom: 12px;">📹</div>
            <h3 style="color: var(--text-main); margin-bottom: 8px;">No Wyze Cameras Configured</h3>
            <p style="font-size: 13px; margin-bottom: 16px;">Add your first Wyze RTSP camera to view the live birdseye feed.</p>
            <button class="btn btn-primary" onclick="openAddCamModal()">＋ Add Camera Now</button>
          </div>
        `;
        return;
      }

      grid.innerHTML = activeCameras.map(cam => {
        const isLive = cam.status === 'live';
        const statusClass = isLive ? 'live' : (cam.status === 'connecting' ? 'connecting' : (cam.status === 'reconnecting' ? 'reconnecting' : ''));
        const statusText = isLive ? 'LIVE' : cam.status.toUpperCase();
        const fpsText = isLive && cam.fps ? `${cam.fps} FPS` : (cam.status || 'Offline');
        const resText = isLive && cam.width ? `${cam.width}×${cam.height}` : '';

        return `
          <div class="cam-card" id="card-${cam.cam_id}">
            <div class="cam-header">
              <div class="cam-title-box">
                <span class="status-dot ${statusClass}" id="dot-${cam.cam_id}"></span>
                <span class="cam-name">${escapeHtml(cam.label)}</span>
                <span class="scheme-badge">${cam.scheme}</span>
              </div>
              <div class="cam-ip">${cam.host}:${cam.port}</div>
            </div>

            <div class="video-viewport" ondblclick="openSubwindow('${cam.cam_id}')">
              <img id="stream-${cam.cam_id}" src="/api/stream/${cam.cam_id}" alt="${escapeHtml(cam.label)}" loading="lazy">
              ${isLive ? `<span class="overlay-live">● LIVE</span>` : ''}
              <div class="overlay-stats" id="stats-${cam.cam_id}">
                ${fpsText}${resText ? ' • ' + resText : ''}
              </div>
            </div>

            <div class="cam-footer">
              <div style="font-size: 11px; color: var(--text-muted);" id="sub-${cam.cam_id}">
                Wyze Cam • ${cam.cam_id}
              </div>
              <div class="cam-actions">
                <button class="btn btn-icon btn-subwin" onclick="openSubwindow('${cam.cam_id}')" title="Pop out into separate browser window">
                  ↗ Subwindow
                </button>
                <button class="btn btn-icon" onclick="takeSnapshot('${cam.cam_id}', '${escapeHtml(cam.label)}')" title="Download full JPEG snapshot">
                  📷 Snap
                </button>
                <button class="btn btn-icon" onclick="restartStream('${cam.cam_id}')" title="Restart worker connection">
                  🔄
                </button>
                <button class="btn btn-icon" onclick="openEditCamModal('${cam.cam_id}')" title="Edit Camera Settings">
                  ⚙
                </button>
                <button class="btn btn-icon" style="color:var(--accent-red);" onclick="deleteCamera('${cam.cam_id}')" title="Remove Camera">
                  ✕
                </button>
              </div>
            </div>
          </div>
        `;
      }).join('');
    }

    async function pollTelemetry() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        data.cameras.forEach(c => {
          const dot = document.getElementById(`dot-${c.cam_id}`);
          const stats = document.getElementById(`stats-${c.cam_id}`);
          if (dot) {
            dot.className = `status-dot ${c.status === 'live' ? 'live' : (c.status === 'connecting' ? 'connecting' : '')}`;
          }
          if (stats) {
            const isLive = c.status === 'live';
            const fpsText = isLive && c.fps ? `${c.fps} FPS` : (c.status || 'Offline');
            const resText = isLive && c.width ? `${c.width}×${c.height}` : '';
            stats.textContent = `${fpsText}${resText ? ' • ' + resText : ''}`;
          }
        });
      } catch (e) {}
    }

    function openSubwindow(camId) {
      const width = 854;
      const height = 510;
      const left = Math.max(50, (screen.width - width) / 2);
      const top = Math.max(50, (screen.height - height) / 2);
      const url = `/cam/${camId}?popup=1`;
      window.open(url, `wyze_subwin_${camId}`, `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes`);
    }

    function openAllInSubwindows() {
      if (!activeCameras.length) {
        alert("No cameras configured to open in subwindows.");
        return;
      }
      const width = 720;
      const height = 440;
      let x = 60;
      let y = 60;
      activeCameras.forEach((cam, idx) => {
        setTimeout(() => {
          const url = `/cam/${cam.cam_id}?popup=1`;
          window.open(url, `wyze_subwin_${cam.cam_id}`, `width=${width},height=${height},left=${x},top=${y},menubar=no,toolbar=no,location=no,status=no,resizable=yes`);
          x += 35;
          y += 35;
          if (x + width > screen.width) x = 60;
          if (y + height > screen.height) y = 60;
        }, idx * 180);
      });
    }

    function changeLayout(layoutClass) {
      const grid = document.getElementById('camGrid');
      grid.className = `cam-grid ${layoutClass}`;
    }

    function toggleWallMode() {
      document.body.classList.toggle('wall-mode');
      if (document.body.classList.contains('wall-mode')) {
        document.documentElement.requestFullscreen?.().catch(() => {});
      } else {
        document.exitFullscreen?.().catch(() => {});
      }
    }

    function takeSnapshot(camId, label) {
      const a = document.createElement('a');
      a.href = `/api/snapshot/${camId}?ts=${Date.now()}`;
      a.download = `${label.replace(/[^a-zA-Z0-9]/g, '_')}_${Date.now()}.jpg`;
      a.click();
    }

    async function restartStream(camId) {
      await fetch(`/api/cams/${camId}/restart`, { method: 'POST' });
      const img = document.getElementById(`stream-${camId}`);
      if (img) img.src = `/api/stream/${camId}?t=${Date.now()}`;
    }

    function openAddCamModal() {
      document.getElementById('modalTitle').textContent = 'Add Wyze Camera';
      document.getElementById('editCamId').value = '';
      document.getElementById('camLabel').value = '';
      document.getElementById('camHost').value = '';
      document.getElementById('camPort').value = '322';
      document.getElementById('camScheme').value = 'rtsps';
      document.getElementById('camPath').value = '/stream0';
      document.getElementById('camRtspUrl').value = '';
      document.getElementById('camUser').value = '';
      document.getElementById('camPass').value = '';
      document.getElementById('testConnResult').textContent = '';
      document.getElementById('camModal').style.display = 'flex';
    }

    function openEditCamModal(camId) {
      const cam = activeCameras.find(c => c.cam_id === camId);
      if (!cam) return;
      document.getElementById('modalTitle').textContent = 'Edit Wyze Camera';
      document.getElementById('editCamId').value = camId;
      document.getElementById('camLabel').value = cam.label;
      document.getElementById('camHost').value = cam.host;
      document.getElementById('camPort').value = cam.port;
      document.getElementById('camScheme').value = cam.scheme;
      document.getElementById('camPath').value = cam.path || '/stream0';
      document.getElementById('camRtspUrl').value = cam.rtsp_url || '';
      document.getElementById('camUser').value = cam.user || '';
      document.getElementById('camPass').value = cam.password || '';
      document.getElementById('testConnResult').textContent = '';
      document.getElementById('camModal').style.display = 'flex';
    }

    async function saveCamFromModal() {
      const id = document.getElementById('editCamId').value;
      const label = document.getElementById('camLabel').value.trim() || 'Wyze Cam';
      const host = document.getElementById('camHost').value.trim();
      const port = parseInt(document.getElementById('camPort').value) || 322;
      const scheme = document.getElementById('camScheme').value;
      const path = document.getElementById('camPath').value.trim() || '/stream0';
      const rtsp_url = document.getElementById('camRtspUrl').value.trim();
      const user = document.getElementById('camUser').value.trim();
      const password = document.getElementById('camPass').value.trim();

      if (!host) {
        alert("Please enter the camera IP address.");
        return;
      }

      const payload = { id, label, host, port, scheme, path, rtsp_url, user, password, enabled: true };
      const res = await fetch('/api/cams', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        closeModal('camModal');
        await loadCameras();
      } else {
        alert("Failed to save camera");
      }
    }

    async function deleteCamera(camId) {
      if (!confirm("Are you sure you want to remove this camera?")) return;
      await fetch(`/api/cams/${camId}`, { method: 'DELETE' });
      await loadCameras();
    }

    async function testConnectionFromModal() {
      const host = document.getElementById('camHost').value.trim();
      const port = parseInt(document.getElementById('camPort').value) || 322;
      const resultSpan = document.getElementById('testConnResult');
      if (!host) {
        resultSpan.innerHTML = '<span style="color:var(--accent-red)">Enter IP first</span>';
        return;
      }
      resultSpan.innerHTML = '<span style="color:var(--accent-cyan)">Testing reachability...</span>';
      try {
        const res = await fetch('/api/test_connection', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ host, port })
        });
        const d = await res.json();
        if (d.reachable) {
          resultSpan.innerHTML = `<span style="color:var(--accent-green)">✓ Port ${port} is OPEN and reachable</span>`;
        } else {
          resultSpan.innerHTML = `<span style="color:var(--accent-red)">✕ Port ${port} closed or timed out (${d.message})</span>`;
        }
      } catch (e) {
        resultSpan.innerHTML = `<span style="color:var(--accent-red)">Error testing: ${e}</span>`;
      }
    }

    function openSettingsModal() {
      document.getElementById('settingsModal').style.display = 'flex';
    }

    async function saveGlobalSettings() {
      const default_user = document.getElementById('globalUser').value.trim();
      const default_password = document.getElementById('globalPass').value.trim();
      const server_port = parseInt(document.getElementById('globalPort').value) || 5005;

      await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ default_user, default_password, server_port })
      });
      closeModal('settingsModal');
      alert("Credentials saved to wyze_config.yaml. Reconnecting streams...");
      await loadCameras();
    }

    function closeModal(id) {
      document.getElementById(id).style.display = 'none';
    }

    function escapeHtml(str) {
      return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // Startup
    loadCameras();
    telemetryTimer = setInterval(pollTelemetry, 2500);
  </script>
</body>
</html>
"""

SUBWINDOW_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ cam.label }} • Wyze Subwindow</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      background: #000;
      color: #fff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      width: 100vw;
      height: 100vh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .subwin-bar {
      background: rgba(15, 20, 31, 0.92);
      border-bottom: 1px solid #1e293b;
      padding: 6px 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      backdrop-filter: blur(8px);
      z-index: 10;
    }
    .title-box { display: flex; align-items: center; gap: 8px; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #10b981; box-shadow: 0 0 8px #10b981; }
    .stats-badge {
      font-size: 11px;
      font-family: monospace;
      color: #38bdf8;
      background: rgba(56, 189, 248, 0.12);
      padding: 2px 6px;
      border-radius: 4px;
    }
    .actions { display: flex; gap: 6px; }
    .btn {
      background: #1e293b;
      color: #f1f5f9;
      border: 1px solid #334155;
      padding: 4px 8px;
      border-radius: 4px;
      font-size: 11px;
      cursor: pointer;
      text-decoration: none;
    }
    .btn:hover { background: #334155; border-color: #38bdf8; }
    .stage {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #000;
      position: relative;
    }
    .stage img {
      max-width: 100%;
      max-height: 100%;
      width: 100%;
      height: 100%;
      object-fit: contain;
    }
  </style>
</head>
<body>
  <div class="subwin-bar">
    <div class="title-box">
      <span class="dot"></span>
      <b>{{ cam.label }}</b>
      <span style="color: #94a3b8; font-family: monospace; font-size: 11px;">{{ cam.host }}:{{ cam.port }}</span>
      <span class="stats-badge" id="liveStats">CONNECTING</span>
    </div>
    <div class="actions">
      <button class="btn" onclick="takeSnapshot()">📷 Snapshot</button>
      <button class="btn" onclick="toggleFullscreen()">⛶ Fullscreen</button>
      <a class="btn" href="/" target="_blank">🏠 Birdseye NOC</a>
    </div>
  </div>

  <div class="stage" ondblclick="toggleFullscreen()">
    <img id="streamImg" src="/api/stream/{{ cam.id }}" alt="{{ cam.label }}">
  </div>

  <script>
    async function updateStats() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        const me = data.cameras.find(c => c.cam_id === "{{ cam.id }}");
        if (me) {
          const el = document.getElementById('liveStats');
          if (me.status === 'live') {
            el.textContent = `${me.fps} FPS • ${me.width}×${me.height}`;
          } else {
            el.textContent = (me.status || 'OFFLINE').toUpperCase();
          }
        }
      } catch (e) {}
    }
    setInterval(updateStats, 2000);
    updateStats();

    function takeSnapshot() {
      const a = document.createElement('a');
      a.href = `/api/snapshot/{{ cam.id }}?ts=${Date.now()}`;
      a.download = `{{ cam.label | replace(' ', '_') }}_${Date.now()}.jpg`;
      a.click();
    }

    function toggleFullscreen() {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen?.().catch(() => {});
      } else {
        document.exitFullscreen?.().catch(() => {});
      }
    }
  </script>
</body>
</html>
"""

# ── Flask API Routes ─────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(
        INDEX_HTML,
        server_port=config_manager.server_port,
        default_user=config_manager.default_user,
        default_password=config_manager.default_password,
    )


@app.route("/cam/<cam_id>")
def cam_subwindow(cam_id: str):
    cam = config_manager.get_camera(cam_id)
    if not cam:
        return f"Camera {cam_id} not found", 404
    return render_template_string(SUBWINDOW_HTML, cam=cam)


@app.route("/api/cams", methods=["GET", "POST"])
def api_cams():
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        item = config_manager.upsert_camera(data)
        stream_hub.sync_workers()
        return jsonify({"status": "ok", "camera": item})

    statuses = stream_hub.get_all_statuses()
    return jsonify(statuses)


@app.route("/api/cams/<cam_id>", methods=["GET", "PUT", "DELETE"])
def api_cam_item(cam_id: str):
    if request.method == "DELETE":
        ok = config_manager.delete_camera(cam_id)
        stream_hub.sync_workers()
        return jsonify({"status": "ok" if ok else "error"})

    if request.method == "PUT":
        data = request.get_json(force=True, silent=True) or {}
        data["id"] = cam_id
        item = config_manager.upsert_camera(data)
        stream_hub.sync_workers()
        return jsonify({"status": "ok", "camera": item})

    cam = config_manager.get_camera(cam_id)
    if not cam:
        return jsonify({"status": "error", "message": "Not found"}), 404
    return jsonify(cam)


@app.route("/api/cams/<cam_id>/restart", methods=["POST"])
def api_cam_restart(cam_id: str):
    stream_hub.restart_worker(cam_id)
    return jsonify({"status": "ok", "message": f"Worker for {cam_id} restarted"})


@app.route("/api/stream/<cam_id>")
def api_stream(cam_id: str):
    res = Response(
        stream_hub.generate_mjpeg(cam_id),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
    res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    res.headers["Pragma"] = "no-cache"
    res.headers["Expires"] = "0"
    res.headers["Access-Control-Allow-Origin"] = "*"
    return res


@app.route("/api/snapshot/<cam_id>")
def api_snapshot(cam_id: str):
    worker = stream_hub.get_worker(cam_id)
    if not worker:
        return "Camera not found", 404
    frame_bytes = worker.get_latest_frame()
    res = Response(frame_bytes, mimetype="image/jpeg")
    res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    res.headers["Access-Control-Allow-Origin"] = "*"
    return res


@app.route("/api/status")
def api_status():
    return jsonify({
        "status": "ok",
        "cameras": stream_hub.get_all_statuses(),
        "total_cams": len(config_manager.cameras),
        "server_port": config_manager.server_port,
    })


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        if "default_user" in data:
            config_manager.default_user = str(data["default_user"]).strip()
        if "default_password" in data:
            config_manager.default_password = str(data["default_password"]).strip()
        if "server_port" in data:
            try:
                config_manager.server_port = int(data["server_port"])
            except Exception:
                pass
        config_manager.save()
        stream_hub.sync_workers()
        return jsonify({"status": "ok", "message": "Settings saved"})

    return jsonify({
        "default_user": config_manager.default_user,
        "default_password": config_manager.default_password,
        "server_port": config_manager.server_port,
    })


@app.route("/api/test_connection", methods=["POST"])
def api_test_connection():
    data = request.get_json(force=True, silent=True) or {}
    host = str(data.get("host", "")).strip()
    port = int(data.get("port", 322))
    if not host:
        return jsonify({"reachable": False, "message": "Host is empty"})

    try:
        with socket.create_connection((host, port), timeout=2.5):
            return jsonify({"reachable": True, "message": "Port is OPEN and reachable"})
    except Exception as e:
        return jsonify({"reachable": False, "message": str(e)})


# ── Server Startup ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Standalone Wyze Multi-Cam Viewer for Windows")
    parser.add_argument("--port", type=int, default=None, help=f"Server port (default: {config_manager.server_port})")
    parser.add_argument("--host", type=str, default=None, help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open default web browser")
    args = parser.parse_args()

    port = args.port or config_manager.server_port
    host = args.host or config_manager.server_host
    open_browser = not args.no_browser and config_manager.auto_open_browser

    stream_hub.sync_workers()

    local_url = f"http://localhost:{port}"
    lan_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    log.info("=" * 64)
    log.info("WYZE MULTI-CAM VIEWER • BIRDSEYE NOC")
    log.info("Local Web UI:       %s", local_url)
    log.info("LAN Web UI:         http://%s:%d", lan_ip, port)
    log.info("Config File:        %s", config_manager.config_file)
    log.info("Configured Cameras: %d", len(config_manager.cameras))
    for c in config_manager.cameras:
        log.info("  • [%s] %s (%s:%d%s)", c['id'], c['label'], c['host'], c['port'], c.get('path', '/stream0'))
    log.info("=" * 64)

    if open_browser:
        def _open():
            time.sleep(1.2)
            log.info("Opening browser: %s", local_url)
            webbrowser.open(local_url)
        threading.Thread(target=_open, daemon=True).start()

    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
