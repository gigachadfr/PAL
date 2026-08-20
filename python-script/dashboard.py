"""
Local web dashboard for the Minecraft AI commentator.

Built on the standard library's http.server rather than Flask or FastAPI on purpose: the bot has
four dependencies and a status page is not worth a fifth. It serves one HTML file and a handful
of JSON endpoints, and runs on a daemon thread so it never delays the commentary loop.

It binds to 127.0.0.1. The settings endpoint reads and writes .env, which holds API keys — do not
move this off loopback without putting authentication in front of it.
"""

import json
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

PAGE_FILE = "dashboard.html"
# ElevenLabs quotas move slowly and every check is a network round trip.
QUOTA_CACHE_S = 90
QUOTA_TIMEOUT_S = 8
# Nothing is served from these unless the request came from the machine running the bot.
LOOPBACK = ("127.0.0.1", "::1", "localhost")

# Which settings the dashboard is allowed to show and change. Anything not listed here cannot be
# written through the API — the endpoint would otherwise be an arbitrary .env editor.
SETTINGS_SCHEMA = [
    {
        "group": "Commentary",
        "fields": [
            {"key": "SYSTEM_PROMPT", "label": "Personality prompt", "type": "textarea",
             "help": "The character. Output rules are appended automatically."},
            {"key": "GEMINI_MODEL", "label": "Gemini model", "type": "text"},
            {"key": "GEMINI_RPM_LIMIT", "label": "Free-tier limit, per minute", "type": "number",
             "help": "Used only to draw the gauge. Google changes these per model."},
            {"key": "GEMINI_RPD_LIMIT", "label": "Free-tier limit, per day", "type": "number"},
            {"key": "SEND_INTERVAL", "label": "Idle timer", "type": "number", "unit": "s",
             "help": "How long a quiet stretch runs before the bot speaks anyway."},
            {"key": "NOTABLE_DEBOUNCE", "label": "Notable grouping", "type": "number", "unit": "s"},
            {"key": "MAX_CHARS_PER_SEND", "label": "Characters per request", "type": "number"},
            {"key": "HISTORY_TURNS", "label": "Conversation window", "type": "number",
             "unit": "turns"},
            {"key": "AI_TOOLS", "label": "Allow lookups", "type": "bool",
             "help": "Lets the model check inventory, stats and status when it wants to."},
        ],
    },
    {
        "group": "Voice",
        "fields": [
            {"key": "TTS_BACKEND", "label": "Backend", "type": "select",
             "choices": ["auto", "chatterbox", "elevenlabs", "edge"]},
            {"key": "EDGE_VOICE", "label": "Edge voice", "type": "options"},
            {"key": "VOICE_ID", "label": "ElevenLabs voice", "type": "options"},
            {"key": "ELEVENLABS_MODEL", "label": "ElevenLabs model", "type": "text"},
        ],
    },
    {
        "group": "Chatterbox",
        "fields": [
            {"key": "CHATTERBOX_EXAGGERATION", "label": "Exaggeration", "type": "range",
             "min": 0.25, "max": 2.0, "step": 0.05,
             "help": "0.5 neutral, 1.4 theatrical. Raise this and lower cfg weight together."},
            {"key": "CHATTERBOX_CFG_WEIGHT", "label": "CFG weight", "type": "range",
             "min": 0.2, "max": 1.0, "step": 0.05,
             "help": "Lower slows the delivery back down after a high exaggeration."},
            {"key": "CHATTERBOX_TEMPERATURE", "label": "Temperature", "type": "range",
             "min": 0.0, "max": 1.5, "step": 0.05},
            {"key": "CHATTERBOX_URGENT_BOOST", "label": "Urgent boost", "type": "range",
             "min": 1.0, "max": 2.0, "step": 0.05,
             "help": "Multiplies exaggeration on CRITICAL events, capped at 2.0."},
            {"key": "CHATTERBOX_MODE", "label": "Voice mode", "type": "select",
             "choices": ["predefined", "clone"]},
            {"key": "CHATTERBOX_VOICE", "label": "Voice", "type": "options"},
            {"key": "CHATTERBOX_USE_TAGS", "label": "Performance cues", "type": "bool"},
            {"key": "CHATTERBOX_AUTOSTART", "label": "Start the server automatically",
             "type": "bool"},
            {"key": "CHATTERBOX_URL", "label": "Server URL", "type": "text"},
            {"key": "CHATTERBOX_PATH", "label": "Server folder", "type": "text"},
        ],
    },
    {
        "group": "Sources and keys",
        "fields": [
            {"key": "LOG_DIRECTORY", "label": "Minecraft log folder", "type": "path",
             "help": "Detect finds it from a running Minecraft, or from the instances on disk."},
            {"key": "CHECK_INTERVAL", "label": "Log poll", "type": "number", "unit": "s"},
            {"key": "GEMINI_API_KEY", "label": "Gemini API key", "type": "secret",
             "help": "Stored in .env. Shown masked; type a new one to replace it."},
        ],
    },
]

WRITABLE = {field["key"] for group in SETTINGS_SCHEMA for field in group["fields"]}
SECRETS = {field["key"] for group in SETTINGS_SCHEMA for field in group["fields"]
           if field["type"] == "secret"}


def mask(secret):
    if not secret:
        return ""
    return secret[:4] + "…" + secret[-4:] if len(secret) > 12 else "…" * 4


class Telemetry:
    """
    The half of the dashboard that is not already on disk: what the bot has been doing.

    Every method is called from the commentary loop, so the lock is held for as short as
    possible and nothing here does I/O.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time()
        self.run_started = None
        self.events = deque(maxlen=500)
        self.replies = deque(maxlen=80)
        self.speech = deque(maxlen=300)
        self.tool_calls = Counter()
        self.event_types = Counter()
        self.levels = Counter()
        self.gemini_calls = 0
        self.gemini_errors = 0
        self.quota_errors = 0
        # One timestamp per request sent, kept for a day so the per-minute and per-day rates
        # can both be read off the same list.
        self.requests = deque(maxlen=20000)
        self.last_error = None
        self.last_quota_error = None
        self.watching = False

    # ---- written by the bot -------------------------------------------------

    def session_started(self):
        with self.lock:
            self.run_started = time.time()
            self.watching = True

    def session_stopped(self):
        with self.lock:
            self.watching = False

    def add_events(self, events):
        now = time.time()
        with self.lock:
            for event in events:
                self.events.append({
                    "at": now,
                    "t": event.get("t", ""),
                    "level": event.get("lvl", "INFO"),
                    "type": event.get("type", ""),
                    "msg": event.get("msg", ""),
                })
                self.levels[event.get("lvl", "INFO")] += 1
                self.event_types[event.get("type", "")] += 1

    def add_reply(self, prompt, reply, seconds, urgent, tools):
        with self.lock:
            self.gemini_calls += 1
            for name in tools:
                self.tool_calls[name] += 1
            self.replies.append({
                "at": time.time(),
                "clock": datetime.now().strftime("%H:%M:%S"),
                "prompt": prompt,
                "reply": reply,
                "seconds": round(seconds, 2),
                "urgent": urgent,
                "tools": tools,
            })

    def add_request(self):
        with self.lock:
            self.requests.append(time.time())

    def add_error(self, message, quota=False):
        with self.lock:
            self.gemini_errors += 1
            self.last_error = {"at": time.time(), "message": str(message)[:400]}
            if quota:
                self.quota_errors += 1
                self.last_quota_error = time.time()

    def add_speech(self, backend, characters, seconds, ok):
        with self.lock:
            self.speech.append({
                "at": time.time(),
                "backend": backend,
                "characters": characters,
                "seconds": round(seconds, 2),
                "ok": ok,
            })

    # ---- read by the dashboard ---------------------------------------------

    def snapshot(self):
        with self.lock:
            spoken = Counter()
            for call in self.speech:
                if call["ok"]:
                    spoken[call["backend"]] += call["characters"]
            latencies = [r["seconds"] for r in self.replies]
            now = time.time()
            last_minute = sum(1 for at in self.requests if now - at < 60)
            last_day = sum(1 for at in self.requests if now - at < 86400)
            return {
                "requests_total": len(self.requests),
                "requests_last_minute": last_minute,
                "requests_last_day": last_day,
                "quota_errors": self.quota_errors,
                "seconds_since_quota_error":
                    round(now - self.last_quota_error) if self.last_quota_error else None,
                "uptime": round(time.time() - self.started),
                "watching": self.watching,
                "run_seconds": round(time.time() - self.run_started) if self.run_started else 0,
                "gemini_calls": self.gemini_calls,
                "gemini_errors": self.gemini_errors,
                "last_error": self.last_error,
                "average_latency": round(sum(latencies) / len(latencies), 2) if latencies else 0,
                "levels": dict(self.levels),
                "event_types": dict(self.event_types.most_common(12)),
                "tool_calls": dict(self.tool_calls),
                "characters_spoken": dict(spoken),
                "events": list(self.events)[-120:],
                "replies": list(self.replies)[-20:],
                "speech": list(self.speech)[-80:],
            }


class KeyQuotas:
    """
    Where each ElevenLabs key stands: characters used, characters left, and when the allowance
    resets. Read from the account itself rather than counted locally, because the count that
    matters is the one ElevenLabs is enforcing — and a key shared with another machine would
    make a local tally quietly wrong.
    """

    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.cache = []
        self.fetched = 0.0
        self.refreshing = False

    def get(self, force=False):
        """
        The cached quotas, refreshing them if they are old.

        Returns a `loading` flag as well, because an empty list means two very different things
        — "you have no keys" and "the first fetch has not come back yet" — and the page would
        otherwise show the wrong one of those for the first few seconds.
        """
        with self.lock:
            fresh = time.time() - self.fetched < QUOTA_CACHE_S
            if self.cache and fresh and not force:
                return {"keys": self.cache, "loading": False}
            if self.refreshing:
                return {"keys": self.cache, "loading": not self.cache}
            self.refreshing = True

        try:
            quotas = self._fetch()
        finally:
            with self.lock:
                self.refreshing = False
        with self.lock:
            self.cache = quotas
            self.fetched = time.time()
            return {"keys": quotas, "loading": False}

    def _fetch(self):
        """
        Asked in parallel: seven keys checked one after another would leave the card empty for
        the best part of a minute whenever an account is slow to answer.
        """
        keys = list(self.config.elevenlabs_keys.get("keys", []))
        if not keys:
            return []
        counts = self.config.elevenlabs_keys.get("usage_count", {})
        chars = self.config.elevenlabs_keys.get("character_count", {})
        active = self.config.current_elevenlabs_key()

        with ThreadPoolExecutor(max_workers=min(8, len(keys))) as pool:
            return list(pool.map(
                lambda pair: self._one(pair[0], pair[1], counts, chars, active), enumerate(keys)))

    def _one(self, index, key, counts, chars, active):
        entry = {
            "index": index + 1,
            "masked": mask(key),
            "active": key == active,
            "calls_made": counts.get(key, 0),
            # What this bot has actually sent through the key. Kept separate from the account's
            # own figure because the two have been seen disagreeing, and only one of them is
            # something we can vouch for.
            "characters_sent": chars.get(key, 0),
            "status": "unknown",
        }
        try:
            response = requests.get(
                "https://api.elevenlabs.io/v1/user/subscription",
                headers={"xi-api-key": key},
                timeout=QUOTA_TIMEOUT_S,
            )
        except requests.RequestException as e:
            entry["status"] = "unreachable"
            entry["detail"] = str(e)[:120]
            return entry

        if response.status_code == 401:
            entry["status"] = "rejected"
            entry["detail"] = "The key was refused — it may have been revoked."
            return entry
        if response.status_code != 200:
            entry["status"] = "error"
            entry["detail"] = f"HTTP {response.status_code}"
            return entry

        try:
            data = response.json()
        except ValueError:
            entry["status"] = "error"
            entry["detail"] = "The account replied with something unreadable."
            return entry

        used = data.get("character_count", 0) or 0
        limit = data.get("character_limit", 0) or 0
        reset = data.get("next_character_count_reset_unix")

        # A reset date in the past means the account has not been billed in a cycle — free
        # accounts left idle report a stale one. Showing "resets in -5991 hours" is worse than
        # admitting we do not know.
        hours = None
        if reset:
            hours = round((reset - time.time()) / 3600, 1)
            if hours < 0:
                hours = None

        sent = entry["characters_sent"]
        entry.update({
            "status": "ok",
            "tier": data.get("tier", "unknown"),
            "used": used,
            "limit": limit,
            "left": max(0, limit - used),
            "percent": round(used / limit * 100, 1) if limit else 0,
            "resets_unix": reset,
            "resets_in_hours": hours,
            "reset_stale": bool(reset) and hours is None,
            # The account says one thing, our own tally says another. Worth surfacing rather
            # than silently trusting whichever we happened to print.
            "disagrees": sent > 0 and used == 0,
        })
        return entry


class Dashboard:
    """The HTTP server and everything it needs to answer with."""

    def __init__(self, config, telemetry, state, options=None, finder=None,
                 host="127.0.0.1", port=8765):
        self.config = config
        self.telemetry = telemetry
        self.state = state
        self.options = options
        self.finder = finder
        self.host = host
        self.port = port
        self.quotas = KeyQuotas(config)
        self.server = None
        self.thread = None
        self.page = Path(__file__).with_name(PAGE_FILE)

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    def start(self):
        if not self.page.is_file():
            print(f"[DASH] {PAGE_FILE} is missing next to the script; dashboard disabled.")
            return False
        try:
            self.server = ThreadingHTTPServer((self.host, self.port), self._handler())
        except OSError as e:
            print(f"[DASH] Could not listen on {self.url}: {e}")
            return False

        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="dashboard", daemon=True)
        self.thread.start()
        print(f"[DASH] Dashboard at {self.url}")
        return True

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    # ---- what the endpoints answer with ------------------------------------

    def snapshot(self):
        player = self.state.read() if self.state else None
        return {
            "now": datetime.now().strftime("%H:%M:%S"),
            "bot": self.telemetry.snapshot(),
            "player": player,
            "mod_live": bool(player and player.get("active") and "stale_seconds" not in player),
            "config": {
                "model": self.config.get("GEMINI_MODEL"),
                "backend": self.config.get("TTS_BACKEND"),
                "tools": self.config.get("AI_TOOLS").lower() == "true",
                "log_directory": self.config.get("LOG_DIRECTORY"),
                "rpm_limit": self.config.get_int("GEMINI_RPM_LIMIT"),
                "rpd_limit": self.config.get_int("GEMINI_RPD_LIMIT"),
            },
        }

    def settings(self):
        groups = []
        for group in SETTINGS_SCHEMA:
            fields = []
            for field in group["fields"]:
                value = self.config.get(field["key"])
                item = dict(field)
                item["value"] = mask(value) if field["key"] in SECRETS else value
                item["set"] = bool(value)
                fields.append(item)
            groups.append({"group": group["group"], "fields": fields})
        return {"groups": groups}

    def update_setting(self, key, value):
        if key not in WRITABLE:
            return False, f"{key} cannot be changed from the dashboard."
        if key in SECRETS and ("…" in value or not value.strip()):
            # The form shows the masked value; submitting it unchanged must not overwrite the key.
            return False, "Nothing changed."
        self.config.set(key, value)
        return True, f"{key} updated."

    def detect_log_directory(self):
        if not self.finder:
            return {"candidates": []}
        found = self.finder.detect()
        return {"candidates": [dict(entry, description=self.finder.describe(entry))
                               for entry in found]}

    def use_log_directory(self, path):
        path = (path or "").strip()
        if not path:
            return False, "No folder given."
        self.config.set("LOG_DIRECTORY", path)
        if self.state:
            self.state.retarget(path)
        if not Path(path).is_dir():
            # Saved anyway: the mod creates the folder the first time you join a world, so a
            # path that does not exist yet is a normal thing to configure ahead of time.
            return True, "Saved. That folder does not exist yet — the mod will create it."
        return True, "Saved. The event log is picked up on the next Start."

    def voice_options(self, field):
        if not self.options:
            return {"choices": [], "value": "", "note": ""}
        return self.options.for_field(field)

    def add_voice_option(self, field, value):
        if not self.options:
            return False, "Voice lists are not available."
        added, reason = self.options.add(field, value)
        if added:
            self.config.set(field, value.strip())
        return added, reason

    def update_keys(self, action, payload):
        if action == "add":
            key = (payload.get("key") or "").strip()
            if not key:
                return False, "No key given."
            added = self.config.add_elevenlabs_key(key)
            self.quotas.get(force=True)
            return added, "Key added." if added else "That key is already in the list."
        if action == "remove":
            index = payload.get("index")
            if not isinstance(index, int):
                return False, "No key chosen."
            removed = self.config.remove_elevenlabs_key(index - 1)
            self.quotas.get(force=True)
            return removed, "Key removed." if removed else "No key at that position."
        return False, f"Unknown action {action}."

    # ---- plumbing ----------------------------------------------------------

    def _handler(self):
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass  # the bot's own output is the interesting one

            def _local_only(self):
                if self.client_address[0] in LOOPBACK:
                    return True
                self._send(403, {"error": "The dashboard only answers local requests."})
                return False

            def _send(self, code, payload, content_type="application/json"):
                body = (json.dumps(payload).encode("utf-8")
                        if content_type == "application/json" else payload)
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._local_only():
                    return
                route = self.path.split("?")[0]
                if route in ("/", "/index.html"):
                    self._send(200, dashboard.page.read_bytes(), "text/html; charset=utf-8")
                elif route == "/api/snapshot":
                    self._send(200, dashboard.snapshot())
                elif route == "/api/keys":
                    self._send(200, dashboard.quotas.get(force="force=1" in self.path))
                elif route == "/api/settings":
                    self._send(200, dashboard.settings())
                elif route == "/api/detect":
                    self._send(200, dashboard.detect_log_directory())
                elif route == "/api/options":
                    field = ""
                    if "field=" in self.path:
                        field = self.path.split("field=")[1].split("&")[0]
                    self._send(200, dashboard.voice_options(field))
                else:
                    self._send(404, {"error": "No such endpoint."})

            def do_POST(self):
                if not self._local_only():
                    return
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    self._send(400, {"error": "That was not JSON."})
                    return

                route = self.path.split("?")[0]
                if route == "/api/settings":
                    ok, message = dashboard.update_setting(
                        payload.get("key", ""), str(payload.get("value", "")))
                    self._send(200 if ok else 400, {"ok": ok, "message": message})
                elif route == "/api/keys":
                    ok, message = dashboard.update_keys(payload.get("action", ""), payload)
                    self._send(200 if ok else 400, {"ok": ok, "message": message})
                elif route == "/api/detect":
                    ok, message = dashboard.use_log_directory(payload.get("path", ""))
                    self._send(200 if ok else 400, {"ok": ok, "message": message})
                elif route == "/api/options":
                    ok, message = dashboard.add_voice_option(
                        payload.get("field", ""), str(payload.get("value", "")))
                    self._send(200 if ok else 400, {"ok": ok, "message": message})
                else:
                    self._send(404, {"error": "No such endpoint."})

        return Handler
