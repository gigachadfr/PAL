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
import webbrowser
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

try:
    from dashboard import Dashboard, Telemetry
except ImportError:  # dashboard.py sits next to this file; the bot works fine without it
    Dashboard = Telemetry = None

# ==================== CONFIGURATION ====================
ENV_FILE = ".env"
CHAT_HISTORY_DIR = "chat_history"
API_KEYS_FILE = "elevenlabs_keys.json"

LOG_FILE_NAME = "session.log"
# Live snapshot published by the mod next to the log, once a second.
STATE_FILE_NAME = "player_state.json"
# Stop trusting the snapshot after this long without an update: Minecraft has been closed.
STATE_MAX_AGE_S = 90
# A model that keeps asking for tools instead of answering is stuck; cut it off.
MAX_TOOL_ROUNDS = 3

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

# Only appended when the mod publishes a state file. The two "do not" rules matter: without
# them the model looks up the health it was just given, and every reply costs an extra round
# trip for nothing.
TOOL_RULES = (
    "\n\nYou can look things up when it is worth it:\n"
    "- check_inventory: what they are carrying, optionally filtered to one item\n"
    "- check_stats: deaths in this world and what killed them, kills, blocks mined, time played\n"
    "- check_player_state: armour, status effects, equipment and durability, what they are doing\n"
    "Their health, hunger and location are already given to you in every message — never call a "
    "tool to learn those. Look something up when they ask you a question about their game, or "
    "when a real number would make the line land. Most replies need no tool at all."
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
    # Lets the model call check_inventory / check_stats / check_player_state when it wants a
    # fact. Costs one extra request per lookup, so it can be switched off.
    "AI_TOOLS": "true",
    # Local web dashboard. Bound to loopback on purpose: it can read and write .env.
    "DASHBOARD": "true",
    "DASHBOARD_HOST": "127.0.0.1",
    "DASHBOARD_PORT": "8765",
    # Free-tier ceilings for the configured model. Google changes these without warning and
    # they differ per model, so they are settings rather than a table baked into the code —
    # the dashboard measures the real usage and compares it against whatever you put here.
    "GEMINI_RPM_LIMIT": "15",
    "GEMINI_RPD_LIMIT": "1000",
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
            empty = {"keys": [], "usage_count": {}, "character_count": {}}
            with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
                json.dump(empty, f, indent=2)
            return empty
        try:
            with open(API_KEYS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[WARN] Could not read {API_KEYS_FILE} ({e}); starting empty.")
            return {"keys": [], "usage_count": {}, "character_count": {}}
        # character_count arrived after usage_count; a file written by an older version has
        # the calls but not the characters.
        data.setdefault("keys", [])
        data.setdefault("usage_count", {})
        data.setdefault("character_count", {})
        return data

    def save_elevenlabs_keys(self):
        with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.elevenlabs_keys, f, indent=2)

    def add_elevenlabs_key(self, api_key):
        if api_key in self.elevenlabs_keys["keys"]:
            print("[INFO] This API key already exists.")
            return False
        self.elevenlabs_keys["keys"].append(api_key)
        self.elevenlabs_keys["usage_count"][api_key] = 0
        self.elevenlabs_keys["character_count"][api_key] = 0
        self.save_elevenlabs_keys()
        print("[OK] API key added.")
        return True

    def remove_elevenlabs_key(self, index):
        if not 0 <= index < len(self.elevenlabs_keys["keys"]):
            return False
        key = self.elevenlabs_keys["keys"].pop(index)
        self.elevenlabs_keys["usage_count"].pop(key, None)
        self.elevenlabs_keys["character_count"].pop(key, None)
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

    def increment_usage(self, api_key, characters=0):
        """
        Records what a key has actually spent.

        Characters are tracked alongside the call count because that is the unit ElevenLabs
        bills in — and because the account's own reported figure has been seen sitting at zero
        while the bot was plainly synthesising, which leaves nothing to reconcile against.
        """
        counts = self.elevenlabs_keys["usage_count"]
        counts[api_key] = counts.get(api_key, 0) + 1
        chars = self.elevenlabs_keys.setdefault("character_count", {})
        chars[api_key] = chars.get(api_key, 0) + characters
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


# ==================== FINDING MINECRAFT ====================
class MinecraftFinder:
    """
    Works out where the mod is writing, so nobody has to paste a path.

    Three signals, strongest first:

      1. **A running Minecraft.** Its own game directory, read from the process itself, which
         is the only signal that picks the right one when you keep several instances.
      2. **A folder the mod has already written to.** `logs/player_actions/session.log` existing
         is proof, and its timestamp says which instance you played last.
      3. **A folder that looks like a game directory** — it has `saves`, `mods` or `options.txt`
         — whether or not the mod has ever run there.

    The scan is bounded by depth and by a time budget, because it runs at startup and a home
    directory can be enormous. Launcher layouts are not guessed from a fixed table either: the
    default CurseForge folder is `~/curseforge`, and the machine this was written on had it in
    `~/Documents`.
    """

    MAX_DEPTH = 6
    TIME_BUDGET_S = 6.0
    SHOW_MAX = 6

    # What a Minecraft game directory has in it that other folders do not.
    GAME_DIR_MARKERS = ("saves", "mods", "versions", "options.txt")

    # Big, deep and never the answer. `assets` and `libraries` alone are tens of thousands of
    # files in every installation.
    SKIP = {
        "assets", "libraries", "saves", "resourcepacks", "shaderpacks", "texturepacks",
        "screenshots", "crash-reports", "node_modules", "__pycache__", "venv", ".git",
        ".cache", ".fabric", ".mixin.out", "steamapps", "Steam", ".steam", "Trash",
        # A backup is a copy of an instance, never the one being written to now.
        "Backups", "backups",
    }

    # Strongest wins when the same folder is reached twice: the walk meets an instance folder
    # before it meets the log directory inside it, and "used before" is the better label.
    SOURCE_RANK = {"game folder": 0, "used before": 1, "running now": 2}

    # Folder names worth treating as a launcher root wherever they turn up.
    HOME_HINTS = ("minecraft", "curseforge", "prism", "multimc", "atlauncher", "modrinth",
                  "technic", "gdlauncher", "ftb")

    # Places a launcher root is likely to sit, checked one level down.
    HOME_BASES = ("", "Documents", "Games", "Apps", ".local/share", ".var/app")

    @classmethod
    def detect(cls, budget=None):
        """Ranked candidates, best first. Never raises; returns [] when it finds nothing."""
        found = {}
        for game_dir, source in cls._running():
            cls._remember(found, game_dir, source, running=True)

        deadline = time.time() + (budget if budget is not None else cls.TIME_BUDGET_S)
        for root in cls._roots():
            if time.time() > deadline:
                break
            cls._scan(root, found, deadline)

        entries = list(found.values())
        # Running first, then whichever was written to most recently, then the rest.
        entries.sort(key=lambda e: (not e["running"], -(e["last_write"] or 0), e["path"]))
        return entries

    # ---- signal 1: a running game -----------------------------------------

    @classmethod
    def _running(cls):
        try:
            if Path("/proc").is_dir():
                return cls._running_from_proc()
            if platform.system() != "Windows":
                return cls._running_from_ps()
        except Exception as e:  # never let process inspection break startup
            print(f"[FIND] Could not inspect running processes: {e}")
        return []

    @staticmethod
    def _is_java(*names):
        return any(Path(name).name.lower().startswith(("java", "javaw")) for name in names if name)

    @classmethod
    def _looks_like_minecraft(cls, executable, args):
        """
        Strict on two counts, both of which caught something real.

        The process has to be a JVM. Without that, any process whose command line merely
        *mentions* these flags matches — a shell running a launch script, a grep, an editor.
        The first thing this rule excluded was this project's own test harness.

        And the marker has to be a Minecraft marker rather than the word "minecraft", which
        appears in this bot's own `python ai_minecraft_bot.py` and would make it find itself.
        """
        if not cls._is_java(executable, args[0] if args else ""):
            return False
        joined = " ".join(args)
        if "--assetIndex" in args or "--assetsDir" in args:
            return True
        return ("net.minecraft.client.main.Main" in joined
                or "net.fabricmc.loader.impl.launch.knot.KnotClient" in joined
                or "cpw.mods.bootstraplauncher" in joined)

    @classmethod
    def _running_from_proc(cls):
        found = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue  # gone, or another user's
            args = [part for part in raw.decode("utf-8", "replace").split("\0") if part]
            if not args:
                continue
            try:
                executable = os.readlink(entry / "exe")
            except OSError:
                executable = ""  # another user's process, or already gone
            if not cls._looks_like_minecraft(executable, args):
                continue

            game_dir = None
            if "--gameDir" in args:
                index = args.index("--gameDir")
                if index + 1 < len(args):
                    candidate = Path(args[index + 1])
                    if candidate.is_absolute() and candidate.is_dir():
                        game_dir = candidate
            if game_dir is None:
                # The working directory is the game directory under every launcher tried, and
                # it is right even when --gameDir was passed as a relative path.
                try:
                    game_dir = Path(os.readlink(entry / "cwd"))
                except OSError:
                    continue
            found.append((game_dir, "running now"))
        return found

    @classmethod
    def _running_from_ps(cls):
        """
        macOS and the BSDs have no /proc. Splitting `ps` output on spaces cannot survive a path
        with a space in it, so this only trusts --gameDir when the result exists on disk.
        """
        try:
            output = subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True,
                                    timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return []

        found = []
        for line in output.splitlines():
            args = line.split()
            if not args or not cls._looks_like_minecraft(args[0], args):
                continue
            if "--gameDir" not in args:
                continue
            index = args.index("--gameDir")
            if index + 1 >= len(args):
                continue
            candidate = Path(args[index + 1])
            if candidate.is_dir():
                found.append((candidate, "running now"))
        return found

    # ---- signals 2 and 3: what is on disk ----------------------------------

    @classmethod
    def _roots(cls):
        home = Path.home()
        roots = [home / ".minecraft", home / "curseforge", home / "Documents" / "curseforge"]

        if platform.system() == "Darwin":
            roots.append(home / "Library" / "Application Support" / "minecraft")
        elif platform.system() == "Windows":
            appdata = os.getenv("APPDATA")
            if appdata:
                roots.append(Path(appdata) / ".minecraft")

        # Anything launcher-shaped one level under the usual places, which is how a CurseForge
        # folder moved to ~/Documents gets found without walking the whole home directory.
        for base in cls.HOME_BASES:
            directory = home / base if base else home
            try:
                children = list(directory.iterdir())
            except OSError:
                continue
            for child in children:
                if not child.is_dir():
                    continue
                name = child.name.lower().lstrip(".")
                if any(hint in name for hint in cls.HOME_HINTS):
                    roots.append(child)

        seen, unique = set(), []
        for root in roots:
            resolved = str(root)
            if resolved not in seen and root.is_dir():
                seen.add(resolved)
                unique.append(root)
        return unique

    @classmethod
    def _scan(cls, root, found, deadline):
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            if time.time() > deadline:
                return
            here = Path(dirpath)

            if here.name == "player_actions" and here.parent.name == "logs":
                cls._remember(found, here.parent.parent, "used before")
                dirnames[:] = []
                continue

            if any(marker in dirnames or marker in filenames
                   for marker in cls.GAME_DIR_MARKERS):
                cls._remember(found, here, "game folder")
                # Nothing below a game directory matters except its own logs.
                dirnames[:] = [d for d in dirnames if d == "logs"]
                continue

            if len(here.parts) - base_depth >= cls.MAX_DEPTH:
                dirnames[:] = []
            else:
                dirnames[:] = [d for d in dirnames if d not in cls.SKIP]

    @staticmethod
    def _remember(found, game_dir, source, running=False):
        target = Path(game_dir) / "logs" / "player_actions"
        key = str(target)
        entry = found.get(key) or {
            "path": key,
            "game_dir": str(game_dir),
            "name": Path(game_dir).name,
            "source": source,
            "running": False,
            "exists": target.is_dir(),
            "last_write": None,
        }
        log = target / LOG_FILE_NAME
        try:
            if log.is_file():
                entry["last_write"] = log.stat().st_mtime
                entry["exists"] = True
        except OSError:
            pass
        if running:
            entry["running"] = True
        if (MinecraftFinder.SOURCE_RANK.get(source, 0)
                >= MinecraftFinder.SOURCE_RANK.get(entry["source"], 0)):
            entry["source"] = source
        found[key] = entry

    # ---- presentation -------------------------------------------------------

    @staticmethod
    def describe(entry):
        bits = [entry["source"]]
        if entry["last_write"]:
            when = datetime.fromtimestamp(entry["last_write"]).strftime("%Y-%m-%d %H:%M")
            bits.append(f"last written {when}")
        elif not entry["exists"]:
            bits.append("the mod has not run here yet")
        return ", ".join(bits)

    @classmethod
    def choose(cls, config):
        """Offers what it found and saves the pick. Returns the chosen path, or ''."""
        print("\n[FIND] Looking for Minecraft…")
        candidates = cls.detect()
        if not candidates:
            print("[FIND] Nothing found.")
            typed = input("Log directory (…/logs/player_actions): ").strip()
            if typed:
                config.set("LOG_DIRECTORY", typed)
            return typed

        shown = candidates[:cls.SHOW_MAX]
        print(f"\nFound {len(candidates)} possible folder(s), likeliest first:")
        for i, entry in enumerate(shown, 1):
            flag = " <-- running now" if entry["running"] else ""
            print(f"  {i}. {entry['name']}{flag}")
            print(f"     {entry['path']}")
            print(f"     {cls.describe(entry)}")
        if len(candidates) > len(shown):
            print(f"  … and {len(candidates) - len(shown)} more the mod has never run in.")
        print("  0. Type a path myself")

        choice = input("\nUse which? [1] ").strip() or "1"
        if choice == "0":
            typed = input("Log directory: ").strip()
            if typed:
                config.set("LOG_DIRECTORY", typed)
            return typed
        if choice.isdigit() and 1 <= int(choice) <= len(shown):
            picked = candidates[int(choice) - 1]["path"]
            config.set("LOG_DIRECTORY", picked)
            print(f"[OK] Watching {picked}")
            return picked
        return ""


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


# ==================== LIVE PLAYER STATE ====================
class PlayerState:
    """
    Reads the snapshot the mod publishes beside the log, once a second.

    The event log says what *happened*; this says what is *true*. Reading only the log, the
    commentator had to infer the player's condition from the last damage line it saw — which is
    why it kept insisting they were at death's door long after they had eaten, healed and moved
    two biomes away. Everything here is a fresh read: the file is rewritten atomically, so a
    read either gets the whole previous version or the whole new one.
    """

    def __init__(self, directory):
        self.path = None
        self._warned = False
        self.retarget(directory)

    def retarget(self, directory):
        """Points at another game folder, for when the log directory is changed live."""
        self.path = Path(directory) / STATE_FILE_NAME if directory else None
        self._warned = False

    def read(self):
        """The parsed snapshot, or None when the mod is not publishing one."""
        if not self.path or not self.path.is_file():
            self._warn_once(
                f"No {STATE_FILE_NAME} yet — live status and lookups need PAL 2.2 or newer."
            )
            return None
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Caught mid-rewrite, or written by a version we cannot parse. Next poll gets it.
            return None

        age = (time.time() * 1000 - state.get("updated_epoch_ms", 0)) / 1000
        if age > STATE_MAX_AGE_S:
            state["stale_seconds"] = int(age)
        return state

    def _warn_once(self, message):
        if not self._warned:
            self._warned = True
            print(f"[STATE] {message}")

    # ---- used on every send -------------------------------------------------

    def context_line(self):
        """One line on the player's condition, prepended to every prompt."""
        state = self.read()
        if not state:
            return ""

        parts = [state.get("vitals", {}).get("summary", "")]

        effects = state.get("effects") or []
        if effects:
            parts.append("Effects: " + ", ".join(
                f"{e.get('name')} {e.get('level')}" for e in effects) + ".")

        location = state.get("location", {}).get("summary")
        if location:
            parts.append(location)

        if not state.get("active", True):
            parts.append("They have left the world; this is where they stopped.")
        elif "stale_seconds" in state:
            parts.append("(This is minutes old — Minecraft may have been closed.)")

        return " ".join(part for part in parts if part)

    # ---- what the model can ask for -----------------------------------------

    def status_report(self):
        state = self.read()
        if not state:
            return "No live status available; the mod is not running."

        vitals = state.get("vitals", {})
        lines = [vitals.get("summary", "")]
        lines.append(f"Game mode {vitals.get('gamemode', '?')}, "
                     f"difficulty {vitals.get('difficulty', '?')}.")

        effects = state.get("effects") or []
        lines.append("Status effects: " + (", ".join(
            f"{e.get('name')} level {e.get('level')} "
            f"({'permanent' if e.get('seconds_left') == -1 else str(e.get('seconds_left')) + 's left'})"
            for e in effects) + "." if effects else "none."))

        equipment = state.get("equipment", {})
        worn = ", ".join(f"{slot.replace('_', ' ')}: {item}"
                         for slot, item in equipment.items() if item and item != "nothing")
        lines.append("Equipped: " + (worn if worn else "nothing at all."))
        lines.append(state.get("location", {}).get("summary", ""))
        return "\n".join(line for line in lines if line)

    def inventory_report(self, item_filter=None):
        state = self.read()
        if not state:
            return "No inventory available; the mod is not running."

        inventory = state.get("inventory", {})
        items = inventory.get("items") or []

        if item_filter:
            needle = str(item_filter).lower()
            matches = [i for i in items if needle in i.get("name", "").lower()]
            if not matches:
                return f"They are carrying no {item_filter}."
            found = ", ".join(f"{i['count']}x {i['name']}" for i in matches)
            return f"They are carrying {found}."

        report = [inventory.get("summary", "")]
        report.append(f"Holding: {inventory.get('holding', 'nothing')}.")
        equipment = state.get("equipment", {})
        worn = ", ".join(f"{slot.replace('_', ' ')}: {item}"
                         for slot, item in equipment.items() if item and item != "nothing")
        if worn:
            report.append(f"Wearing and holding: {worn}.")
        return "\n".join(line for line in report if line)

    def stats_report(self):
        state = self.read()
        if not state:
            return "No statistics available; the mod is not running."

        stats = state.get("stats") or {}
        if not stats:
            return "Statistics have not arrived from the server yet; try again in a moment."

        lines = [stats.get("summary", "")]

        killed_by = stats.get("killed_by") or []
        if killed_by:
            lines.append("Killed by: " + ", ".join(
                f"{k['name']} x{k['count']}" for k in killed_by) + ".")

        by_cause = stats.get("deaths_by_cause") or []
        if by_cause:
            lines.append("Deaths by cause: " + ", ".join(
                f"{c['cause']} x{c['count']}" for c in by_cause) + ".")

        recent = stats.get("recent_deaths") or []
        if recent:
            lines.append("Most recent deaths: " + "; ".join(
                f"{d.get('message', 'died')} in {d.get('dimension', 'the world')} "
                f"at Y={d.get('y', '?')} ({d.get('when', '')})" for d in recent[-4:]) + ".")

        kills = stats.get("kills_by_creature") or []
        if kills:
            lines.append("Has killed: " + ", ".join(
                f"{k['name']} x{k['count']}" for k in kills) + ".")

        mined = stats.get("blocks_mined") or []
        if mined:
            lines.append("Blocks mined: " + ", ".join(
                f"{b['name']} x{b['count']}" for b in mined) + ".")

        lines.append(f"Damage taken {stats.get('damage_taken', 0)}, "
                     f"dealt {stats.get('damage_dealt', 0)}, "
                     f"walked {stats.get('blocks_walked', 0)} blocks.")
        return "\n".join(line for line in lines if line)


class AITools:
    """
    The three things the model may look up, and the plumbing to describe them to Gemini.

    Declared without an automatic function-calling helper on purpose: the SDK's automatic mode
    wants a Chat object, and this bot drives the conversation itself so it can keep the tool
    round trips out of the saved history.
    """

    def __init__(self, state):
        self.state = state

    def declarations(self):
        return [genai_types.Tool(function_declarations=[
            genai_types.FunctionDeclaration(
                name="check_inventory",
                description=(
                    "Look at what the player is carrying right now, including what is in their "
                    "hands and the durability of their gear."
                ),
                parameters={
                    "type": "OBJECT",
                    "properties": {
                        "item": {
                            "type": "STRING",
                            "description": (
                                "Optional. Ask about one thing only, e.g. 'diamond', 'torch', "
                                "'food'. Leave empty for the whole inventory."
                            ),
                        }
                    },
                },
            ),
            genai_types.FunctionDeclaration(
                name="check_stats",
                description=(
                    "Minecraft's own statistics for this world: how many times the player has "
                    "died and what killed them, what they have killed, blocks mined, distance "
                    "walked, hours played."
                ),
            ),
            genai_types.FunctionDeclaration(
                name="check_player_state",
                description=(
                    "The player's condition beyond health and hunger: armour, active status "
                    "effects, what they are wearing, and what they are currently doing."
                ),
            ),
        ])]

    def run(self, name, args):
        if name == "check_inventory":
            return self.state.inventory_report((args or {}).get("item"))
        if name == "check_stats":
            return self.state.stats_report()
        if name == "check_player_state":
            return self.state.status_report()
        return f"There is no tool called {name}."


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

    def __init__(self, config, telemetry=None):
        self.config = config
        self.telemetry = telemetry
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
        started = time.time()
        backend, audio = self._synthesize(text, urgent)
        if self.telemetry:
            self.telemetry.add_speech(backend, len(text), time.time() - started, bool(audio))
        return audio

    def _synthesize(self, text, urgent=False):
        """Returns the backend that produced the audio alongside it, so the dashboard can chart
        which engine is actually doing the talking rather than which one was configured."""
        if self.backend in ("auto", "chatterbox") and self._chatterbox_available():
            audio = self._chatterbox(text, urgent)
            if audio:
                return "chatterbox", audio
            print("[TTS] Chatterbox failed, falling back to Edge.")

        if self.backend in ("auto", "elevenlabs") and not self.elevenlabs_blocked:
            audio = self._elevenlabs(text)
            if audio:
                return "elevenlabs", audio
            print("[TTS] ElevenLabs failed, falling back to Edge.")

        # Edge is the last resort whatever the chosen backend: a single failed request should
        # cost a different voice, not silence. Previously an explicit backend returned None
        # here and the commentary just went quiet.
        return "edge", self._edge(text)

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
            self.config.increment_usage(api_key, len(text))
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


# ==================== VOICE OPTIONS ====================
class VoiceOptions:
    """
    The lists behind the dashboard's voice pickers.

    Typing a voice id by hand is how you end up with a silent bot and a typo you cannot see,
    so the dashboard offers what each backend actually has: Edge's catalogue, the voices your
    ElevenLabs account can use, and whatever the Chatterbox server is holding. Anything you add
    yourself is remembered in voice_options.json, so a voice added once stays in the list even
    though no backend advertises it.

    Every lookup is a network call, so results are cached and a failure falls back to the
    built-in list rather than leaving an empty dropdown.
    """

    FILE = "voice_options.json"
    CACHE_S = 120
    FIELDS = ("EDGE_VOICE", "VOICE_ID", "CHATTERBOX_VOICE")

    # Enough to be useful when edge-tts cannot be reached; it normally lists about 200.
    BUILTIN_EDGE = [
        "en-US-AriaNeural", "en-US-JennyNeural", "en-US-GuyNeural", "en-US-AnaNeural",
        "en-US-ChristopherNeural", "en-US-EricNeural", "en-US-MichelleNeural",
        "en-GB-SoniaNeural", "en-GB-RyanNeural", "en-AU-NatashaNeural",
        "fr-FR-DeniseNeural", "fr-FR-HenriNeural", "fr-FR-EloiseNeural",
    ]

    def __init__(self, config):
        self.config = config
        self.custom = self._load()
        self._cache = {}
        self._fetched = {}

    # ---- persistence -------------------------------------------------------

    def _load(self):
        try:
            with open(self.FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, json.JSONDecodeError):
            stored = {}
        return {field: list(stored.get(field, [])) for field in self.FIELDS}

    def _save(self):
        try:
            with open(self.FILE, "w", encoding="utf-8") as f:
                json.dump(self.custom, f, indent=2)
        except OSError as e:
            print(f"[WARN] Could not save {self.FILE}: {e}")

    def add(self, field, value):
        """Remembers a voice the backends do not advertise. Returns (added, reason)."""
        value = (value or "").strip()
        if field not in self.FIELDS:
            return False, f"{field} is not a voice setting."
        if not value:
            return False, "Nothing to add."
        if value in self.custom[field]:
            return False, "That one is already in your list."
        self.custom[field].append(value)
        self._save()
        self._fetched.pop(field, None)  # rebuild the merged list on the next read
        return True, f"{value} added and selected."

    # ---- what the dashboard asks for ---------------------------------------

    def for_field(self, field):
        if field not in self.FIELDS:
            return {"choices": [], "value": "", "note": ""}

        age = time.time() - self._fetched.get(field, 0)
        if field not in self._cache or age > self.CACHE_S:
            self._cache[field] = self._build(field)
            self._fetched[field] = time.time()

        choices = list(self._cache[field])
        current = self.config.get(field)
        known = {choice["value"] for choice in choices}

        for value in self.custom[field]:
            if value not in known:
                choices.append({"value": value, "label": f"{value} (added by you)"})
                known.add(value)

        # Whatever is configured must always be in the list, or opening the page would look
        # like it had silently changed the setting. Added after the custom entries, and only
        # when still missing, so a voice you added yourself is not listed twice.
        if current and current not in known:
            choices.insert(0, {"value": current, "label": f"{current} (current)"})
        return {"choices": choices, "value": current, "note": self._note(field)}

    def _note(self, field):
        if field == "VOICE_ID":
            return "Only 'premade' voices work on the free ElevenLabs tier; the rest return 402."
        if field == "CHATTERBOX_VOICE":
            return ("Predefined voices come from the server; in clone mode this lists the "
                    "reference audio files instead.")
        return "Run 'edge-tts --list-voices' to see the full catalogue."

    def _build(self, field):
        if field == "EDGE_VOICE":
            return self._edge_voices()
        if field == "VOICE_ID":
            return self._elevenlabs_voices()
        return self._chatterbox_voices()

    def _edge_voices(self):
        if edge_tts:
            try:
                voices = asyncio.run(edge_tts.list_voices())
                found = [
                    {"value": v["ShortName"],
                     "label": f"{v['ShortName']}  ({v.get('Gender', '?')}, {v.get('Locale', '?')})"}
                    for v in voices if v.get("ShortName")
                ]
                if found:
                    return sorted(found, key=lambda c: c["value"])
            except Exception as e:
                print(f"[VOICES] Could not list Edge voices ({e}); using the built-in list.")
        return [{"value": name, "label": name} for name in self.BUILTIN_EDGE]

    def _elevenlabs_voices(self):
        choices = [{"value": voice_id, "label": f"{name}  (default)"}
                   for name, voice_id in ELEVENLABS_DEFAULT_VOICES.items()]
        known = {choice["value"] for choice in choices}

        api_key = self.config.current_elevenlabs_key()
        if not api_key:
            return choices
        try:
            response = requests.get("https://api.elevenlabs.io/v1/voices",
                                    headers={"xi-api-key": api_key}, timeout=10)
            if response.status_code != 200:
                return choices
            for voice in response.json().get("voices", []):
                voice_id, name = voice.get("voice_id"), voice.get("name", "?")
                if not voice_id or voice_id in known:
                    continue
                category = voice.get("category", "?")
                warning = "" if category == "premade" else " — needs a paid plan"
                choices.append({"value": voice_id, "label": f"{name}  ({category}){warning}"})
                known.add(voice_id)
        except (requests.RequestException, ValueError):
            pass
        return choices

    def _chatterbox_voices(self):
        tts = TTS(self.config)
        clone = self.config.get("CHATTERBOX_MODE").lower() == "clone"
        try:
            names = tts.list_reference_files() if clone else tts.list_predefined_voices()
        except Exception:
            names = []
        return [{"value": name, "label": name} for name in names]


# ==================== AI ====================
def _is_quota_error(error):
    """A 429 is worth separating from a network blip: it means waiting, not retrying."""
    text = str(error)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower()


class AIHandler:
    def __init__(self, config, player, history=None, state=None, telemetry=None):
        self.config = config
        self.player = player
        self.telemetry = telemetry
        self.tts = TTS(config, telemetry=telemetry)
        self.client = genai.Client(api_key=config.get("GEMINI_API_KEY"))
        self.history = list(history) if history else []
        self.max_turns = config.get_int("HISTORY_TURNS")
        self.model = config.get("GEMINI_MODEL")
        self.system_prompt = config.get("SYSTEM_PROMPT") + FORMAT_RULES

        # Offered on the setting alone, not on whether the state file exists yet: the bot is
        # usually started before Minecraft, and probing here would silently disable lookups for
        # the whole run. A missing file just makes a lookup answer "not available".
        wanted = config.get("AI_TOOLS").lower() == "true"
        self.tools = AITools(state) if wanted and state else None
        if self.tools:
            self.system_prompt += TOOL_RULES
            print("[AI] Inventory, status and death statistics available as lookups.")

        if self.tts.supports_tags():
            self.system_prompt += PARALINGUISTIC_RULES
            print("[AI] Chatterbox is live — performance cues enabled in the prompt.")

    def comment(self, prompt_text, urgent=False):
        started = time.time()
        self.used_tools = []
        try:
            reply = self._generate(prompt_text)
        except Exception as e:
            print(f"[ERROR] Gemini call failed: {e}")
            if self.telemetry:
                self.telemetry.add_error(e, quota=_is_quota_error(e))
            return

        if not reply:
            print("[WARN] Gemini returned nothing.")
            return

        print(f"[AI] {reply}")
        self._remember(prompt_text, reply)
        if self.telemetry:
            self.telemetry.add_reply(prompt_text, reply, time.time() - started, urgent,
                                     self.used_tools)

        # `urgent` also drives the delivery, not just the timing: on Chatterbox it dials up the
        # emotional exaggeration so a death sounds like one.
        audio = self.tts.synthesize(reply, urgent=urgent)
        if audio:
            self.player.play(audio, interrupt=urgent)

    def _generate(self, prompt_text):
        """
        One reply, going round again for each batch of lookups the model asks for.

        Tool round trips are deliberately not kept: the saved conversation stays a readable
        exchange, and the model does not re-read a stale inventory from ten minutes ago.
        """
        contents = self.history + [
            genai_types.Content(role="user", parts=[genai_types.Part(text=prompt_text)])
        ]

        for _ in range(MAX_TOOL_ROUNDS):
            # Counted before the call, and once per round: a reply that needed a lookup costs
            # two requests against the quota, and the rate limit does not care that they were
            # the same reply.
            if self.telemetry:
                self.telemetry.add_request()
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=self._request_config(),
            )

            calls = list(getattr(response, "function_calls", None) or [])
            if not calls:
                return (response.text or "").strip()
            if not response.candidates:
                return ""

            answers = []
            for call in calls:
                result = self.tools.run(call.name, dict(call.args or {}))
                self.used_tools.append(call.name)
                print(f"[TOOL] {call.name}({dict(call.args or {})}) -> "
                      f"{result.splitlines()[0][:90] if result else 'nothing'}")
                answers.append(genai_types.Part.from_function_response(
                    name=call.name, response={"result": result}))

            contents = contents + [
                response.candidates[0].content,
                genai_types.Content(role="user", parts=answers),
            ]

        print("[WARN] The model kept asking for lookups instead of answering; skipping this one.")
        return ""

    def _request_config(self):
        settings = {
            "system_instruction": self.system_prompt,
            # Enough room for a lookup and a short reply. The 1-2 sentence rule caps the
            # spoken part regardless.
            "max_output_tokens": 400 if self.tools else 200,
            # This bot drives the tool loop itself, so the SDK's automatic mode stays off —
            # which also silences its "use Chat.send_message instead" warning.
            "automatic_function_calling": genai_types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        }
        if self.tools:
            settings["tools"] = self.tools.declarations()
        return genai_types.GenerateContentConfig(**settings)

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

    def __init__(self, config, ai, state=None, telemetry=None):
        self.config = config
        self.ai = ai
        self.state = state
        self.telemetry = telemetry
        self.pending = []
        self.scene = None
        self.last_send = time.time()
        self.notable_deadline = None
        self.send_interval = config.get_int("SEND_INTERVAL")
        self.debounce = config.get_float("NOTABLE_DEBOUNCE")
        self.max_chars = config.get_int("MAX_CHARS_PER_SEND")

    def ingest(self, events):
        if self.telemetry:
            self.telemetry.add_events(events)
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

        # The live snapshot is read now, at send time, so the model is told the health the
        # player has when it speaks rather than the health they had when they were hit. The
        # scene line is only a fallback: it is throttled to 25s, so it can be stale too.
        status = self.state.context_line() if self.state else ""
        prompt = ""
        if status:
            prompt += f"[PLAYER RIGHT NOW] {status}\n"
        elif self.scene:
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
            MinecraftFinder.choose(self.config)

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
        # Telemetry outlives an individual Start, so the dashboard keeps its history when you
        # stop watching and start again.
        self.telemetry = Telemetry() if Telemetry else None
        self.state = PlayerState(self.config.get("LOG_DIRECTORY"))
        self.options = VoiceOptions(self.config)
        self.dashboard = None
        # Watching used to be a blocking loop in the main thread that only Ctrl+C could end,
        # which is why starting the bot was console-only. It now runs on its own thread and
        # ends on an Event, so the menu and the dashboard drive it through the same two calls.
        self._watcher = None
        self._stop = threading.Event()
        self._quit = threading.Event()

    def run(self):
        print("=" * 60)
        print("MINECRAFT AI COMMENTATOR")
        print("=" * 60)

        if not self.config.is_configured():
            self.wizard.run_initial_setup()
        self._check_log_directory()

        self._start_dashboard()

        # Loading the model costs ~12s and ~5GB of VRAM, so it happens once per run rather
        # than on every Start. It stays up until you quit.
        self._ensure_server()
        try:
            self._menu_loop()
        finally:
            self._shutdown_server()
            if self.dashboard:
                self.dashboard.stop()

    def _check_log_directory(self):
        """
        A folder that was right last month is wrong once you make a new instance, and the
        symptom — the bot sitting silently on a path that no longer exists — looks identical
        to Minecraft simply not running.
        """
        current = self.config.get("LOG_DIRECTORY")
        if current and Path(current).is_dir():
            return
        if current:
            print(f"[FIND] {current} does not exist any more.")
        MinecraftFinder.choose(self.config)
        self.state.retarget(self.config.get("LOG_DIRECTORY"))

    def _start_dashboard(self):
        if not Dashboard or self.config.get("DASHBOARD").lower() != "true":
            return
        dashboard = Dashboard(
            self.config, self.telemetry, self.state,
            options=self.options, finder=MinecraftFinder, controller=self,
            host=self.config.get("DASHBOARD_HOST"),
            port=self.config.get_int("DASHBOARD_PORT"),
        )
        if dashboard.start():
            self.dashboard = dashboard

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

    # ---- what the dashboard is allowed to ask for ---------------------------

    def start_chatterbox(self):
        """Menu option 2 -> 6 ("start it now"), as a single call the page can make."""
        if self.launcher:
            return False, "Chatterbox is already being managed by the bot."
        launcher = ChatterboxLauncher(self.config)
        if not launcher.enabled():
            return False, "Set the Chatterbox server folder first."
        if TTS(self.config)._chatterbox_available():
            return False, "Chatterbox is already running."
        if launcher.start():
            self.launcher = launcher
            return True, "Chatterbox is up."
        return False, "Chatterbox would not start — check the server folder."

    # Two lines that differ only in delivery, so the urgent boost can actually be judged — the
    # same pair the console's preview speaks.
    TEST_LINES = {
        False: "Twenty-one whole seconds to mine some grey rocks. Riveting.",
        True: "A creeper! Behind you! Move, you absolute walnut!",
    }

    def synthesize_test(self, urgent=False):
        """
        One spoken line for the dashboard's voice tester.

        Returns (audio, engine, problem). The engine that answered is returned rather than the
        one configured, because `auto` falls back quietly and knowing Edge spoke when you asked
        for Chatterbox is the point of testing.
        """
        try:
            tts = TTS(self.config)
            engine, audio = tts._synthesize(self.TEST_LINES[bool(urgent)], urgent=bool(urgent))
        except Exception as e:
            return None, "", f"The voice failed: {e}"
        if not audio:
            return None, "", "No engine produced any audio. Check the keys and the server."
        return audio, engine, ""

    def clone_voice(self, file_path):
        """
        Menu option 2 -> 4, as one call.

        Takes a path on this machine rather than a browser upload because the dashboard is
        loopback-only by design — the file is already on the same disk, and the console flow
        asks for exactly the same thing.
        """
        path = (file_path or "").strip().strip("'\"")
        if not path:
            return False, "No file given."
        tts = TTS(self.config)
        if not tts._chatterbox_available():
            return False, "Chatterbox is not running."
        uploaded = tts.upload_reference(path)
        if not uploaded:
            return False, "That file could not be uploaded — it must be an existing .wav or .mp3."
        self.config.set("CHATTERBOX_MODE", "clone")
        self.config.set("CHATTERBOX_VOICE", uploaded)
        return True, f"Now cloning from {uploaded}."

    def quit_app(self):
        """
        Ends the program from the page.

        The menu is parked in a blocking `input()` that nothing can interrupt from another
        thread, so a clean shutdown runs here and the process is then ended outright. It is
        deferred by a moment so this request can still be answered first.
        """
        def shutdown():
            time.sleep(0.4)
            self.stop_watching(timeout=3.0)
            self._shutdown_server()
            if self.dashboard:
                self.dashboard.stop()
            print("\n[QUIT] Stopped from the dashboard.")
            os._exit(0)

        self._quit.set()
        threading.Thread(target=shutdown, name="quit", daemon=True).start()
        return True, "Shutting down."

    def _menu_loop(self):
        while True:
            # The dashboard can start and stop watching too, so the menu reports the real state
            # rather than assuming the console is the only thing driving it.
            first = "Stop" if self.is_watching() else "Start"
            print(f"\n1. {first}   2. Voice   3. API keys   4. Settings   5. Quit"
                  + ("   6. Dashboard" if self.dashboard else ""))
            choice = input("> ").strip()
            if choice == "1":
                if self.is_watching():
                    ok, message = self.stop_watching()
                    print(f"[STOP] {message}")
                else:
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
            elif choice == "6" and self.dashboard:
                print(f"[DASH] {self.dashboard.url}")
                webbrowser.open(self.dashboard.url)

    # ---- watching, driven by the menu and by the dashboard alike -------------

    def is_watching(self):
        return bool(self._watcher and self._watcher.is_alive())

    def blocking_reason(self):
        """Why Start would not work, in one sentence, or None. The page greys the button out."""
        directory = self.config.get("LOG_DIRECTORY")
        if not directory:
            return "No Minecraft folder is set."
        if not Path(directory).is_dir():
            return f"{directory} does not exist."
        if not self.config.get("GEMINI_API_KEY"):
            return "No Gemini API key is set."
        return None

    def start_watching(self):
        """Starts the watch thread. Returns (ok, message); never blocks."""
        if self.is_watching():
            return False, "Already watching."
        reason = self.blocking_reason()
        if reason:
            return False, reason

        self._stop.clear()
        self._watcher = threading.Thread(target=self._watch, name="watcher", daemon=True)
        self._watcher.start()
        return True, "Watching."

    def stop_watching(self, timeout=6.0):
        if not self.is_watching():
            return False, "Not watching."
        self._stop.set()
        self._watcher.join(timeout=timeout)
        # A thread still alive here is stuck in a request that will end on its own; it is a
        # daemon and the stop flag is set, so it cannot outlive the process or speak again.
        return True, "Stopped." if not self.is_watching() else "Stopping…"

    def start(self):
        """Menu option 1: start, then hold the console until Ctrl+C, as it always did."""
        ok, message = self.start_watching()
        print(f"[START] {message}" if ok else f"[WARN] {message}")
        if not ok:
            return
        print("Press Ctrl+C to stop watching.\n")
        try:
            while self.is_watching():
                time.sleep(0.3)
        except KeyboardInterrupt:
            self.stop_watching()
            print("\n[STOP] Stopped.")

    def _watch(self):
        player = AudioPlayer()
        if not player.available() and platform.system() != "Windows":
            print("[WARN] No audio player found — install ffplay (ffmpeg), mpv or mpg123.")

        state = self.state
        ai = AIHandler(self.config, player, history=ChatHistory.choose(), state=state,
                       telemetry=self.telemetry)
        tailer = LogTailer(self.config.get("LOG_DIRECTORY"))
        commentator = Commentator(self.config, ai, state=state, telemetry=self.telemetry)

        found = tailer.find_log()
        if found:
            tailer.attach(found)
        else:
            # Waiting for the log used to block until it appeared; now a Stop from the page has
            # to be able to cut that short, so it is polled against the same flag.
            while not self._stop.is_set():
                found = tailer.find_log()
                if found:
                    tailer.attach(found)
                    break
                self._stop.wait(2.0)
            if self._stop.is_set():
                return

        interval = self.config.get_float("CHECK_INTERVAL")
        print("\n[START] Watching. CRITICAL events interrupt, NOTABLE groups, INFO waits.")
        if self.dashboard:
            print(f"[DASH] Follow along at {self.dashboard.url}")

        if self.telemetry:
            self.telemetry.session_started()
        try:
            while not self._stop.is_set():
                events = tailer.poll()
                if events:
                    for event in events:
                        print(f"  · {event.get('lvl','?'):8} {event.get('msg','')}")
                    commentator.ingest(events)
                commentator.maybe_flush()
                # wait() rather than sleep() so a Stop is acted on immediately instead of after
                # a full poll interval.
                self._stop.wait(interval)
        finally:
            if self.telemetry:
                self.telemetry.session_stopped()
            ChatHistory.save(ai.history)


if __name__ == "__main__":
    App().run()
