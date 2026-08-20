"""
Minecraft AI Commentator — reads the JSONL action log produced by the PAL mod and reacts to it
out loud via Gemini + ElevenLabs.

Design notes (why it looks like this):
  * The log is tailed by byte offset and survives being truncated when a new Minecraft session
    starts. The previous version diffed by line count, so a truncation left it permanently stuck.
  * Events carry a level (INFO / NOTABLE / CRITICAL) which drives *when* we talk: CRITICAL cuts
    the queue, NOTABLE debounces so bursts get grouped, INFO waits for the timer.
  * Audio plays on its own thread, so a 15 s clip no longer blocks log reading.
"""

import asyncio
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover - guidance for a fresh install
    raise SystemExit(
        "Missing dependency: google-genai\n"
        "Install the requirements first:  pip install -r requirements.txt"
    )

try:
    import edge_tts
except ImportError:  # optional: only needed for the free TTS backend
    edge_tts = None

# ==================== CONFIGURATION ====================
ENV_FILE = ".env"
CHAT_HISTORY_DIR = "chat_history"
API_KEYS_FILE = "elevenlabs_keys.json"

LOG_FILE_NAME = "session.log"

# Appended to the user's own system prompt. Keeps replies short enough to be worth speaking,
# and stops the model from reading out coordinates, which sound terrible as audio.
FORMAT_RULES = (
    "\n\nOUTPUT RULES (always follow, they override any style instruction):\n"
    "- Reply with 1 to 2 short spoken sentences. Never more. No lists, no markdown, no emoji.\n"
    "- You are being read aloud by a text-to-speech voice. Write how a person talks.\n"
    "- Never read out raw coordinates or numbers like Y=-54; say 'deep underground' instead.\n"
    "- React to the most interesting thing that just happened; ignore the routine noise.\n"
    "- Never describe the log itself or mention that you are reading logs."
)

# Chatterbox Turbo renders these natively — the syntax is SQUARE brackets, [laugh], as per the
# model card. Every other backend would read them out loud, so they are stripped there.
PARALINGUISTIC_TAGS = (
    "laugh", "chuckle", "sigh", "gasp", "cough", "clear throat", "sniff", "groan", "shush",
)

PARALINGUISTIC_RULES = (
    "\n- You may add ONE performance cue per reply, inline, only where it genuinely fits:\n"
    "  [laugh] [chuckle] [sigh] [gasp] [groan] [cough] [sniff] [shush]\n"
    '  Example: "[sigh] You walked into the lava. Of course you did."\n'
    "  Square brackets exactly as shown. Do not invent other tags, do not use more than one,\n"
    "  and never start every reply with one."
)

# Matches only the known tags, in either bracket style and singular or plural. Square brackets
# are the real syntax; angle brackets are matched so a model slip can be repaired rather than
# spoken. Anything else in brackets is left alone.
_TAG_ALTERNATION = "|".join(re.escape(t) + "(?:es|s)?" for t in PARALINGUISTIC_TAGS)
# Ranges enforced by the server (models.py), which took them from Chatterbox's Gradio app.
# Sending values outside them is untested territory and makes synthesis unreliable.
EXAGGERATION_MIN, EXAGGERATION_MAX = 0.25, 2.0
CFG_WEIGHT_MIN, CFG_WEIGHT_MAX = 0.2, 1.0
EXAGGERATION_CEILING = EXAGGERATION_MAX

TAG_PATTERN = re.compile(rf"[\[<]\s*(?:{_TAG_ALTERNATION})\s*[\]>]", re.IGNORECASE)
PAREN_TAG_PATTERN = re.compile(rf"\(\s*(?:{_TAG_ALTERNATION})\s*\)", re.IGNORECASE)

DEFAULT_SYSTEM_PROMPT = (
    "You are watching a friend play Minecraft over their shoulder and commenting live. "
    "Personality: a tsundere who acts unimpressed but is secretly invested — tease them when "
    "they do something stupid, and grudgingly admit it when they pull something off."
)

DEFAULTS = {
    "GEMINI_API_KEY": "",
    "VOICE_ID": "",
    "LOG_DIRECTORY": "",
    # Flash Lite has the friendlier free-tier rate limits, which matters when a busy session
    # fires several CRITICAL events in a row.
    "GEMINI_MODEL": "gemini-3.5-flash-lite",
    "ELEVENLABS_MODEL": "eleven_turbo_v2_5",
    "SYSTEM_PROMPT": DEFAULT_SYSTEM_PROMPT,
    "CHECK_INTERVAL": "0.5",
    "SEND_INTERVAL": "45",
    "NOTABLE_DEBOUNCE": "3",
    "MAX_CHARS_PER_SEND": "2000",
    "HISTORY_TURNS": "12",
    # Backend order in "auto": local Chatterbox if its server answers, then ElevenLabs, then Edge.
    # Force one with "chatterbox" / "elevenlabs" / "edge".
    "TTS_BACKEND": "auto",
    "EDGE_VOICE": "en-US-AriaNeural",
    # --- local Chatterbox server (github.com/devnen/Chatterbox-TTS-Server) ---
    "CHATTERBOX_URL": "http://localhost:8004",
    "CHATTERBOX_MODE": "predefined",  # "predefined" or "clone"
    "CHATTERBOX_VOICE": "",  # predefined voice id, or reference audio filename when cloning
    # Tuned for a loud, theatrical commentator rather than a narrator. 1.4 sits well into
    # the dramatic end, and the urgent boost takes critical events to ~1.96, just under
    # the 2.0 ceiling — so a death is delivered at close to maximum intensity.
    "CHATTERBOX_EXAGGERATION": "1.4",  # 0.25-2.0
    "CHATTERBOX_TEMPERATURE": "0.85",  # 0.0-1.5; a little variation keeps it lively
    # 0.2-1.0, and deliberately low: a high exaggeration rushes the delivery, and dropping
    # cfg_weight is what buys the pacing back. 0.3 is the value the docs name for expressive.
    "CHATTERBOX_CFG_WEIGHT": "0.3",
    # Multiplier applied to exaggeration on CRITICAL events (death, creeper, low health).
    "CHATTERBOX_URGENT_BOOST": "1.4",
    # Start the server automatically when it is not already running.
    "CHATTERBOX_AUTOSTART": "false",
    "CHATTERBOX_PATH": "",  # folder holding server.py (the cloned Chatterbox-TTS-Server)
    "CHATTERBOX_START_TIMEOUT": "180",
    # Confirmed working by ear on chatterbox-tts 0.1.6 with SQUARE brackets, [laugh].
    # Audio length is not a reliable test here — a performed sigh lasts about as long as the
    # spoken word "sigh". When false, tags are stripped on every backend.
    "CHATTERBOX_USE_TAGS": "true",
}

# ElevenLabs "Default" voices: unlike Voice Library voices, these work on the free plan via the
# API. Note ElevenLabs has announced the current default set expires on 2026-12-31.
ELEVENLABS_DEFAULT_VOICES = {
    "Rachel": "21m00Tcm4TlvDq8ikWAM",
    "Domi": "AZnzlk1XvdvUeBnXmlld",
    "Bella": "EXAVITQu4vr4xnSDxMaL",
    "Antoni": "ErXwobaYiN019PkySvjV",
    "Elli": "MF3mGyEYCl7XYWbV9V6O",
    "Josh": "TxGEqnHWrfWFTfGW9XjX",
}


class Config:
    """Settings in .env, ElevenLabs keys in their own JSON file."""

    def __init__(self):
        self.load_env()
        self.elevenlabs_keys = self.load_elevenlabs_keys()
        self.current_key_index = 0

    def load_env(self):
        if not os.path.exists(ENV_FILE):
            with open(ENV_FILE, "w", encoding="utf-8") as f:
                for key, value in DEFAULTS.items():
                    f.write(f"{key}={value}\n")
        load_dotenv(ENV_FILE)
        self._add_missing_settings()

    def _add_missing_settings(self):
        """
        Fills in settings introduced by a newer version of the script, leaving everything the
        user already set completely alone — including an empty value, which is a deliberate
        'not configured yet' rather than a missing key.
        """
        added = []
        for key, value in DEFAULTS.items():
            if os.getenv(key) is None:
                set_key(ENV_FILE, key, value)
                os.environ[key] = value
                added.append(key)
        if added:
            print(f"[MIGRATE] New settings added to .env: {', '.join(added)}")

    def load_elevenlabs_keys(self):
        if not os.path.exists(API_KEYS_FILE):
            empty = {"keys": [], "usage_count": {}}
            with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
                json.dump(empty, f, indent=2)
            return empty
        try:
            with open(API_KEYS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[WARN] Could not read {API_KEYS_FILE} ({e}); starting empty.")
            return {"keys": [], "usage_count": {}}

    def save_elevenlabs_keys(self):
        with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.elevenlabs_keys, f, indent=2)

    def add_elevenlabs_key(self, api_key):
        if api_key in self.elevenlabs_keys["keys"]:
            print("[INFO] This API key already exists.")
            return False
        self.elevenlabs_keys["keys"].append(api_key)
        self.elevenlabs_keys["usage_count"][api_key] = 0
        self.save_elevenlabs_keys()
        print("[OK] API key added.")
        return True

    def remove_elevenlabs_key(self, index):
        if not 0 <= index < len(self.elevenlabs_keys["keys"]):
            return False
        key = self.elevenlabs_keys["keys"].pop(index)
        self.elevenlabs_keys["usage_count"].pop(key, None)
        self.current_key_index = 0
        self.save_elevenlabs_keys()
        print("[OK] API key removed.")
        return True

    def current_elevenlabs_key(self):
        keys = self.elevenlabs_keys["keys"]
        if not keys:
            return None
        return keys[self.current_key_index % len(keys)]

    def switch_to_next_key(self):
        if len(self.elevenlabs_keys["keys"]) <= 1:
            return False
        self.current_key_index = (self.current_key_index + 1) % len(self.elevenlabs_keys["keys"])
        print(f"[SWITCH] Now using ElevenLabs key #{self.current_key_index + 1}")
        return True

    def increment_usage(self, api_key):
        counts = self.elevenlabs_keys["usage_count"]
        counts[api_key] = counts.get(api_key, 0) + 1
        self.save_elevenlabs_keys()

    def get(self, key, default=""):
        value = os.getenv(key, "")
        return value if value != "" else (DEFAULTS.get(key, default) if default == "" else default)

    def get_float(self, key):
        try:
            return float(self.get(key))
        except (TypeError, ValueError):
            return float(DEFAULTS[key])

    def get_int(self, key):
        try:
            return int(float(self.get(key)))
        except (TypeError, ValueError):
            return int(DEFAULTS[key])

    def set(self, key, value):
        set_key(ENV_FILE, key, value)
        os.environ[key] = value

    def is_configured(self):
        required = ["GEMINI_API_KEY", "VOICE_ID", "LOG_DIRECTORY"]
        return all(self.get(k) for k in required) and bool(self.elevenlabs_keys["keys"])


# ==================== LOG TAILING ====================
class LogTailer:
    """
    Incremental JSONL reader.

    Tracks byte offset and file identity so it keeps working when Minecraft restarts and the mod
    truncates the log — the failure mode that used to wedge this bot permanently.
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = None
        self.offset = 0
        self.inode = None
        self.partial = ""

    def find_log(self):
        """Prefers the mod's session.log, falls back to any .log in the directory."""
        if not self.directory.is_dir():
            return None
        preferred = self.directory / LOG_FILE_NAME
        if preferred.is_file():
            return preferred
        logs = sorted(self.directory.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        return logs[0] if logs else None

    def wait_for_log(self):
        print(f"[WAIT] Looking for a log file in {self.directory}")
        while True:
            found = self.find_log()
            if found:
                self.attach(found)
                return found
            time.sleep(1)

    def attach(self, path, from_start=False):
        self.path = Path(path)
        stat = self.path.stat()
        self.inode = stat.st_ino
        # Start at the end: we care about what happens from now on, not the whole backlog.
        self.offset = 0 if from_start else stat.st_size
        self.partial = ""
        print(f"[LOG] Following {self.path}")

    def poll(self):
        """Returns the list of event dicts appended since the last call."""
        if self.path is None or not self.path.exists():
            return []

        try:
            stat = self.path.stat()
        except OSError:
            return []

        # New session: the mod truncated the file, or it was replaced entirely.
        if stat.st_ino != self.inode or stat.st_size < self.offset:
            print("[LOG] File was reset — a new Minecraft session started.")
            self.inode = stat.st_ino
            self.offset = 0
            self.partial = ""

        if stat.st_size == self.offset:
            return []

        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self.offset)
                chunk = f.read()
                self.offset = f.tell()
        except OSError as e:
            print(f"[ERROR] Could not read the log: {e}")
            return []

        chunk = self.partial + chunk
        lines = chunk.split("\n")
        # A trailing fragment means the mod was mid-write; hold it until the rest arrives.
        self.partial = lines.pop()

        events = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a torn or non-JSON line rather than dying
        return events


# ==================== AUDIO ====================
class AudioPlayer:
    """
    Plays clips on a worker thread so the log keeps being read while the AI talks.
    A critical clip clears anything queued and interrupts what is playing.
    """

    def __init__(self):
        self.queue = queue.Queue()
        self.process = None
        self.lock = threading.Lock()
        self.command = self._detect_player()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    @staticmethod
    def _detect_player():
        for name in ("ffplay", "mpv", "mpg123", "afplay"):
            path = shutil.which(name)
            if not path:
                continue
            if name == "ffplay":
                return [path, "-nodisp", "-autoexit", "-loglevel", "quiet"]
            if name == "mpv":
                return [path, "--no-video", "--really-quiet"]
            return [path]
        return None

    def available(self):
        return self.command is not None

    def play(self, mp3_bytes, interrupt=False):
        if interrupt:
            self._drain()
            self._kill_current()
        self.queue.put(mp3_bytes)

    def _drain(self):
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                return

    def _kill_current(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()

    def _worker(self):
        while True:
            data = self.queue.get()
            try:
                self._play_blocking(data)
            except Exception as e:
                print(f"[ERROR] Audio playback failed: {e}")
            finally:
                self.queue.task_done()

    def _play_blocking(self, data):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(data)
            path = tmp.name

        try:
            if self.command:
                with self.lock:
                    self.process = subprocess.Popen(
                        self.command + [path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                self.process.wait()
                with self.lock:
                    self.process = None
            elif platform.system() == "Windows":
                os.startfile(path)  # noqa: S606 - no interruptible player available
                time.sleep(6)
            else:
                print("[WARN] No audio player found. Install ffplay, mpv or mpg123.")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


def _clamp(value, low, high):
    return max(low, min(high, value))


def _describe_free_vram():
    """Best-effort VRAM reading, so a failed load says why rather than just timing out."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            free, total = (v.strip() for v in out.stdout.strip().splitlines()[0].split(","))
            return f"Right now: {free} free of {total}."
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return "Could not read the GPU's free memory."


# ==================== CHAT HISTORY ====================
class ChatHistory:
    """
    Saves and restores the conversation so a session can pick up where the last one stopped.

    Stored as plain {role, text} pairs rather than SDK objects, so a saved chat stays readable
    and survives a change of client library.
    """

    @staticmethod
    def _dir():
        path = Path(CHAT_HISTORY_DIR)
        path.mkdir(exist_ok=True)
        return path

    @staticmethod
    def save(history, name=None):
        if not history:
            return None

        plain = []
        for message in history:
            text = " ".join(part.text for part in message.parts if getattr(part, "text", None))
            if text:
                plain.append({"role": message.role, "text": text})
        if not plain:
            return None

        name = name or datetime.now().strftime("%Y%m%d_%H%M%S")
        path = ChatHistory._dir() / f"{name}.json"
        path.write_text(json.dumps(plain, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[SAVE] Conversation saved to {path} ({len(plain)} messages)")
        return path

    @staticmethod
    def list_saved():
        return sorted(ChatHistory._dir().glob("*.json"), key=lambda p: p.stat().st_mtime,
                      reverse=True)

    @staticmethod
    def load(path):
        try:
            plain = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[ERROR] Could not read {path}: {e}")
            return []

        history = []
        for message in plain:
            text = message.get("text")
            if not text:
                continue
            history.append(genai_types.Content(
                role=message.get("role", "user"), parts=[genai_types.Part(text=text)]))
        print(f"[LOAD] Restored {len(history)} messages from {Path(path).name}")
        return history

    @staticmethod
    def choose():
        """Offers the saved conversations. Returns the history to start from, possibly empty."""
        saved = ChatHistory.list_saved()
        if not saved:
            return []

        print(f"\n{len(saved)} saved conversation(s):")
        print("  0. Start fresh")
        for i, path in enumerate(saved[:10], 1):
            age = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            try:
                count = len(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                count = 0
            print(f"  {i}. {path.stem}  ({age}, {count} messages)")

        choice = input("\nLoad which? [0] ").strip()
        if choice.isdigit() and 1 <= int(choice) <= min(10, len(saved)):
            return ChatHistory.load(saved[int(choice) - 1])
        return []


# ==================== CHATTERBOX LAUNCHER ====================
class ChatterboxLauncher:
    """
    Starts the local Chatterbox server on demand.

    It runs `server.py` directly rather than `start.sh`, because the shell script prompts for
    hardware selection and would block forever unattended. If the server was already running
    when we looked, we leave it alone on exit — we only stop what we started.
    """

    def __init__(self, config):
        self.config = config
        self.process = None

    def enabled(self):
        return self.config.get("CHATTERBOX_AUTOSTART").lower() in ("1", "true", "yes", "on")

    def _find_python(self, folder):
        """Prefers the server's own virtualenv — it holds torch and the model deps."""
        for candidate in (".venv/bin/python", "venv/bin/python",
                          ".venv/Scripts/python.exe", "venv/Scripts/python.exe"):
            path = folder / candidate
            if path.is_file():
                return str(path)
        return sys.executable

    def start(self):
        """Launches the server and waits for it to answer. Returns True once it is up."""
        folder = self.config.get("CHATTERBOX_PATH")
        if not folder:
            print("[CHATTERBOX] Autostart is on but CHATTERBOX_PATH is not set.")
            return False

        folder = Path(folder).expanduser()
        server_py = folder / "server.py"
        if not server_py.is_file():
            print(f"[CHATTERBOX] No server.py in {folder}")
            return False

        # A server whose model failed to load still answers the port and still holds its VRAM.
        # Starting a second one just fails on "address already in use", so say so instead.
        if self._port_in_use():
            print("[CHATTERBOX] Something is already listening on that port, but its model is")
            print("[CHATTERBOX] not loaded — usually it ran out of VRAM when it started.")
            print("[CHATTERBOX] Stop it and try again:  pkill -f Chatterbox-TTS-Server")
            return False

        python = self._find_python(folder)
        print(f"[CHATTERBOX] Starting server from {folder} ...")
        print("[CHATTERBOX] First run loads the model onto the GPU, this can take a minute.")

        # Keep the output: without it a failed start gives nothing to diagnose.
        self._log_path = Path(tempfile.gettempdir()) / "chatterbox-server.log"
        try:
            self._log_handle = open(self._log_path, "w", encoding="utf-8")
            self.process = subprocess.Popen(
                [python, "server.py"],
                cwd=str(folder),
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
            )
        except OSError as e:
            print(f"[CHATTERBOX] Could not start the server: {e}")
            return False

        return self._wait_until_ready()

    def _port_in_use(self):
        url = self.config.get("CHATTERBOX_URL").rstrip("/")
        try:
            requests.get(f"{url}/api/model-info", timeout=3)
            return True
        except requests.RequestException:
            return False

    def _report_log_tail(self, lines=6):
        """Surfaces the reason the server gave up — CUDA OOM, a port clash, a bad venv."""
        path = getattr(self, "_log_path", None)
        if not path or not path.exists():
            return
        tail = [l.rstrip() for l in path.read_text(errors="replace").splitlines() if l.strip()]
        interesting = [l for l in tail if any(
            w in l for w in ("Error", "error", "CRITICAL", "Traceback", "OutOfMemory", "in use"))]
        for line in (interesting or tail)[-lines:]:
            print(f"[CHATTERBOX]   {line[:160]}")

    def _wait_until_ready(self):
        base = self.config.get("CHATTERBOX_URL").rstrip("/")
        try:
            timeout = float(self.config.get("CHATTERBOX_START_TIMEOUT"))
        except ValueError:
            timeout = 180.0

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                print("[CHATTERBOX] The server exited while starting up:")
                self._report_log_tail()
                print(f"[CHATTERBOX] Full log: {getattr(self, '_log_path', '(none)')}")
                self.process = None
                return False
            try:
                response = requests.get(f"{base}/api/model-info", timeout=2)
                if response.status_code == 200 and response.json().get("loaded"):
                    print("[CHATTERBOX] Server is ready.")
                    return True
            except (requests.RequestException, ValueError):
                pass
            time.sleep(2)

        print(f"[CHATTERBOX] The model did not finish loading within {timeout:.0f}s.")
        print("[CHATTERBOX] The usual cause is VRAM: the model needs ~4.9GB, and Minecraft")
        print("[CHATTERBOX] with a browser open can leave less than that free.")
        print(f"[CHATTERBOX] {_describe_free_vram()}")
        print("[CHATTERBOX] Start this bot BEFORE Minecraft so the model claims its memory")
        print("[CHATTERBOX] first, or set TTS_BACKEND=edge, which uses no VRAM at all.")
        return False

    def stop(self):
        if not self.process:
            return
        print("[CHATTERBOX] Stopping the server we started.")
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None
        handle = getattr(self, "_log_handle", None)
        if handle:
            handle.close()
            self._log_handle = None


# ==================== TEXT TO SPEECH ====================
class TTS:
    """
    Speech synthesis with a free fallback.

    ElevenLabs sounds better but its free plan is both tiny (10k characters/month) and now
    refuses Voice Library voices over the API with a 402. Edge TTS needs no key and has no
    practical cap, so in "auto" mode it takes over the moment ElevenLabs refuses — the
    commentary keeps talking instead of going silent.
    """

    def __init__(self, config):
        self.config = config
        self.backend = config.get("TTS_BACKEND").lower()
        # Set after an error that will not fix itself, so we stop paying the round-trip each time.
        self.elevenlabs_blocked = False
        self._chatterbox_up = None  # None = not probed yet

    def supports_tags(self):
        """
        Whether performance cues should be offered to the model at all.

        Gated behind CHATTERBOX_USE_TAGS because the feature is currently broken upstream —
        see the note on that setting.
        """
        if self.config.get("CHATTERBOX_USE_TAGS").lower() not in ("1", "true", "yes", "on"):
            return False
        return self.backend in ("auto", "chatterbox") and self._chatterbox_available()

    @staticmethod
    def _strip_tags(text):
        return TAG_PATTERN.sub("", text).replace("  ", " ").strip()

    @staticmethod
    def _normalize_tags(text):
        """
        Rewrites cues to the square-bracket form Chatterbox expects.

        The model only performs [laugh]; anything else is read out loud. Since the LLM
        occasionally reaches for <laugh> or (laughs), those are repaired rather than spoken.
        """
        def repair(match):
            inner = match.group(0).strip("[]<>()").strip().lower()
            # Depluralise only when that yields a real tag — "shush" must stay "shush".
            if inner not in PARALINGUISTIC_TAGS:
                for suffix in ("es", "s"):
                    if inner.endswith(suffix) and inner[: -len(suffix)] in PARALINGUISTIC_TAGS:
                        inner = inner[: -len(suffix)]
                        break
            return f"[{inner}]"

        normalized = TAG_PATTERN.sub(repair, text)
        return PAREN_TAG_PATTERN.sub(repair, normalized)

    def synthesize(self, text, urgent=False):
        if self.backend in ("auto", "chatterbox") and self._chatterbox_available():
            audio = self._chatterbox(text, urgent)
            if audio:
                return audio
            print("[TTS] Chatterbox failed, falling back to Edge.")

        if self.backend in ("auto", "elevenlabs") and not self.elevenlabs_blocked:
            audio = self._elevenlabs(text)
            if audio:
                return audio
            print("[TTS] ElevenLabs failed, falling back to Edge.")

        # Edge is the last resort whatever the chosen backend: a single failed request should
        # cost a different voice, not silence. Previously an explicit backend returned None
        # here and the commentary just went quiet.
        return self._edge(text)

    # ---- Chatterbox (local, GPU, emotional) ----
    def _chatterbox_available(self):
        """Probed once per run so a stopped server costs one timeout, not one per line."""
        if self._chatterbox_up is not None:
            return self._chatterbox_up

        base = self.config.get("CHATTERBOX_URL").rstrip("/")
        try:
            response = requests.get(f"{base}/api/model-info", timeout=3)
            # The server answers 200 as soon as it binds the port, while the model is still
            # loading onto the GPU — synthesis then fails with 503. Only "loaded" means ready.
            self._chatterbox_up = (
                response.status_code == 200 and bool(response.json().get("loaded"))
            )
        except (requests.RequestException, ValueError):
            self._chatterbox_up = False

        if self._chatterbox_up:
            print(f"[TTS] Local Chatterbox server found at {base}.")
        elif self.backend == "chatterbox":
            print(f"[TTS] No Chatterbox server at {base} — start it, or set TTS_BACKEND=edge.")
        return self._chatterbox_up

    def _chatterbox(self, text, urgent=False):
        base = self.config.get("CHATTERBOX_URL").rstrip("/")

        # Cues reach the model only in the exact form it performs; otherwise they are removed,
        # because anything it does not recognise gets read out loud.
        text = self._normalize_tags(text) if self.supports_tags() else self._strip_tags(text)
        if not text:
            return None

        try:
            exaggeration = float(self.config.get("CHATTERBOX_EXAGGERATION"))
            boost = float(self.config.get("CHATTERBOX_URGENT_BOOST"))
            cfg_weight = float(self.config.get("CHATTERBOX_CFG_WEIGHT"))
        except ValueError:
            exaggeration, boost, cfg_weight = 0.8, 1.4, 0.4

        # Out-of-range values are not rejected by the server, they just make it behave
        # unpredictably — so clamp rather than pass them through.
        exaggeration = _clamp(exaggeration, EXAGGERATION_MIN, EXAGGERATION_MAX)
        cfg_weight = _clamp(cfg_weight, CFG_WEIGHT_MIN, CFG_WEIGHT_MAX)

        # A death or a lit creeper should not be read in the same tone as "mined 60 stone".
        # The ceiling must never drag the boosted value below the normal one: with a normal
        # level of 4.0 an old hard cap of 2.0 made critical events *less* expressive.
        if urgent:
            exaggeration = max(exaggeration, min(EXAGGERATION_CEILING, exaggeration * boost))

        payload = {
            "text": text,
            "voice_mode": self.config.get("CHATTERBOX_MODE"),
            "output_format": "mp3",
            "exaggeration": exaggeration,
            "temperature": float(self.config.get("CHATTERBOX_TEMPERATURE")),
            "cfg_weight": cfg_weight,
            "speed_factor": 1.0,
            "language": "en",
            "stream": False,
            "split_text": True,
        }

        voice = self.config.get("CHATTERBOX_VOICE")
        if voice:
            key = ("reference_audio_filename"
                   if payload["voice_mode"] == "clone" else "predefined_voice_id")
            payload[key] = voice

        try:
            response = requests.post(f"{base}/tts", json=payload, timeout=60)
        except requests.RequestException as e:
            print(f"[ERROR] Chatterbox request failed: {e}")
            self._chatterbox_up = False  # server went away mid-session
            return None

        if response.status_code == 200:
            return response.content

        print(f"[ERROR] Chatterbox {response.status_code}: {response.text[:200]}")
        return None

    # ---- Edge (free, no key) ----
    def _edge(self, text):
        if edge_tts is None:
            print("[ERROR] edge-tts is not installed. Run: pip install edge-tts")
            return None

        text = self._strip_tags(text)  # Edge would pronounce "<sigh>" literally
        if not text:
            return None

        voice = self.config.get("EDGE_VOICE")

        async def run():
            communicate = edge_tts.Communicate(text, voice)
            buffer = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buffer.extend(chunk["data"])
            return bytes(buffer)

        try:
            return asyncio.run(run())
        except Exception as e:
            print(f"[ERROR] Edge TTS failed: {e}")
            return None

    # ---- ElevenLabs ----
    def _elevenlabs(self, text, attempt=0):
        text = self._strip_tags(text)  # ElevenLabs would pronounce "<sigh>" literally
        keys = self.config.elevenlabs_keys["keys"]
        if attempt >= max(1, len(keys)):
            print("[ERROR] Every ElevenLabs key failed.")
            return None

        api_key = self.config.current_elevenlabs_key()
        if not api_key:
            return None

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.config.get('VOICE_ID')}"
        try:
            response = requests.post(
                url,
                json={
                    "text": text,
                    "model_id": self.config.get("ELEVENLABS_MODEL"),
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
                },
                headers={
                    "Accept": "audio/mpeg",
                    "Content-Type": "application/json",
                    "xi-api-key": api_key,
                },
                timeout=30,  # never let a hung request stall the whole bot
            )
        except requests.RequestException as e:
            print(f"[ERROR] ElevenLabs request failed: {e}")
            if self.config.switch_to_next_key():
                return self._elevenlabs(text, attempt + 1)
            return None

        if response.status_code == 200:
            self.config.increment_usage(api_key)
            return response.content

        detail = response.text[:200]
        print(f"[ERROR] ElevenLabs {response.status_code}: {detail}")

        # 402 = the voice itself is off-limits on this plan. Rotating keys cannot help, and
        # every future call would fail the same way.
        if response.status_code == 402:
            self.elevenlabs_blocked = True
            print(
                "[TTS] This voice needs a paid plan (Voice Library voices are blocked on the\n"
                "      free API tier). Either pick a Default voice — menu 'ElevenLabs voices' —\n"
                "      or set TTS_BACKEND=edge to use the free engine."
            )
            return None

        # 401 = bad key, 429 = quota. Both are worth rotating away from.
        if response.status_code in (401, 429) and self.config.switch_to_next_key():
            return self._elevenlabs(text, attempt + 1)
        return None

    # ---- Chatterbox voice management ----
    def _chatterbox_get(self, path):
        base = self.config.get("CHATTERBOX_URL").rstrip("/")
        try:
            response = requests.get(f"{base}{path}", timeout=10)
        except requests.RequestException as e:
            print(f"[ERROR] Chatterbox unreachable: {e}")
            return None
        if response.status_code != 200:
            print(f"[ERROR] Chatterbox {response.status_code} on {path}")
            return None
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _names_from(payload):
        """
        Normalises the various shapes these endpoints can return: a bare list of strings, a list
        of dicts, or a dict wrapping one of those.
        """
        if payload is None:
            return []
        if isinstance(payload, dict):
            for key in ("voices", "files", "reference_files", "data"):
                if key in payload:
                    payload = payload[key]
                    break
            else:
                return []
        if not isinstance(payload, list):
            return []

        names = []
        for item in payload:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("filename", "name", "display_name", "voice_id", "id"):
                    if item.get(key):
                        names.append(str(item[key]))
                        break
        return names

    def list_predefined_voices(self):
        return self._names_from(self._chatterbox_get("/get_predefined_voices"))

    def list_reference_files(self):
        return self._names_from(self._chatterbox_get("/get_reference_files"))

    def upload_reference(self, file_path):
        """
        Uploads a .wav/.mp3 to the server's reference_audio/ folder.

        The multipart field name is not documented, so both common spellings are tried before
        giving up and telling the user to drop the file in manually.
        """
        base = self.config.get("CHATTERBOX_URL").rstrip("/")
        path = Path(file_path).expanduser()

        if not path.is_file():
            print(f"[ERROR] No such file: {path}")
            return None
        if path.suffix.lower() not in (".wav", ".mp3"):
            print(f"[ERROR] Reference audio must be .wav or .mp3 (got {path.suffix}).")
            return None

        for field in ("files", "file"):
            try:
                with open(path, "rb") as handle:
                    response = requests.post(
                        f"{base}/upload_reference",
                        files={field: (path.name, handle, "audio/mpeg")},
                        timeout=60,
                    )
            except requests.RequestException as e:
                print(f"[ERROR] Upload failed: {e}")
                return None

            if response.status_code == 200:
                print(f"[OK] Uploaded {path.name} to the server.")
                return path.name
            # 422 = FastAPI rejected the field name; worth trying the other spelling.
            if response.status_code != 422:
                print(f"[ERROR] Chatterbox {response.status_code}: {response.text[:200]}")
                return None

        print(
            "[ERROR] The server rejected the upload.\n"
            f"        Copy {path.name} into the server's reference_audio/ folder yourself,\n"
            "        then pick it from the list."
        )
        return None

    def list_elevenlabs_voices(self):
        """Lists the voices this account can actually use. Costs no characters."""
        api_key = self.config.current_elevenlabs_key()
        if not api_key:
            print("[ERROR] No ElevenLabs key configured.")
            return

        try:
            response = requests.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": api_key},
                timeout=30,
            )
        except requests.RequestException as e:
            print(f"[ERROR] Could not reach ElevenLabs: {e}")
            return

        if response.status_code != 200:
            print(f"[ERROR] ElevenLabs {response.status_code}: {response.text[:200]}")
            return

        voices = response.json().get("voices", [])
        print(f"\n{len(voices)} voice(s) on this account:\n")
        for voice in voices:
            category = voice.get("category", "?")
            flag = "  <-- free-tier safe" if category == "premade" else ""
            print(f"  {voice.get('name','?'):22} {voice.get('voice_id','?')}  [{category}]{flag}")
        print(
            "\nOnly 'premade' (Default) voices work on the free API tier."
            "\n'professional' and 'generated' voices come from the Voice Library and return 402."
        )


# ==================== AI ====================
class AIHandler:
    def __init__(self, config, player, history=None):
        self.config = config
        self.player = player
        self.tts = TTS(config)
        self.client = genai.Client(api_key=config.get("GEMINI_API_KEY"))
        self.history = list(history) if history else []
        self.max_turns = config.get_int("HISTORY_TURNS")
        self.model = config.get("GEMINI_MODEL")
        self.system_prompt = config.get("SYSTEM_PROMPT") + FORMAT_RULES
        if self.tts.supports_tags():
            self.system_prompt += PARALINGUISTIC_RULES
            print("[AI] Chatterbox is live — performance cues enabled in the prompt.")

    def comment(self, prompt_text, urgent=False):
        try:
            contents = self.history + [
                genai_types.Content(role="user", parts=[genai_types.Part(text=prompt_text)])
            ]
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    system_instruction=self.system_prompt,
                    max_output_tokens=200,
                    # We pass no tools, so the SDK's automatic function calling has nothing to
                    # do — turning it off silences its "use Chat.send_message instead" warning.
                    automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
            reply = (response.text or "").strip()
        except Exception as e:
            print(f"[ERROR] Gemini call failed: {e}")
            return

        if not reply:
            print("[WARN] Gemini returned nothing.")
            return

        print(f"[AI] {reply}")
        self._remember(prompt_text, reply)

        # `urgent` also drives the delivery, not just the timing: on Chatterbox it dials up the
        # emotional exaggeration so a death sounds like one.
        audio = self.tts.synthesize(reply, urgent=urgent)
        if audio:
            self.player.play(audio, interrupt=urgent)

    def _remember(self, prompt_text, reply):
        self.history.append(
            genai_types.Content(role="user", parts=[genai_types.Part(text=prompt_text)])
        )
        self.history.append(
            genai_types.Content(role="model", parts=[genai_types.Part(text=reply)])
        )
        # Sliding window, so a long stream does not turn into an ever-growing token bill.
        limit = self.max_turns * 2
        if len(self.history) > limit:
            self.history = self.history[-limit:]


# ==================== COMMENTATOR ====================
class Commentator:
    """Applies the send policy: CRITICAL now, NOTABLE debounced, INFO on the timer."""

    def __init__(self, config, ai):
        self.config = config
        self.ai = ai
        self.pending = []
        self.scene = None
        self.last_send = time.time()
        self.notable_deadline = None
        self.send_interval = config.get_int("SEND_INTERVAL")
        self.debounce = config.get_float("NOTABLE_DEBOUNCE")
        self.max_chars = config.get_int("MAX_CHARS_PER_SEND")

    def ingest(self, events):
        urgent = False
        for event in events:
            level = event.get("lvl", "INFO")

            if event.get("type") == "scene":
                self.scene = event.get("msg", "")
                continue
            if event.get("type") == "session_start":
                # New world: forget the old context, it is a different story now.
                self.pending.clear()
                self.scene = None

            self.pending.append(event)

            if level == "CRITICAL":
                urgent = True
            elif level == "NOTABLE" and self.notable_deadline is None:
                self.notable_deadline = time.time() + self.debounce

        if urgent:
            self.flush(urgent=True)

    def maybe_flush(self):
        now = time.time()
        if self.notable_deadline and now >= self.notable_deadline:
            self.flush()
        elif now - self.last_send >= self.send_interval:
            self.flush()

    def flush(self, urgent=False):
        self.last_send = time.time()
        self.notable_deadline = None

        if not self.pending:
            return

        events, self.pending = self.pending, []
        body = self._render(events)
        if not body:
            return

        prompt = ""
        if self.scene:
            prompt += f"[WHERE THEY ARE] {self.scene}\n"
        prompt += f"[WHAT JUST HAPPENED]\n{body}"

        print(f"\n[SEND]{' (urgent)' if urgent else ''}\n{prompt}\n")
        self.ai.comment(prompt, urgent=urgent)

    def _render(self, events):
        """Renders newest-last, dropping the least important lines if over budget."""
        lines = [f"{e.get('t', '')} [{e.get('lvl', 'INFO')}] {e.get('msg', '')}" for e in events]
        text = "\n".join(lines)
        if len(text) <= self.max_chars:
            return text

        # Over budget: keep CRITICAL and NOTABLE, then fill with the most recent INFO lines.
        ranked = [e for e in events if e.get("lvl") in ("CRITICAL", "NOTABLE")]
        info = [e for e in events if e.get("lvl") == "INFO"]
        kept = ranked[:]
        for event in reversed(info):
            candidate = kept + [event]
            rendered = "\n".join(
                f"{e.get('t', '')} [{e.get('lvl', 'INFO')}] {e.get('msg', '')}" for e in candidate
            )
            if len(rendered) > self.max_chars:
                break
            kept = candidate

        kept.sort(key=lambda e: events.index(e))
        return "\n".join(
            f"{e.get('t', '')} [{e.get('lvl', 'INFO')}] {e.get('msg', '')}" for e in kept
        )


# ==================== SETUP / MENUS ====================
class SetupWizard:
    def __init__(self, config):
        self.config = config

    def run_initial_setup(self):
        print("\n" + "=" * 60)
        print("INITIAL SETUP")
        print("=" * 60)

        if not self.config.get("GEMINI_API_KEY"):
            self.config.set("GEMINI_API_KEY", input("Gemini API key: ").strip())

        if not self.config.elevenlabs_keys["keys"]:
            print("\nYou need at least one ElevenLabs API key.")
            while True:
                key = input("ElevenLabs API key: ").strip()
                if not key:
                    break
                self.config.add_elevenlabs_key(key)
                if input("Add another? (y/n): ").strip().lower() != "y":
                    break

        if not self.config.get("VOICE_ID"):
            self.config.set("VOICE_ID", input("ElevenLabs voice ID: ").strip())

        if not self.config.get("LOG_DIRECTORY"):
            print("\nThis is the mod's log folder, usually:")
            print("  <your minecraft folder>/logs/player_actions")
            self.config.set("LOG_DIRECTORY", input("Log directory: ").strip())

        print("\n[OK] Setup complete.")

    def manage_api_keys(self):
        while True:
            print("\n" + "=" * 60)
            print("API KEYS")
            print("=" * 60)
            gemini = self.config.get("GEMINI_API_KEY")
            print(f"\nGemini: {mask(gemini) if gemini else 'not set'}")

            print("\nElevenLabs:")
            keys = self.config.elevenlabs_keys["keys"]
            if not keys:
                print("  none configured")
            for i, key in enumerate(keys):
                used = self.config.elevenlabs_keys["usage_count"].get(key, 0)
                marker = "  <- current" if i == self.config.current_key_index else ""
                print(f"  {i + 1}. {mask(key)} ({used} calls){marker}")

            print("\n1. Change Gemini key   2. Add ElevenLabs key")
            print("3. Remove ElevenLabs key   4. Back")
            choice = input("> ").strip()

            if choice == "1":
                new = input("New Gemini key: ").strip()
                if new:
                    self.config.set("GEMINI_API_KEY", new)
            elif choice == "2":
                new = input("New ElevenLabs key: ").strip()
                if new:
                    self.config.add_elevenlabs_key(new)
            elif choice == "3":
                try:
                    self.config.remove_elevenlabs_key(int(input("Number to remove: ").strip()) - 1)
                except ValueError:
                    print("[ERROR] Not a number.")
            elif choice == "4":
                return

    # ---- voice / engine selection ----
    def voice_setup(self):
        while True:
            tts = TTS(self.config)
            backend = self.config.get("TTS_BACKEND")
            online = tts._chatterbox_available()

            print("\n" + "=" * 60)
            print("VOICE SETUP")
            print("=" * 60)
            print(f"Engine        : {backend}")
            print(f"Chatterbox    : {'running' if online else 'not running'}"
                  f"  ({self.config.get('CHATTERBOX_URL')})")
            mode = self.config.get("CHATTERBOX_MODE")
            voice = self.config.get("CHATTERBOX_VOICE") or "(server default)"
            print(f"  mode        : {mode} -> {voice}")
            print(f"  emotion     : {self.config.get('CHATTERBOX_EXAGGERATION')}"
                  f" (x{self.config.get('CHATTERBOX_URGENT_BOOST')} when critical)")
            print(f"ElevenLabs    : voice {self.config.get('VOICE_ID') or 'not set'}")
            print(f"Edge          : {self.config.get('EDGE_VOICE')}")

            autostart = self.config.get("CHATTERBOX_AUTOSTART")
            print(f"  autostart   : {autostart}"
                  f"{' -> ' + self.config.get('CHATTERBOX_PATH') if autostart.lower() in ('1','true','yes','on') else ''}")

            print("\n1. Hear the current voice")
            print("2. Switch engine")
            print("3. Chatterbox - pick a built-in voice")
            print("4. Chatterbox - clone a voice from an audio file")
            print("5. Chatterbox - emotion levels")
            print("6. Chatterbox - server (autostart / start it now)")
            print("7. ElevenLabs voice")
            print("8. Edge voice")
            print("9. Back")

            choice = input("> ").strip()
            if choice == "1":
                self._preview_voice(tts)
            elif choice == "2":
                self._pick_engine()
            elif choice == "3":
                self._pick_chatterbox_predefined(tts)
            elif choice == "4":
                self._pick_chatterbox_clone(tts)
            elif choice == "5":
                self._set_emotion()
            elif choice == "6":
                self._set_autostart()
            elif choice == "7":
                self._pick_elevenlabs(tts)
            elif choice == "8":
                self._pick_edge()
            elif choice == "9":
                return

    def _preview_voice(self, tts):
        """
        Speaks two lines with the settings exactly as configured: one routine, one critical.
        Hearing them back to back is the only way to judge whether the emotion boost is doing
        anything, since the two differ only in delivery.
        """
        player = AudioPlayer()
        if not player.available() and platform.system() != "Windows":
            print("\n[ERROR] No audio player found — install ffmpeg, mpv or mpg123.")
            return

        try:
            base = float(self.config.get("CHATTERBOX_EXAGGERATION"))
            boost = float(self.config.get("CHATTERBOX_URGENT_BOOST"))
            critical = max(base, min(EXAGGERATION_CEILING, base * boost))
        except ValueError:
            base = critical = 0.0

        print(f"\nEngine : {tts.backend}")
        if tts.backend in ("auto", "chatterbox"):
            print(f"Voice  : {self.config.get('CHATTERBOX_VOICE') or '(server default)'}"
                  f"  [{self.config.get('CHATTERBOX_MODE')}]")
            print(f"Emotion: {base} normally, {critical} on critical events")
            if not tts._chatterbox_available():
                print("Note   : Chatterbox is not answering, so you will hear the fallback.")

        for label, line, urgent in (
            ("routine (INFO)", "Twenty-one whole seconds to mine some grey rocks. Riveting.", False),
            ("critical (CRITICAL)", "A creeper! Behind you! Move, you absolute walnut!", True),
        ):
            print(f"\n  [{label}] \"{line}\"")
            audio = tts.synthesize(line, urgent=urgent)
            if not audio:
                print("     -> nothing came back")
                continue
            print(f"     -> {len(audio):,} bytes")
            player.play(audio)
            player.queue.join()

        print("\nToo flat or too much? Adjust it in 'Chatterbox - emotion levels'.")

    def _set_autostart(self):
        """
        Autostart only fires from the main menu's "Start". Landing here with the server down and
        autostart already on is confusing, so this menu can also launch it on the spot.
        """
        launcher = ChatterboxLauncher(self.config)
        running = TTS(self.config)._chatterbox_available()

        print(f"\nServer: {'running' if running else 'not running'}")
        print("Autostart launches the server when you pick 'Start' from the main menu, and")
        print("stops it again when you quit. It does not start it from this menu by itself.")
        print("\n1. Turn autostart on / off")
        if not running:
            print("2. Start the server now (stays up after you leave this menu)")
        print("0. Back")

        choice = input("> ").strip()
        if choice == "2" and not running:
            if not self.config.get("CHATTERBOX_PATH"):
                print("[ERROR] Set the server path first (option 1).")
                return
            if launcher.start():
                # Deliberately not stopped on exit: it was started on request, not by a run.
                print("[OK] Server is up and will stay up.")
            return
        if choice != "1":
            return

        answer = input("Enable autostart? (y/n): ").strip().lower()
        if answer not in ("y", "n"):
            return

        if answer == "n":
            self.config.set("CHATTERBOX_AUTOSTART", "false")
            print("[OK] Autostart off.")
            return

        current = self.config.get("CHATTERBOX_PATH")
        prompt = f"Path to the Chatterbox-TTS-Server folder [{current or 'not set'}]: "
        folder = input(prompt).strip().strip("'\"") or current

        if not folder:
            print("[ERROR] A path is required.")
            return
        if not (Path(folder).expanduser() / "server.py").is_file():
            print(f"[ERROR] No server.py found in {folder}")
            return

        self.config.set("CHATTERBOX_PATH", folder)
        self.config.set("CHATTERBOX_AUTOSTART", "true")
        print("[OK] Autostart on.")

    def _pick_engine(self):
        engines = [
            ("auto", "Chatterbox if running, else ElevenLabs, else Edge"),
            ("chatterbox", "local GPU, emotional (falls back to Edge if it dies)"),
            ("elevenlabs", "cloud, best quality, tight free limits"),
            ("edge", "free, no key, no quota"),
        ]
        print()
        for i, (name, description) in enumerate(engines, 1):
            print(f"{i}. {name:12} {description}")

        choice = input("> ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(engines):
            name = engines[int(choice) - 1][0]
            self.config.set("TTS_BACKEND", name)
            print(f"[OK] Engine set to {name}.")

    def _pick_chatterbox_predefined(self, tts):
        if not tts._chatterbox_available():
            print("\n[ERROR] Chatterbox server is not running.")
            return

        voices = tts.list_predefined_voices()
        if not voices:
            print("\nNo built-in voices reported by the server.")
            return

        print()
        for i, name in enumerate(voices, 1):
            print(f"{i}. {name}")

        choice = input("\nPick a voice: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(voices):
            picked = voices[int(choice) - 1]
            self.config.set("CHATTERBOX_MODE", "predefined")
            self.config.set("CHATTERBOX_VOICE", picked)
            print(f"[OK] Voice set to {picked}.")

            # Not every bundled voice actually synthesises — some return HTTP 500 every time.
            # Better to find out here than mid-session.
            print("Checking the voice works...")
            if TTS(self.config).synthesize("Voice check.") is None:
                print(f"[WARN] {picked} failed to synthesise. That voice looks broken on the")
                print("       server; pick another one.")
            else:
                print("[OK] Voice works.")

    def _pick_chatterbox_clone(self, tts):
        if not tts._chatterbox_available():
            print("\n[ERROR] Chatterbox server is not running.")
            return

        existing = tts.list_reference_files()
        print("\nReference clips already on the server:")
        if existing:
            for i, name in enumerate(existing, 1):
                print(f"{i}. {name}")
        else:
            print("  (none yet)")
        print("\n0. Upload a new .mp3 / .wav")

        choice = input("> ").strip()

        if choice == "0":
            print("\nTip: a clean 10-30s clip of one person talking, no music, no background noise.")
            path = input("Path to the audio file: ").strip().strip("'\"")
            uploaded = tts.upload_reference(path)
            if not uploaded:
                return
            self.config.set("CHATTERBOX_MODE", "clone")
            self.config.set("CHATTERBOX_VOICE", uploaded)
            print(f"[OK] Now cloning from {uploaded}.")
            return

        if choice.isdigit() and existing and 1 <= int(choice) <= len(existing):
            name = existing[int(choice) - 1]
            self.config.set("CHATTERBOX_MODE", "clone")
            self.config.set("CHATTERBOX_VOICE", name)
            print(f"[OK] Now cloning from {name}.")

    def _set_emotion(self):
        current = self.config.get("CHATTERBOX_EXAGGERATION")
        cfg = self.config.get("CHATTERBOX_CFG_WEIGHT")
        print(f"\nExaggeration must be {EXAGGERATION_MIN}-{EXAGGERATION_MAX}: 0.5 is neutral,")
        print("0.4-0.6 conversational, above that increasingly dramatic.")
        print("Anything outside that range is clamped before it reaches the server.")
        print(f"\nTip from the Chatterbox docs: for expressive delivery, raise exaggeration to")
        print(f"0.7+ AND lower cfg_weight to about 0.3 — high exaggeration speeds the voice up,")
        print(f"and a lower cfg_weight slows it back down. cfg_weight is {cfg} "
              f"(valid {CFG_WEIGHT_MIN}-{CFG_WEIGHT_MAX}, in Settings).")

        try:
            if not EXAGGERATION_MIN <= float(current) <= EXAGGERATION_MAX:
                print(f"\n[WARN] Your current value ({current}) is out of range and is being "
                      f"clamped to {EXAGGERATION_MAX}.")
        except ValueError:
            pass
        print("\nHear the difference first with:  python test_voice.py --emotions")

        base = input(f"Normal level [{self.config.get('CHATTERBOX_EXAGGERATION')}]: ").strip()
        if base:
            try:
                self.config.set("CHATTERBOX_EXAGGERATION", str(float(base)))
            except ValueError:
                print("[ERROR] Not a number.")

        boost = input(f"Critical multiplier [{self.config.get('CHATTERBOX_URGENT_BOOST')}]: ").strip()
        if boost:
            try:
                self.config.set("CHATTERBOX_URGENT_BOOST", str(float(boost)))
            except ValueError:
                print("[ERROR] Not a number.")

    def _pick_elevenlabs(self, tts):
        tts.list_elevenlabs_voices()
        print("\nKnown free-tier Default voices:")
        names = list(ELEVENLABS_DEFAULT_VOICES)
        for i, name in enumerate(names, 1):
            print(f"{i}. {name:10} {ELEVENLABS_DEFAULT_VOICES[name]}")

        choice = input("\nPick a number, paste a voice id, or blank to skip: ").strip()
        if not choice:
            return
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            name = names[int(choice) - 1]
            self.config.set("VOICE_ID", ELEVENLABS_DEFAULT_VOICES[name])
            print(f"[OK] Voice set to {name}.")
        else:
            self.config.set("VOICE_ID", choice)
            print("[OK] Voice id saved.")

    def _pick_edge(self):
        common = [
            "en-US-AriaNeural", "en-US-JennyNeural", "en-US-GuyNeural",
            "en-GB-SoniaNeural", "fr-FR-DeniseNeural", "fr-FR-HenriNeural",
        ]
        print()
        for i, name in enumerate(common, 1):
            print(f"{i}. {name}")
        print("\nFull list:  edge-tts --list-voices")

        choice = input("\nPick a number or type a voice name: ").strip()
        if not choice:
            return
        if choice.isdigit() and 1 <= int(choice) <= len(common):
            self.config.set("EDGE_VOICE", common[int(choice) - 1])
            print(f"[OK] Edge voice set to {common[int(choice) - 1]}.")
        else:
            self.config.set("EDGE_VOICE", choice)
            print("[OK] Edge voice saved.")

    def advanced_settings(self):
        editable = [
            ("GEMINI_MODEL", "Gemini model"),
            ("TTS_BACKEND", "TTS backend (auto / chatterbox / elevenlabs / edge)"),
            ("EDGE_VOICE", "Edge voice (free backend)"),
            ("CHATTERBOX_URL", "Chatterbox server URL (local)"),
            ("CHATTERBOX_VOICE", "Chatterbox voice"),
            ("CHATTERBOX_MODE", "Chatterbox mode (predefined / clone)"),
            ("CHATTERBOX_EXAGGERATION", "Emotion intensity (0.25-2.0, 0.5 = neutral)"),
            ("CHATTERBOX_URGENT_BOOST", "Emotion multiplier on CRITICAL events"),
            ("CHATTERBOX_CFG_WEIGHT", "Pacing / voice adherence (0.2-1.0, lower = slower)"),
            ("ELEVENLABS_MODEL", "ElevenLabs model"),
            ("SYSTEM_PROMPT", "Personality prompt"),
            ("LOG_DIRECTORY", "Log directory"),
            ("VOICE_ID", "Voice ID"),
            ("SEND_INTERVAL", "Idle send interval (s)"),
            ("NOTABLE_DEBOUNCE", "Notable debounce (s)"),
            ("CHECK_INTERVAL", "Log poll interval (s)"),
            ("MAX_CHARS_PER_SEND", "Max characters per send"),
            ("HISTORY_TURNS", "Conversation turns kept"),
        ]

        while True:
            print("\n" + "=" * 60)
            print("SETTINGS")
            print("=" * 60)
            for i, (key, label) in enumerate(editable, 1):
                value = self.config.get(key)
                shown = value if len(value) <= 60 else value[:57] + "..."
                print(f"{i:2}. {label}: {shown}")
            print(f"{len(editable) + 1:2}. Back")

            choice = input("\nEdit which? ").strip()
            if not choice.isdigit():
                continue
            index = int(choice) - 1
            if index == len(editable):
                return
            if 0 <= index < len(editable):
                key, label = editable[index]
                new = input(f"{label}: ").strip()
                if new:
                    self.config.set(key, new)
                    print("[OK] Saved.")


def mask(key):
    return f"{key[:8]}...{key[-4:]}" if len(key) > 14 else "****"


# ==================== APP ====================
class App:
    def __init__(self):
        self.config = Config()
        self.wizard = SetupWizard(self.config)
        # A server we started ourselves, kept up for the whole run.
        self.launcher = None

    def run(self):
        print("=" * 60)
        print("MINECRAFT AI COMMENTATOR")
        print("=" * 60)

        if not self.config.is_configured():
            self.wizard.run_initial_setup()

        # Loading the model costs ~12s and ~5GB of VRAM, so it happens once per run rather
        # than on every Start. It stays up until you quit.
        self._ensure_server()
        try:
            self._menu_loop()
        finally:
            self._shutdown_server()

    def _ensure_server(self):
        if self.config.get("TTS_BACKEND").lower() not in ("auto", "chatterbox"):
            return
        launcher = ChatterboxLauncher(self.config)
        if not launcher.enabled():
            return
        if TTS(self.config)._chatterbox_available():
            return  # already running, someone else's to manage
        if launcher.start():
            self.launcher = launcher

    def _shutdown_server(self):
        if self.launcher:
            self.launcher.stop()
            self.launcher = None

    def _menu_loop(self):
        while True:
            print("\n1. Start   2. Voice   3. API keys   4. Settings   5. Quit")
            choice = input("> ").strip()
            if choice == "1":
                self.start()
            elif choice == "2":
                self.wizard.voice_setup()
            elif choice == "3":
                self.wizard.manage_api_keys()
            elif choice == "4":
                self.wizard.advanced_settings()
            elif choice == "5":
                print("Bye.")
                return

    def start(self):
        player = AudioPlayer()
        if not player.available() and platform.system() != "Windows":
            print("[WARN] No audio player found — install ffplay (ffmpeg), mpv or mpg123.")

        # The server's lifetime is the program's, not this run's — see _ensure_server.
        self._run(player)

    def _run(self, player):
        ai = AIHandler(self.config, player, history=ChatHistory.choose())
        tailer = LogTailer(self.config.get("LOG_DIRECTORY"))
        commentator = Commentator(self.config, ai)

        found = tailer.find_log()
        if found:
            tailer.attach(found)
        else:
            tailer.wait_for_log()

        interval = self.config.get_float("CHECK_INTERVAL")
        print("\n[START] Watching. CRITICAL events interrupt, NOTABLE groups, INFO waits.")
        print("Press Ctrl+C to stop.\n")

        try:
            while True:
                events = tailer.poll()
                if events:
                    for event in events:
                        print(f"  · {event.get('lvl','?'):8} {event.get('msg','')}")
                    commentator.ingest(events)
                commentator.maybe_flush()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[STOP] Stopped.")
        finally:
            # Save even on Ctrl+C, which is how a session normally ends.
            ChatHistory.save(ai.history)


if __name__ == "__main__":
    App().run()
