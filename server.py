#!/usr/bin/env python3
import array
import concurrent.futures
import contextlib
import hashlib
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import posixpath
import re
import secrets
import shlex
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import ProxyHandler, build_opener


WEB_ROOT = Path(os.environ.get("WEB_ROOT", "/usr/share/nginx/html"))
HLS_DIR = WEB_ROOT / "hls"
STATUS_FILE = WEB_ROOT / "status.json"
CONFIG_DIR = Path(os.environ.get("WEB_CONFIG_DIR", "/home/echowarp/.config/echowarp-web"))
CONFIG_FILE = CONFIG_DIR / "config.json"
SERVERS_FILE = CONFIG_DIR / "servers.json"
SESSIONS_FILE = CONFIG_DIR / "sessions.sqlite3"
RUNTIME_LOG = CONFIG_DIR / "runtime.log"
RECORDINGS_DIR = CONFIG_DIR / "recordings"
ECHOWARP_BIN = os.environ.get("ECHOWARP_BIN", "/opt/echowarp/EchoWarp")
ECHOWARP_CLIENT_LOG = Path("/home/echowarp/.config/echowarp/logs/client.log")
AUTH_COOKIE = "echowarp_session"
DEFAULT_AUTH_USER = "admin@example.com"
DEFAULT_AUTH_PASSWORD = "CHANGE_ME_BEFORE_START"


def pcm_level(raw):
    if len(raw) < 2:
        return {"rms": 0.0, "peak": 0.0, "dbfs": -120.0}
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return {"rms": 0.0, "peak": 0.0, "dbfs": -120.0}
    peak_sample = max(abs(sample) for sample in samples)
    square_total = sum(sample * sample for sample in samples)
    rms = min(1.0, ((square_total / len(samples)) ** 0.5) / 32768.0)
    peak = min(1.0, peak_sample / 32768.0)
    dbfs = 20 * math.log10(rms) if rms > 0 else -120.0
    return {"rms": rms, "peak": peak, "dbfs": dbfs}


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def auth_enabled():
    return bool(os.environ.get("WEB_AUTH_PASSWORD", DEFAULT_AUTH_PASSWORD))


def auth_user():
    return os.environ.get("WEB_AUTH_USER", DEFAULT_AUTH_USER)


def session_ttl():
    return max(300, env_int("WEB_SESSION_TTL_SECONDS", 12 * 60 * 60))


def parse_cookie_header(header):
    cookies = {}
    for item in (header or "").split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        cookies[key.strip()] = value.strip()
    return cookies


def safe_next_url(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def default_config():
    return {
        "enabled": True,
        "role": os.environ.get("ECHOWARP_ROLE", "client"),
        "address": os.environ.get("ECHOWARP_SERVER", ""),
        "port": env_int("ECHOWARP_PORT", 4415),
        "password": os.environ.get("ECHOWARP_PASSWORD", ""),
        "device_mode": "name",
        "device_id": "",
        "device_name": os.environ.get("ECHOWARP_DEVICE_NAME", "EchoWarpWeb"),
        "capture_devices": [],
        "playback_devices": [],
        "nickname": os.environ.get("ECHOWARP_NICKNAME", ""),
        "log_level": os.environ.get("ECHOWARP_LOG_LEVEL", "info"),
        "discover": env_bool("ECHOWARP_DISCOVER", True),
        "discover_timeout": os.environ.get("ECHOWARP_DISCOVER_TIMEOUT", "3s"),
        "tls_insecure": env_bool("ECHOWARP_TLS_INSECURE", False),
        "virtual_mic": env_bool("ECHOWARP_VIRTUAL_MIC", False),
        "loopback": env_bool("ECHOWARP_LOOPBACK", False),
        "aec": env_bool("ECHOWARP_AEC", False),
        "stun_servers": [
            item.strip()
            for item in os.environ.get("ECHOWARP_STUN_SERVERS", "").split(",")
            if item.strip()
        ],
        "max_reconnect": env_int("ECHOWARP_MAX_RECONNECT", 5),
        "auto_restart": env_bool("ECHOWARP_AUTO_RESTART", True),
        "restart_delay": env_int("ECHOWARP_RESTART_DELAY", 3),
        "extra_args": os.environ.get("ECHOWARP_EXTRA_ARGS", ""),
        "server_mode": os.environ.get("ECHOWARP_SERVER_MODE", "normal"),
        "server_name": os.environ.get("ECHOWARP_SERVER_NAME", "EchoWarpWeb"),
        "server_discovery": env_bool("ECHOWARP_SERVER_DISCOVERY", True),
        "max_clients": env_int("ECHOWARP_MAX_CLIENTS", 1),
        "sample_rate": env_int("PULSE_RATE", 48000),
        "channels": env_int("PULSE_CHANNELS", 2),
        "auto_reconnect": env_bool("ECHOWARP_CLIENT_AUTO_RECONNECT", False),
        "auto_reconnect_attempts": env_int("ECHOWARP_CLIENT_AUTO_RECONNECT_ATTEMPTS", 0),
        "record_dir": os.environ.get("ECHOWARP_RECORD_DIR", ""),
        "audio_buffer_frames": env_int("ECHOWARP_AUDIO_BUFFER_FRAMES", 5),
        "tls_cert": os.environ.get("ECHOWARP_TLS_CERT", ""),
        "tls_key": os.environ.get("ECHOWARP_TLS_KEY", ""),
        "rate_limit": env_int("ECHOWARP_RATE_LIMIT", 5),
        "max_auth_failures": env_int("ECHOWARP_MAX_AUTH_FAILURES", 5),
        "ban_file": os.environ.get("ECHOWARP_BAN_FILE", ""),
        "hwid_required": env_bool("ECHOWARP_HWID_REQUIRED", False),
        "server_muted": env_bool("ECHOWARP_SERVER_MUTED", False),
        "record": os.environ.get("ECHOWARP_RECORD", ""),
        "recording_retention_hours": env_float("RECORDING_RETENTION_HOURS", 168),
    }


def merge_config(current, incoming):
    merged = dict(current)
    for key, value in incoming.items():
        if key == "password" and value in (None, "__KEEP__"):
            continue
        merged[key] = value

    merged["port"] = int(merged.get("port") or 4415)
    merged["max_reconnect"] = int(merged.get("max_reconnect") or 0)
    merged["auto_reconnect_attempts"] = int(merged.get("auto_reconnect_attempts") or 0)
    merged["restart_delay"] = max(1, int(merged.get("restart_delay") or 3))
    merged["max_clients"] = max(1, int(merged.get("max_clients") or 1))
    merged["sample_rate"] = int(merged.get("sample_rate") or 48000)
    merged["channels"] = int(merged.get("channels") or 2)
    merged["audio_buffer_frames"] = max(1, int(merged.get("audio_buffer_frames") or 5))
    merged["rate_limit"] = max(0, int(merged.get("rate_limit") or 0))
    merged["max_auth_failures"] = max(0, int(merged.get("max_auth_failures") or 0))
    retention = float(merged.get("recording_retention_hours", 168))
    if not math.isfinite(retention) or not 1 <= retention <= 87600:
        raise ValueError("Retencao deve estar entre 1 e 87600 horas")
    merged["recording_retention_hours"] = retention

    for key in ("capture_devices", "playback_devices", "stun_servers"):
        value = merged.get(key)
        if isinstance(value, str):
            merged[key] = [item.strip() for item in value.splitlines() if item.strip()]
        elif not isinstance(value, list):
            merged[key] = []

    for key in (
        "enabled",
        "discover",
        "tls_insecure",
        "virtual_mic",
        "loopback",
        "aec",
        "auto_restart",
        "auto_reconnect",
        "server_discovery",
        "hwid_required",
        "server_muted",
    ):
        merged[key] = bool(merged.get(key))

    return merged


def redacted_config(config):
    data = dict(config)
    data["password"] = ""
    data["password_set"] = bool(config.get("password"))
    return data


def normalize_server_entry(entry, existing=None, fallback_password=""):
    existing = existing or {}
    server_id = str(entry.get("id") or existing.get("id") or secrets.token_urlsafe(8)).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{4,80}", server_id):
        server_id = secrets.token_urlsafe(8)

    name = str(entry.get("name") or existing.get("name") or "").strip()
    address = str(entry.get("address") or entry.get("host") or existing.get("address") or "").strip()
    if not name:
        raise ValueError("Defina um nome para o servidor")
    if not address:
        raise ValueError("Informe o endereco do servidor")

    try:
        port = int(entry.get("port") or existing.get("port") or 4415)
    except (TypeError, ValueError) as exc:
        raise ValueError("Porta invalida") from exc
    if port < 1 or port > 65535:
        raise ValueError("Porta invalida")

    password_value = entry.get("password", "__KEEP__")
    if password_value in (None, "__KEEP__"):
        password = str(existing.get("password") or "")
    elif password_value == "__KEEP_CURRENT__":
        password = str(fallback_password or "")
    else:
        password = str(password_value)

    return {
        "id": server_id,
        "name": name[:80],
        "address": address[:255],
        "port": port,
        "password": password,
        "nickname": str(entry.get("nickname") or existing.get("nickname") or "")[:40],
        "discover_timeout": str(entry.get("discover_timeout") or existing.get("discover_timeout") or "3s")[:20],
        "tls_insecure": bool(entry.get("tls_insecure", existing.get("tls_insecure", False))),
    }


def redacted_servers(servers, current_config=None):
    current_config = current_config or {}
    current_address = str(current_config.get("address") or "")
    current_port = int(current_config.get("port") or 4415)
    items = []
    for server in servers:
        data = dict(server)
        data["password"] = ""
        data["password_set"] = bool(server.get("password"))
        data["active"] = (
            bool(current_config.get("enabled", True))
            and str(server.get("address") or "") == current_address
            and int(server.get("port") or 4415) == current_port
        )
        items.append(data)
    return items


def append_log(line):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with RUNTIME_LOG.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(line.rstrip("\n") + "\n")


def redact_sensitive_text(text):
    text = re.sub(r"(--password(?:=|\s+))(?!matches\b)(\S+)", r"\1***", text)
    text = re.sub(r'("password"\s*:\s*")[^"]*', r'\1***', text)
    return text


def redacted_args(args):
    safe = []
    skip_next = False
    for index, part in enumerate(args):
        if skip_next:
            safe.append("***")
            skip_next = False
            continue
        if part == "--password":
            safe.append(part)
            skip_next = index + 1 < len(args)
            if not skip_next:
                safe.append("***")
            continue
        if part.startswith("--password="):
            safe.append("--password=***")
            continue
        safe.append(part)
    return safe


def tail_file(path, limit=160):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    return [redact_sensitive_text(line) for line in lines[-limit:]]


def history_segments(playlist, seconds):
    segments = []
    duration = None
    for line in playlist.splitlines():
        if line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif line and not line.startswith("#") and duration is not None:
            if not re.fullmatch(r"segment_[0-9]+\.ts", line) or not math.isfinite(duration) or duration <= 0:
                raise ValueError("Historico invalido")
            segments.append((line, duration))
            duration = None
    selected = []
    total = 0
    for segment in reversed(segments):
        selected.append(segment)
        total += segment[1]
        if total >= seconds:
            break
    return list(reversed(selected)), total


class EchoWarpController:
    def __init__(self):
        self.lock = threading.RLock()
        self.lifecycle_lock = threading.RLock()
        self.connection_generation = 0
        self.recording_proc = None
        self.recording_prefix = None
        self.recording_error = ""
        self.history_export_lock = threading.Lock()
        self.server_availability = {}
        self.stop_event = threading.Event()
        self.config = self.load_config()
        self.servers = self.load_servers()
        self.ffmpeg_proc = None
        self.echowarp_proc = None
        self.audio_meter_proc = None
        self.audio_level = {
            "ok": False,
            "rms": 0.0,
            "peak": 0.0,
            "dbfs": -120.0,
            "detail": "audio meter starting",
            "updated_at": None,
        }
        self.intentional_stop = False
        self.connection_blocked = False
        self.silence_started_at = None
        self.last_silence_restart_at = 0
        self.status = {
            "state": "starting",
            "detail": "Starting EchoWarp Web",
            "server": self.config.get("address", ""),
            "port": self.config.get("port", 4415),
        }

    def load_config(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config = default_config()
        if CONFIG_FILE.exists():
            try:
                saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                config = merge_config(config, saved)
            except (OSError, json.JSONDecodeError):
                append_log("Could not read config.json; using environment defaults")
        self.save_config(config)
        return config

    def save_config(self, config=None):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config = config or self.config
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(CONFIG_FILE)

    def load_servers(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        servers = []
        if SERVERS_FILE.exists():
            try:
                saved = json.loads(SERVERS_FILE.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    saved = saved.get("servers", [])
                for item in saved if isinstance(saved, list) else []:
                    if isinstance(item, dict):
                        servers.append(normalize_server_entry(item))
            except (OSError, ValueError, json.JSONDecodeError):
                append_log("Could not read servers.json; starting with empty server list")

        if not servers and str(self.config.get("address", "")).strip():
            try:
                servers.append(
                    normalize_server_entry(
                        {
                            "name": self.config.get("nickname") or self.config.get("address"),
                            "address": self.config.get("address"),
                            "port": self.config.get("port", 4415),
                            "password": "__KEEP_CURRENT__",
                            "nickname": self.config.get("nickname", ""),
                            "discover_timeout": self.config.get("discover_timeout", "3s"),
                            "tls_insecure": self.config.get("tls_insecure", False),
                        },
                        fallback_password=self.config.get("password", ""),
                    )
                )
            except ValueError:
                pass

        self.save_servers(servers)
        return servers

    def save_servers(self, servers=None):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        servers = servers if servers is not None else self.servers
        tmp = SERVERS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(servers, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(SERVERS_FILE)

    def list_servers(self):
        with self.lock:
            items = redacted_servers(self.servers, self.config)
            for item in items:
                item["online"] = self.server_availability.get((item["address"], item["port"]))
            return items

    def monitor_server_availability(self):
        while not self.stop_event.is_set():
            with self.lock:
                targets = list({(item["address"], item["port"]) for item in self.servers})
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda target: check_server_online(*target, timeout=1), targets))
            with self.lock:
                self.server_availability = dict(zip(targets, results))
            self.stop_event.wait(15)

    def save_server(self, payload):
        with self.lock:
            server_id = str(payload.get("id") or "").strip()
            index = next((idx for idx, item in enumerate(self.servers) if item.get("id") == server_id), None)
            existing = self.servers[index] if index is not None else None
            server = normalize_server_entry(
                payload,
                existing=existing,
                fallback_password=self.config.get("password", ""),
            )
            if index is None:
                self.servers.append(server)
            else:
                self.servers[index] = server
            self.save_servers()
            return redacted_servers(self.servers, self.config)

    def delete_server(self, server_id):
        with self.lock:
            before = len(self.servers)
            self.servers = [server for server in self.servers if server.get("id") != server_id]
            if len(self.servers) == before:
                raise ValueError("Servidor nao encontrado")
            self.save_servers()
            return redacted_servers(self.servers, self.config)

    def server_config_payload(self, server):
        payload = {
            "enabled": True,
            "role": "client",
            "address": server.get("address", ""),
            "port": server.get("port", 4415),
            "password": server.get("password", ""),
            "nickname": server.get("nickname", ""),
            "discover_timeout": server.get("discover_timeout", "3s"),
            "tls_insecure": bool(server.get("tls_insecure", False)),
        }
        return payload

    def connect_server(self, server_id):
        with self.lock:
            server = next((dict(item) for item in self.servers if item.get("id") == server_id), None)
        if not server:
            raise ValueError("Servidor nao encontrado")
        return self.update_config(self.server_config_payload(server), restart=True)

    def write_status(self, state=None, detail=None):
        with self.lock:
            if state:
                self.status["state"] = state
            if detail is not None:
                self.status["detail"] = detail
            self.status.update(
                {
                    "server": self.config.get("address", ""),
                    "port": self.config.get("port", 4415),
                    "role": self.config.get("role", "client"),
                    "enabled": bool(self.config.get("enabled", True)),
                    "stream": "/hls/live.m3u8",
                    "echowarp_pid": self.echowarp_proc.pid if self.echowarp_proc else None,
                    "ffmpeg_pid": self.ffmpeg_proc.pid if self.ffmpeg_proc else None,
                    "updated_at": int(time.time()),
                }
            )
            HLS_DIR.mkdir(parents=True, exist_ok=True)
            STATUS_FILE.write_text(json.dumps(self.status), encoding="utf-8")

    def start(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        HLS_DIR.mkdir(parents=True, exist_ok=True)
        for item in HLS_DIR.glob("*"):
            if item.is_file():
                item.unlink()
        RUNTIME_LOG.write_text("", encoding="utf-8")
        self.start_ffmpeg()
        self.start_audio_meter()
        self.start_silence_watchdog()
        threading.Thread(target=self.monitor_recordings, daemon=True).start()
        threading.Thread(target=self.monitor_server_availability, daemon=True).start()
        if self.config.get("enabled", True):
            self.start_echowarp()
        else:
            self.write_status("stopped", "EchoWarp stopped")

    def stop(self):
        self.stop_event.set()
        self.stop_echowarp(save=False)
        self.stop_process("audio meter", self.audio_meter_proc)
        self.stop_process("ffmpeg", self.ffmpeg_proc)

    def start_audio_meter(self):
        threading.Thread(target=self.monitor_audio_level, daemon=True).start()

    def start_silence_watchdog(self):
        seconds = env_int("SILENCE_RESTART_SECONDS", 45)
        if seconds > 0:
            threading.Thread(target=self.monitor_silence, daemon=True).start()

    def monitor_silence(self):
        restart_after = env_int("SILENCE_RESTART_SECONDS", 45)
        cooldown = env_int("SILENCE_RESTART_COOLDOWN_SECONDS", 120)
        dbfs_threshold = env_float("SILENCE_DBFS_THRESHOLD", -88.0)
        peak_threshold = env_float("SILENCE_PEAK_THRESHOLD", 0.0005)

        while not self.stop_event.is_set():
            time.sleep(5)
            with self.lock:
                config = dict(self.config)
                status = dict(self.status)
                level = dict(self.audio_level)

            active_client = (
                config.get("enabled", True)
                and config.get("role", "client") == "client"
                and status.get("state") == "connected"
            )
            silent = (
                level.get("ok")
                and float(level.get("dbfs", -120.0)) <= dbfs_threshold
                and float(level.get("peak", 0.0)) <= peak_threshold
            )

            now = time.time()
            if not active_client or not silent:
                with self.lock:
                    self.silence_started_at = None
                continue

            with self.lock:
                if self.silence_started_at is None:
                    self.silence_started_at = now
                    continue
                silent_for = now - self.silence_started_at
                in_cooldown = now - self.last_silence_restart_at < cooldown
                if silent_for < restart_after or in_cooldown:
                    continue
                self.last_silence_restart_at = now
                self.silence_started_at = None

            append_log(f"Audio silent for {int(silent_for)}s; restarting EchoWarp")
            self.write_status("reconnecting", "Sem audio; reiniciando EchoWarp")
            self.restart_echowarp()

    def monitor_audio_level(self):
        pulse_sink = os.environ.get("PULSE_SINK_NAME", "echowarp_web")
        pulse_rate = env_int("PULSE_RATE", 48000)
        pulse_channels = env_int("PULSE_CHANNELS", 2)
        chunk_size = max(4096, min((pulse_rate * pulse_channels * 2) // 4, 65536))
        args = [
            "parec",
            "--raw",
            "--format=s16le",
            f"--rate={pulse_rate}",
            f"--channels={pulse_channels}",
            "-d",
            f"{pulse_sink}.monitor",
        ]

        while not self.stop_event.is_set():
            try:
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                with self.lock:
                    self.audio_level.update(
                        {"ok": False, "detail": str(exc), "updated_at": int(time.time())}
                    )
                return

            with self.lock:
                self.audio_meter_proc = proc
            append_log("Started PulseAudio level meter")

            try:
                while not self.stop_event.is_set() and proc.poll() is None:
                    raw = proc.stdout.read(chunk_size) if proc.stdout else b""
                    if not raw:
                        break
                    level = pcm_level(raw)
                    level.update(
                        {
                            "ok": True,
                            "detail": "reading echowarp_web.monitor",
                            "updated_at": int(time.time()),
                        }
                    )
                    with self.lock:
                        self.audio_level.update(level)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2)
                with self.lock:
                    if self.audio_meter_proc is proc:
                        self.audio_meter_proc = None
                    if not self.stop_event.is_set():
                        self.audio_level.update(
                            {
                                "ok": False,
                                "detail": "audio meter stopped; retrying",
                                "updated_at": int(time.time()),
                            }
                        )
            if not self.stop_event.is_set():
                time.sleep(2)

    def start_ffmpeg(self):
        with self.lock:
            if self.ffmpeg_proc and self.ffmpeg_proc.poll() is None:
                return
            pulse_sink = os.environ.get("PULSE_SINK_NAME", "echowarp_web")
            pulse_rate = str(env_int("PULSE_RATE", 48000))
            pulse_channels = str(env_int("PULSE_CHANNELS", 2))
            hls_time = str(max(0.25, env_float("HLS_TIME", 0.5)))
            hls_list_size = str(math.ceil(1800 / float(hls_time)))
            bitrate = os.environ.get("AUDIO_BITRATE", "128k")
            audio_filter = os.environ.get("AUDIO_FILTER", "dynaudnorm=f=75:g=3:p=0.95")
            args = [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "warning",
                "-f",
                "pulse",
                "-sample_rate",
                pulse_rate,
                "-channels",
                pulse_channels,
                "-fragment_size",
                str(max(256, int(pulse_rate) * int(pulse_channels) * 2 // 50)),
                "-i",
                f"{pulse_sink}.monitor",
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                bitrate,
                "-ar",
                pulse_rate,
                "-ac",
                pulse_channels,
            ]
            if audio_filter.strip():
                args.extend(["-af", audio_filter.strip()])
            args.extend([
                "-f",
                "hls",
                "-hls_time",
                hls_time,
                "-hls_list_size",
                hls_list_size,
                "-hls_flags",
                "delete_segments+append_list+omit_endlist+program_date_time+temp_file",
                "-hls_segment_filename",
                str(HLS_DIR / "segment_%05d.ts"),
                str(HLS_DIR / "live.m3u8"),
            ])
            self.ffmpeg_proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            append_log("Started FFmpeg HLS encoder")
            threading.Thread(
                target=self.pipe_logs,
                args=("ffmpeg", self.ffmpeg_proc),
                daemon=True,
            ).start()
            threading.Thread(target=self.monitor_ffmpeg, daemon=True).start()

    def monitor_ffmpeg(self):
        proc = self.ffmpeg_proc
        code = proc.wait()
        if self.stop_event.is_set():
            return
        with self.lock:
            if proc is not self.ffmpeg_proc:
                return
        append_log(f"FFmpeg stopped with code {code}")
        self.write_status("error", f"FFmpeg stopped with code {code}")
        time.sleep(2)
        if not self.stop_event.is_set():
            self.start_ffmpeg()

    def start_echowarp(self):
        with self.lock:
            if self.stop_event.is_set() or not self.config.get("enabled", True):
                return
            if self.echowarp_proc and self.echowarp_proc.poll() is None:
                return
            self.connection_generation += 1
            self.intentional_stop = False
            self.connection_blocked = False
            args = self.build_echowarp_args()
            self.write_status("connecting", "Starting EchoWarp")
            safe_args = redacted_args(args)
            append_log("Starting: " + " ".join(shlex.quote(part) for part in safe_args))
            self.echowarp_proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            threading.Thread(
                target=self.pipe_logs,
                args=("echowarp", self.echowarp_proc),
                daemon=True,
            ).start()
            threading.Thread(target=self.monitor_echowarp, args=(self.echowarp_proc,), daemon=True).start()

    def monitor_echowarp(self, proc):
        code = proc.wait()
        if self.stop_event.is_set():
            return
        with self.lock:
            if proc is not self.echowarp_proc:
                return
            self.echowarp_proc = None
            generation = self.connection_generation
            if self.connection_blocked:
                self.write_status(self.status.get("state", "error"), self.status.get("detail", "EchoWarp stopped"))
                return
            if self.intentional_stop or not self.config.get("enabled", True):
                self.write_status("stopped", "EchoWarp stopped")
                return
            delay = int(self.config.get("restart_delay", 3))
            auto_restart = bool(self.config.get("auto_restart", True))
            append_log(f"EchoWarp stopped with code {code}")
            if not auto_restart:
                self.write_status("error", f"EchoWarp stopped with code {code}")
                return
            self.write_status("reconnecting", f"EchoWarp stopped with code {code}; retrying")
        if self.stop_event.wait(delay):
            return
        with self.lifecycle_lock, self.lock:
            if generation == self.connection_generation and not self.intentional_stop:
                self.start_echowarp()

    def pipe_logs(self, label, proc):
        if not proc.stdout:
            return
        for line in proc.stdout:
            raw = line.rstrip()
            entry = f"[{label}] {redact_sensitive_text(raw)}"
            print(entry, flush=True)
            append_log(entry)
            if label == "echowarp":
                with self.lock:
                    if proc is self.echowarp_proc and not self.intentional_stop:
                        self.observe_echowarp_log(raw)

    def observe_echowarp_log(self, line):
        lower = line.lower()
        handshake_timeout = "context deadline exceeded" in lower or "timeout" in lower
        if "ip banned" in lower or "device banned" in lower or "[e303]" in lower:
            self.block_echowarp("access_blocked", "Origem bloqueou o acesso; verifique a lista de IPs banidos")
        elif "[e305]" in lower or "key exchange failed" in lower:
            self.block_echowarp("handshake_error", "Origem rejeitou a troca de chaves; verifique bloqueio de IP ou compatibilidade")
        elif "invalid password" in lower:
            self.block_echowarp("auth_error", "Senha do EchoWarp invalida")
        elif "authentication failed" in lower and not handshake_timeout:
            self.block_echowarp("auth_error", "Autenticacao rejeitada pela origem; verifique o motivo nos logs do servidor")
        elif handshake_timeout:
            self.write_status("reconnecting", "Handshake do EchoWarp expirou; tentando novamente")
        elif "server not reachable" in lower:
            self.write_status("unreachable", "Servidor EchoWarp indisponivel")
        elif "connection state changed state=connected" in lower or "player started" in lower:
            self.write_status("connected", "Conectado ao EchoWarp")
        elif "datachannel ready" in lower:
            self.write_status("connected", "Canal de audio ativo")
        elif "authenticated successfully" in lower:
            self.write_status("connecting", "Autenticado; aguardando audio")
        elif "connected to server" in lower:
            self.write_status("connecting", "Servidor respondeu; autenticando")
        elif "connecting to server" in lower:
            self.write_status("connecting", "Conectando ao EchoWarp")
        elif "connection lost" in lower:
            self.write_status("reconnecting", "Conexao perdida; reconectando")
        elif "server" in lower and ("listening" in lower or "started" in lower):
            self.write_status("running", "EchoWarp server ativo")

    def block_echowarp(self, state, detail):
        with self.lock:
            self.connection_blocked = True
            self.intentional_stop = True
            proc = self.echowarp_proc
            self.write_status(state, detail)
        if proc and proc.poll() is None:
            append_log(f"Stopping EchoWarp after {state}")
            proc.terminate()

    def stop_process(self, label, proc):
        if not proc or proc.poll() is not None:
            return
        append_log(f"Stopping {label}")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def restart_echowarp(self):
        with self.lifecycle_lock:
            self.stop_echowarp(save=False)
            with self.lock:
                self.config["enabled"] = True
                self.save_config()
            self.start_echowarp()

    def stop_echowarp(self, save=True):
        with self.lifecycle_lock:
            self.stop_recording()
            with self.lock:
                self.connection_generation += 1
                self.intentional_stop = True
                if save:
                    self.config["enabled"] = False
                    self.save_config()
                proc = self.echowarp_proc
            self.stop_process("EchoWarp", proc)
            self.write_status("stopped", "EchoWarp stopped")

    def recording_status(self):
        with self.lock:
            active = bool(self.recording_proc and self.recording_proc.poll() is None)
            return {"active": active, "error": self.recording_error,
                    "retention_hours": self.config.get("recording_retention_hours", 168)}

    def save_history(self, minutes):
        minutes = float(minutes)
        if not math.isfinite(minutes) or not 1 <= minutes <= 30:
            raise ValueError("Escolha entre 1 e 30 minutos")
        if not self.history_export_lock.acquire(blocking=False):
            raise ValueError("Ja existe um historico sendo salvo; aguarde")
        try:
            with tempfile.TemporaryDirectory(prefix="echowarp-history-") as folder:
                snapshot = Path(folder)
                # Copy before encoding so rolling deletion cannot invalidate the export.
                selected, total = history_segments((HLS_DIR / "live.m3u8").read_text(), minutes * 60)
                if not selected:
                    raise ValueError("Historico ainda nao disponivel")
                for name, _ in selected:
                    (snapshot / name).write_bytes((HLS_DIR / name).read_bytes())
                manifest = snapshot / "input.txt"
                manifest.write_text("".join(f"file '{name}'\n" for name, _ in selected), encoding="utf-8")
                output = snapshot / "saved.m4a"
                run = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
                                      "-f", "concat", "-safe", "1", "-i", str(manifest),
                                      "-ss", str(max(0, total - minutes * 60)), "-vn", "-c:a", "copy",
                                      "-movflags", "+faststart", str(output)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=120)
                if run.returncode:
                    append_log("[history] " + redact_sensitive_text(run.stderr[-2000:]))
                    raise ValueError("Nao foi possivel salvar o historico")
                name = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + secrets.token_hex(4) + "-00000.m4a"
                with self.lock:
                    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
                    temporary = RECORDINGS_DIR / (name + ".tmp")
                    try:
                        shutil.copyfile(output, temporary)
                        temporary.replace(RECORDINGS_DIR / name)
                    finally:
                        temporary.unlink(missing_ok=True)
                return {"name": name, "seconds": min(total, minutes * 60)}
        finally:
            self.history_export_lock.release()

    def start_recording(self):
        with self.lock:
            if self.recording_proc and self.recording_proc.poll() is None:
                return
            if not self.config.get("enabled") or self.status.get("state") != "connected":
                raise ValueError("Conecte a um servidor antes de gravar")
            RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
            self.recording_prefix = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + secrets.token_hex(4)
            sink = os.environ.get("PULSE_SINK_NAME", "echowarp_web")
            args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
                    "-f", "pulse", "-i", f"{sink}.monitor", "-vn", "-c:a", "aac",
                    "-b:a", os.environ.get("AUDIO_BITRATE", "128k"),
                    "-f", "segment", "-segment_time", "1800", "-reset_timestamps", "1",
                    str(RECORDINGS_DIR / (self.recording_prefix + "-%05d.m4a"))]
            self.recording_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            self.recording_error = ""
            threading.Thread(target=self.monitor_recording, args=(self.recording_proc,), daemon=True).start()

    def monitor_recording(self, proc):
        _, error = proc.communicate()
        with self.lock:
            if proc is self.recording_proc and proc.returncode:
                self.recording_error = "Falha na gravacao; verifique os logs"
                append_log("[recording] " + redact_sensitive_text((error or "")[-2000:]))

    def stop_recording(self):
        with self.lock:
            proc = self.recording_proc
            if proc and proc.poll() is None:
                # SIGINT lets FFmpeg finalize the current M4A segment.
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            self.recording_proc = None
            self.recording_prefix = None

    def recording_files(self):
        with self.lock:
            files = sorted(RECORDINGS_DIR.glob("*.m4a"), reverse=True)
            active = self.recording_status()["active"]
            current = next((p for p in files if p.name.startswith(self.recording_prefix or "")), None) if active else None
            result = []
            for path in files:
                if path == current:
                    continue
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                result.append({"name": path.name, "bytes": stat.st_size, "created_at": int(stat.st_mtime)})
            return result

    def cleanup_recordings(self):
        with self.lock:
            cutoff = time.time() - self.config.get("recording_retention_hours", 168) * 3600
            for item in self.recording_files():
                if item["created_at"] < cutoff:
                    (RECORDINGS_DIR / item["name"]).unlink(missing_ok=True)

    def monitor_recordings(self):
        while not self.stop_event.is_set():
            try:
                self.cleanup_recordings()
            except OSError as exc:
                append_log(f"Recording cleanup failed: {exc}")
            self.stop_event.wait(60)

    def update_config(self, incoming, restart=True):
        with self.lock:
            self.config = merge_config(self.config, incoming)
            self.save_config()
            enabled = self.config.get("enabled", True)
        if restart:
            if enabled:
                self.restart_echowarp()
            else:
                self.stop_echowarp(save=False)
        return redacted_config(self.config)

    def add_common_audio_flags(self, args, config):
        if config.get("device_mode") == "id" and str(config.get("device_id", "")).strip():
            args.extend(["--device", str(config["device_id"]).strip()])
        elif str(config.get("device_name", "")).strip():
            args.extend(["--device-name", str(config["device_name"]).strip()])

        for device in config.get("capture_devices", []):
            args.extend(["--capture-device", str(device)])
        for device in config.get("playback_devices", []):
            args.extend(["--playback-device", str(device)])
        if config.get("virtual_mic"):
            args.append("--virtual-mic")
        if config.get("loopback"):
            args.append("--loopback")
        if config.get("aec"):
            args.append("--aec")

    def add_common_network_flags(self, args, config):
        args.extend(["--port", str(config.get("port", 4415))])
        if config.get("password"):
            args.extend(["--password", str(config["password"])])
        for server in config.get("stun_servers", []):
            args.extend(["--stun-server", str(server)])
        if str(config.get("extra_args", "")).strip():
            args.extend(shlex.split(str(config["extra_args"])))

    def add_common_config_flags(self, args, config):
        if str(config.get("record_dir", "")).strip():
            args.extend(["--record-dir", str(config["record_dir"]).strip()])

    def build_echowarp_args(self):
        config = dict(self.config)
        role = config.get("role", "client")
        args = [ECHOWARP_BIN, role, "--no-interactive"]

        self.add_common_audio_flags(args, config)
        self.add_common_network_flags(args, config)
        self.add_common_config_flags(args, config)

        if role == "client":
            if str(config.get("address", "")).strip():
                args.extend(["--address", str(config["address"]).strip()])
            if not config.get("discover", True):
                args.append("--discover=false")
            elif str(config.get("discover_timeout", "")).strip():
                args.extend(["--discover-timeout", str(config["discover_timeout"]).strip()])
            if config.get("tls_insecure"):
                args.append("--tls-insecure")
            if str(config.get("nickname", "")).strip():
                args.extend(["--nickname", str(config["nickname"]).strip()])
            args.extend(["--max-reconnect", str(config.get("max_reconnect", 5))])
            if config.get("auto_reconnect"):
                args.append("--auto-reconnect")
            if int(config.get("auto_reconnect_attempts") or 0) > 0:
                args.extend(["--auto-reconnect-attempts", str(config["auto_reconnect_attempts"])])
        else:
            mode = str(config.get("server_mode", "normal") or "normal")
            args.extend(["--mode", mode])
            args.extend(["--sample-rate", str(config.get("sample_rate", 48000))])
            args.extend(["--channels", str(config.get("channels", 2))])
            args.extend(["--audio-buffer-frames", str(config.get("audio_buffer_frames", 5))])
            args.extend(["--max-clients", str(config.get("max_clients", 1))])
            if str(config.get("server_name", "")).strip():
                args.extend(["--server-name", str(config["server_name"]).strip()])
            if not config.get("server_discovery", True):
                args.append("--no-discovery")
            if str(config.get("tls_cert", "")).strip():
                args.extend(["--tls-cert", str(config["tls_cert"]).strip()])
            if str(config.get("tls_key", "")).strip():
                args.extend(["--tls-key", str(config["tls_key"]).strip()])
            args.extend(["--rate-limit", str(config.get("rate_limit", 5))])
            args.extend(["--max-auth-failures", str(config.get("max_auth_failures", 5))])
            if str(config.get("ban_file", "")).strip():
                args.extend(["--ban-file", str(config["ban_file"]).strip()])
            if config.get("hwid_required"):
                args.append("--hwid-required")
            if config.get("server_muted"):
                args.append("--server-muted")
            if str(config.get("record", "")).strip():
                args.extend(["--record", str(config["record"]).strip()])

        args.extend(["--log-level", str(config.get("log_level", "info"))])
        return args


def parse_devices(text):
    section = None
    devices = {"input": [], "output": [], "raw": text}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("input devices"):
            section = "input"
            continue
        if stripped.lower().startswith("output devices"):
            section = "output"
            continue
        if not section or not stripped.startswith("["):
            continue
        try:
            ident, rest = stripped.split("]", 1)
            device_id = ident.strip("[")
            name, meta = rest.strip(), ""
            if "(" in name and name.endswith(")"):
                name, meta = name.rsplit("(", 1)
                meta = meta.rstrip(")")
            devices[section].append(
                {
                    "id": device_id,
                    "name": name.strip(),
                    "meta": meta,
                }
            )
        except ValueError:
            continue
    return devices


def run_command(args, timeout=10):
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout


def dns_name(name):
    name = name.rstrip(".")
    result = bytearray()
    for label in name.split("."):
        encoded = label.encode("utf-8")
        result.append(len(encoded))
        result.extend(encoded)
    result.append(0)
    return bytes(result)


def read_dns_name(data, offset):
    labels = []
    jumped = False
    original = offset
    seen = set()
    while True:
        if offset >= len(data):
            return ".".join(labels), offset
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                break
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if pointer in seen:
                break
            seen.add(pointer)
            if not jumped:
                original = offset + 2
            offset = pointer
            jumped = True
            continue
        offset += 1
        labels.append(data[offset : offset + length].decode("utf-8", errors="replace"))
        offset += length
    return ".".join(labels), (original if jumped else offset)


def parse_dns_response(data):
    if len(data) < 12:
        return []
    counts = struct.unpack("!HHHHHH", data[:12])[2:]
    offset = 12
    records = []
    for _ in range(counts[0]):
        _, offset = read_dns_name(data, offset)
        offset += 4
    for _ in range(sum(counts[1:])):
        name, offset = read_dns_name(data, offset)
        if offset + 10 > len(data):
            break
        rr_type, rr_class, ttl, rd_len = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        start = offset
        end = offset + rd_len
        if end > len(data):
            break
        value = None
        if rr_type == 12:
            value, _ = read_dns_name(data, start)
        elif rr_type == 33 and rd_len >= 6:
            priority, weight, port = struct.unpack("!HHH", data[start : start + 6])
            target, _ = read_dns_name(data, start + 6)
            value = {"priority": priority, "weight": weight, "port": port, "target": target}
        elif rr_type == 16:
            items = []
            cursor = start
            while cursor < end:
                length = data[cursor]
                cursor += 1
                items.append(data[cursor : cursor + length].decode("utf-8", errors="replace"))
                cursor += length
            value = items
        elif rr_type == 1 and rd_len == 4:
            value = socket.inet_ntoa(data[start:end])
        elif rr_type == 28 and rd_len == 16:
            value = socket.inet_ntop(socket.AF_INET6, data[start:end])
        records.append({"name": name, "type": rr_type, "class": rr_class, "ttl": ttl, "value": value})
        offset = end
    return records


def mdns_discover(timeout=3.0):
    service = "_echowarp._tcp.local"
    packet = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
    packet += dns_name(service) + struct.pack("!HH", 12, 1)
    services = {}
    addresses = {}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.4)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    try:
        sock.sendto(packet, ("224.0.0.251", 5353))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, _ = sock.recvfrom(9000)
            except socket.timeout:
                continue
            for record in parse_dns_response(data):
                name = record["name"].rstrip(".")
                value = record["value"]
                if record["type"] == 12 and isinstance(value, str):
                    instance = value.rstrip(".")
                    services.setdefault(instance, {"instance": instance})
                elif record["type"] == 33 and isinstance(value, dict):
                    services.setdefault(name, {"instance": name}).update(value)
                elif record["type"] == 16 and isinstance(value, list):
                    txt = {}
                    for item in value:
                        if "=" in item:
                            key, txt_value = item.split("=", 1)
                            txt[key] = txt_value
                        else:
                            txt[item] = True
                    services.setdefault(name, {"instance": name})["txt"] = txt
                elif record["type"] in (1, 28) and isinstance(value, str):
                    addresses[name] = value
    finally:
        sock.close()

    result = []
    for item in services.values():
        host = str(item.get("target", "")).rstrip(".")
        address = addresses.get(host, "")
        instance = str(item.get("instance", ""))
        name = instance.replace("." + service, "").rstrip(".")
        if item.get("port"):
            result.append(
                {
                    "name": name or instance,
                    "host": host,
                    "address": address,
                    "port": item.get("port"),
                    "txt": item.get("txt", {}),
                    "source": "mdns",
                }
            )
    return result


def local_lan_network():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        address = sock.getsockname()[0]
        sock.close()
        return ipaddress.ip_network(address + "/24", strict=False)
    except OSError:
        return None


def check_server_online(address, port, timeout=1):
    # Never probe the signaling port: incomplete logins count toward IP bans.
    try:
        if not 1 <= int(port) < 65535:
            return False
        host = str(address).strip().strip("[]")
        if ":" in host:
            host = f"[{ipaddress.IPv6Address(host)}]"
        elif not re.fullmatch(r"[A-Za-z0-9._-]+", host):
            return False
        url = f"http://{host}:{int(port) + 1}/api/v1/session/info"
        with build_opener(ProxyHandler({})).open(url, timeout=timeout) as response:
            data = json.loads(response.read(65536))
        return isinstance(data, dict) and bool(data.get("server_version"))
    except (OSError, ValueError):
        return False


def scan_open_ports(port, limit=256):
    network = local_lan_network()
    if not network:
        return []
    hosts = list(network.hosts())[:limit]
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as executor:
        future_map = {executor.submit(check_server_online, host, port): host for host in hosts}
        for future in concurrent.futures.as_completed(future_map):
            host = future_map[future]
            try:
                if future.result():
                    found.append(
                        {
                            "name": f"{host}:{port}",
                            "host": str(host),
                            "address": str(host),
                            "port": port,
                            "txt": {},
                            "source": "tcp",
                        }
                    )
            except OSError:
                pass
    return found


class EchoWarpHandler(BaseHTTPRequestHandler):
    server_version = "EchoWarpWeb/1.0"

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/login", "/login.html", "/styles.css"}:
            self.serve_static("/login.html" if parsed.path != "/styles.css" else parsed.path, head_only=True)
            return
        if not self.ensure_authenticated(parsed.path):
            return
        if parsed.path.startswith("/api/") or parsed.path == "/status.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.serve_static(parsed.path, head_only=True)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/login", "/login.html"}:
            if self.is_authenticated():
                self.send_redirect(safe_next_url(parse_qs(parsed.query).get("next", ["/"])[0]))
                return
            self.serve_static("/login.html")
            return
        if path == "/styles.css":
            self.serve_static(path)
            return
        if not self.ensure_authenticated(path):
            return
        if path == "/api/status":
            data = dict(self.server.controller.status)
            data["config"] = redacted_config(self.server.controller.config)
            data["audio_level"] = dict(self.server.controller.audio_level)
            data["hls_ready"] = (HLS_DIR / "live.m3u8").exists()
            data["recording"] = self.server.controller.recording_status()
            self.send_json(data)
            return
        if path == "/api/recordings":
            self.send_json({**self.server.controller.recording_status(), "files": self.server.controller.recording_files()})
            return
        if path.startswith("/api/recordings/download/"):
            name = unquote(path.rsplit("/", 1)[-1])
            if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{8}-[0-9]{5}\.m4a", name):
                self.send_error(404)
                return
            with self.server.controller.lock:
                if name not in {item["name"] for item in self.server.controller.recording_files()}:
                    self.send_error(404)
                    return
                try:
                    content = (RECORDINGS_DIR / name).read_bytes()
                except FileNotFoundError:
                    self.send_error(404)
                    return
            self.send_response(200)
            self.send_header("Content-Type", "audio/mp4")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if path == "/status.json":
            data = dict(self.server.controller.status)
            self.send_json(data)
            return
        if path == "/api/config":
            self.send_json(redacted_config(self.server.controller.config))
            return
        if path == "/api/servers":
            self.send_json({"ok": True, "servers": self.server.controller.list_servers()})
            return
        if path == "/api/devices":
            try:
                code, output = run_command([ECHOWARP_BIN, "devices"], timeout=8)
                data = parse_devices(output)
                data["ok"] = code == 0
                self.send_json(data, 200 if code == 0 else 500)
            except subprocess.TimeoutExpired:
                self.send_json({"ok": False, "error": "devices timeout"}, 504)
            return
        if path == "/api/discover":
            query = parse_qs(parsed.query)
            timeout = float(query.get("timeout", ["3"])[0] or 3)
            port = int(query.get("port", [str(self.server.controller.config.get("port", 4415))])[0])
            portscan = query.get("portscan", ["0"])[0] in {"1", "true", "yes"}
            found = mdns_discover(timeout=timeout)
            if portscan:
                existing = {(item.get("address") or item.get("host"), item.get("port")) for item in found}
                for item in scan_open_ports(port):
                    key = (item.get("address") or item.get("host"), item.get("port"))
                    if key not in existing:
                        found.append(item)
            self.send_json({"ok": True, "servers": found})
            return
        if path == "/api/logs":
            self.send_json(
                {
                    "runtime": tail_file(RUNTIME_LOG),
                    "client": tail_file(ECHOWARP_CLIENT_LOG),
                }
            )
            return
        self.serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/login", "/api/login"}:
            self.handle_login(path, parsed)
            return
        if path == "/api/logout":
            token = self.session_token()
            self.server.delete_session(token)
            if path.startswith("/api/"):
                self.send_json({"ok": True}, headers=[self.expire_auth_cookie()])
            else:
                self.send_redirect("/login", headers=[self.expire_auth_cookie()])
            return
        if not self.ensure_authenticated(path):
            return
        if path in {"/api/recordings/start", "/api/recordings/stop", "/api/recordings/settings"}:
            try:
                controller = self.server.controller
                if path.endswith("/start"):
                    controller.start_recording()
                elif path.endswith("/stop"):
                    controller.stop_recording()
                else:
                    payload = self.read_json(default={})
                    controller.update_config({"recording_retention_hours": payload.get("retention_hours")}, restart=False)
                    controller.cleanup_recordings()
                self.send_json({"ok": True, **controller.recording_status()})
            except (ValueError, TypeError, OSError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/history/save":
            try:
                payload = self.read_json(default={})
                result = self.server.controller.save_history(payload.get("minutes", 30))
                self.send_json({"ok": True, **result})
            except (ValueError, TypeError, OSError, subprocess.TimeoutExpired) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/config":
            payload = self.read_json()
            config = self.server.controller.update_config(payload, restart=True)
            self.send_json({"ok": True, "config": config})
            return
        if path == "/api/servers":
            payload = self.read_json(default={})
            try:
                servers = self.server.controller.save_server(payload)
                self.send_json({"ok": True, "servers": servers})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/servers/connect":
            payload = self.read_json(default={})
            try:
                config = self.server.controller.connect_server(str(payload.get("id", "")))
                self.send_json(
                    {
                        "ok": True,
                        "config": config,
                        "servers": self.server.controller.list_servers(),
                    }
                )
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if path == "/api/servers/delete":
            payload = self.read_json(default={})
            try:
                servers = self.server.controller.delete_server(str(payload.get("id", "")))
                self.send_json({"ok": True, "servers": servers})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if path == "/api/restart":
            self.server.controller.restart_echowarp()
            self.send_json({"ok": True})
            return
        if path == "/api/start":
            self.server.controller.update_config({"enabled": True}, restart=True)
            self.send_json({"ok": True})
            return
        if path == "/api/stop":
            self.server.controller.stop_echowarp(save=True)
            self.send_json({"ok": True})
            return
        if path == "/api/doctor":
            payload = self.read_json(default={})
            config = merge_config(self.server.controller.config, payload)
            args = [ECHOWARP_BIN, "doctor", "--json", "--port", str(config.get("port", 4415))]
            if str(config.get("address", "")).strip():
                args.extend(["--address", str(config["address"]).strip()])
            if str(config.get("tls_cert", "")).strip():
                args.extend(["--tls-cert", str(config["tls_cert"]).strip()])
            try:
                code, output = run_command(args, timeout=20)
                try:
                    report = json.loads(output)
                except json.JSONDecodeError:
                    report = None
                self.send_json({"ok": code == 0, "code": code, "report": report, "raw": output})
            except subprocess.TimeoutExpired:
                self.send_json({"ok": False, "error": "doctor timeout"}, 504)
            return
        self.send_json({"ok": False, "error": "not found"}, 404)

    def is_authenticated(self):
        if not auth_enabled():
            return True
        token = self.session_token()
        if not token:
            return False
        return self.server.valid_session(token)

    def session_token(self):
        return parse_cookie_header(self.headers.get("Cookie", "")).get(AUTH_COOKIE, "")

    def ensure_authenticated(self, path):
        if self.is_authenticated():
            return True
        if path.startswith("/api/") or path.startswith("/hls/") or path == "/status.json":
            self.send_json({"ok": False, "error": "auth_required"}, 401)
            return False
        next_url = quote(self.path or "/", safe="")
        self.send_redirect(f"/login?next={next_url}")
        return False

    def check_credentials(self, username, password):
        expected_user = auth_user()
        expected_password = os.environ.get("WEB_AUTH_PASSWORD", DEFAULT_AUTH_PASSWORD)
        return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password)

    def handle_login(self, path, parsed):
        payload = self.read_json(default={}) if path == "/api/login" else self.read_form()
        username = str(payload.get("username", ""))
        password = str(payload.get("password", ""))
        next_url = safe_next_url(str(payload.get("next", parse_qs(parsed.query).get("next", ["/"])[0])))

        if auth_enabled() and not self.check_credentials(username, password):
            if path == "/api/login":
                self.send_json({"ok": False, "error": "invalid_credentials"}, 401)
            else:
                self.send_redirect(f"/login?error=1&next={quote(next_url, safe='')}")
            return

        remember = str(payload.get("remember", "")).lower() in {"1", "true", "on", "yes"}
        ttl = 30 * 24 * 60 * 60 if remember else session_ttl()
        token = self.server.create_session(ttl)
        cookie = self.auth_cookie(token, ttl)
        if path == "/api/login":
            self.send_json({"ok": True}, headers=[cookie])
        else:
            self.send_redirect(next_url, headers=[cookie])

    def auth_cookie(self, token, ttl=None):
        return (
            "Set-Cookie",
            f"{AUTH_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={ttl if ttl is not None else session_ttl()}",
        )

    def expire_auth_cookie(self):
        return (
            "Set-Cookie",
            f"{AUTH_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0",
        )

    def read_json(self, default=None):
        raw = self.read_body()
        if not raw:
            return default if default is not None else {}
        return json.loads(raw.decode("utf-8"))

    def read_form(self):
        raw = self.read_body()
        data = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in data.items()}

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return b""
        return self.rfile.read(length)

    def send_redirect(self, location, status=303, headers=None):
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        for key, value in headers or []:
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, data, status=200, headers=None):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        for key, value in headers or []:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, request_path, head_only=False):
        path = posixpath.normpath(unquote(request_path))
        if path in {"", "/"}:
            path = "/index.html"
        relative = path.lstrip("/")
        file_path = (WEB_ROOT / relative).resolve()
        root = WEB_ROOT.resolve()
        if not str(file_path).startswith(str(root)):
            self.send_error(403)
            return
        if not file_path.exists() or file_path.is_dir():
            if "." not in Path(relative).name:
                file_path = WEB_ROOT / "index.html"
            else:
                self.send_error(404)
                return
        content = file_path.read_bytes()
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if file_path.suffix == ".m3u8":
            mime = "application/vnd.apple.mpegurl"
        elif file_path.suffix == ".ts":
            mime = "video/mp2t"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        if "/hls/" in request_path:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0")
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def log_message(self, fmt, *args):
        append_log("[web] " + fmt % args)


class EchoWarpHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler, controller):
        super().__init__(address, handler)
        self.controller = controller
        SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with self.session_db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL, auth_tag TEXT NOT NULL)")
        SESSIONS_FILE.chmod(0o600)

    @contextlib.contextmanager
    def session_db(self):
        with contextlib.closing(sqlite3.connect(SESSIONS_FILE, timeout=10)) as db, db:
            yield db

    def session_auth_tag(self, token):
        credentials = auth_user() + "\0" + os.environ.get("WEB_AUTH_PASSWORD", DEFAULT_AUTH_PASSWORD)
        return hmac.new(token.encode(), credentials.encode(), hashlib.sha256).hexdigest()

    def create_session(self, ttl):
        token = secrets.token_urlsafe(32)
        with self.session_db() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                       (hashlib.sha256(token.encode()).hexdigest(), time.time() + ttl, self.session_auth_tag(token)))
        return token

    def valid_session(self, token):
        with self.session_db() as db:
            row = db.execute("SELECT expires, auth_tag FROM sessions WHERE token_hash = ?",
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return bool(row and row[0] > time.time() and hmac.compare_digest(row[1], self.session_auth_tag(token)))

    def delete_session(self, token):
        with self.session_db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))


def main():
    mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
    mimetypes.add_type("video/mp2t", ".ts")

    controller = EchoWarpController()
    controller.start()

    host = os.environ.get("WEB_HOST", "")
    port = env_int("WEB_PORT", 3008)
    httpd = EchoWarpHTTPServer((host, port), EchoWarpHandler, controller)

    def shutdown(_signum, _frame):
        controller.stop()
        httpd.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if controller.status.get("state") == "starting":
        controller.write_status(detail=f"Web listening on port {port}")
    else:
        controller.write_status()
    listen_host = host or "0.0.0.0"
    print(f"EchoWarp Web listening on {listen_host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
