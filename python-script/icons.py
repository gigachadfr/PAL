"""
Item icons for the dashboard, read straight out of the jars already on this machine.

Nothing is downloaded and nothing is bundled. Minecraft's own jar holds every vanilla texture,
and each mod jar holds its own, so scanning the game folder covers Create, Farmer's Delight and
anything installed later without a list of "known mods" to maintain.

Two wrinkles the jars force on us:

  * **Blocks have no item texture.** `cobblestone` lives in `textures/block/`, never in
    `textures/item/`, and its inventory icon is really a 3D render of the block model. Rendering
    models is far out of scope, so a block falls back to its face texture — flat, but instantly
    recognisable, which is the whole point of the grid.
  * **Animated textures are film strips.** `water_still.png` is 16x512: thirty-two frames stacked
    vertically. Cropping without Pillow would mean re-encoding PNGs by hand, so the page draws
    icons as `background-image` with `background-size: 100% auto`, which shows the top frame and
    nothing else. Still images are unaffected, so one rule covers both.

The index maps a bare registry path to the jar entry holding it, deliberately dropping the
namespace: the mod logs "Grizzly Bear" with no way back to `alexsmobs:grizzly_bear`. Collisions
between two mods using the same path are possible but rare, and vanilla always wins.
"""

import io
import re
import threading
import zipfile
from pathlib import Path

# Where a texture can be, best first. An item texture is the real inventory icon; a block texture
# is the fallback described above.
KINDS = ("item", "block")

# Block textures are rarely named after the block alone. `oak_log` is `oak_log_top`/`oak_log`,
# a furnace is `furnace_front`, and animated ones end in `_still`. Tried in order, after the
# bare name.
BLOCK_SUFFIXES = ("", "_front", "_side", "_top", "_all", "_still", "_0")

# A mod jar can carry other mods inside it — NeoForge's JarJar. `create-aeronautics-bundled`
# is nothing but a wrapper: its own assets folder is empty and everything is one level down.
NESTED_PREFIX = "META-INF/jarjar/"

# Enough for a full inventory several times over; icons are a few hundred bytes each.
CACHE_MAX = 512

# What a folder must contain to be the game directory rather than something above or below it.
GAME_MARKERS = ("mods", "versions", "options.txt", "saves")

SAFE_NAME = re.compile(r"^[a-z0-9_]+$")


def readable_to_path(name):
    """
    "Diamond Sword" -> "diamond_sword".

    Exactly inverts the mod's `Names.prettify`, which only title-cases words and swaps `_` for a
    space, so the round trip is lossless — including oddities like `tnt` -> "Tnt" -> `tnt`.
    """
    return (name or "").strip().lower().replace(" ", "_")


def png_size(data):
    """(width, height) from a PNG header, or None. IHDR is fixed-offset, so no decoding needed."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


class IconLibrary:
    """
    A lazy index of every texture in the game folder's jars.

    Building it reads each jar's central directory — the file list, not the contents — so 50-odd
    mods cost well under a second. Texture bytes are pulled out on demand and kept in a small
    cache, which avoids unpacking thousands of files nobody will look at.
    """

    def __init__(self, game_dir=None):
        self._lock = threading.Lock()
        self._index = {}          # "item/diamond_sword" -> (jar path, nested jar or None, entry)
        self._cache = {}          # "item/diamond_sword" -> PNG bytes
        self._sources = []        # jars that contributed, for the status endpoint
        self._built = False
        self._game_dir = Path(game_dir) if game_dir else None

    # ---- discovery ---------------------------------------------------------

    def retarget(self, game_dir):
        """Points at another instance and drops the index, for when LOG_DIRECTORY changes."""
        game_dir = Path(game_dir) if game_dir else None
        with self._lock:
            if game_dir == self._game_dir:
                return
            self._game_dir = game_dir
            self._index, self._cache, self._sources, self._built = {}, {}, [], False

    @staticmethod
    def game_dir_from_log_directory(log_directory):
        """
        The mod writes to `<game>/logs/player_actions`, so the game folder is a parent — but how
        far up depends on the launcher, hence walking up rather than assuming two levels.
        """
        if not log_directory:
            return None
        path = Path(log_directory)
        for candidate in (path, *path.parents):
            if any((candidate / marker).exists() for marker in GAME_MARKERS):
                return candidate
        return None

    def _jars(self):
        """
        Mod jars first, then vanilla — but vanilla wins on conflicts, see `_add`.

        CurseForge keeps instances and the vanilla jars apart: an instance has `mods/` but no
        `versions/`, which sits in a shared `Install/` next to `Instances/`. So when the game
        folder has no `versions/`, we look for one among its ancestors' children.
        """
        if not self._game_dir or not self._game_dir.is_dir():
            return [], []

        mods = sorted(p for p in (self._game_dir / "mods").glob("*.jar") if p.is_file())

        versions = self._game_dir / "versions"
        if not versions.is_dir():
            versions = None
            for parent in list(self._game_dir.parents)[:3]:
                for sibling in parent.iterdir() if parent.is_dir() else []:
                    if (sibling / "versions").is_dir():
                        versions = sibling / "versions"
                        break
                if versions:
                    break

        vanilla = []
        if versions and versions.is_dir():
            # A loader jar (`fabric-loader-…`) carries no assets; the client jar is named after
            # its folder. Newest last so it overwrites older vanilla entries.
            for folder in sorted(versions.iterdir()):
                jar = folder / f"{folder.name}.jar"
                if jar.is_file() and not folder.name.startswith(("fabric-loader", "forge-", "neoforge-")):
                    vanilla.append(jar)

        return mods, vanilla

    # ---- index -------------------------------------------------------------

    def _add(self, key, source, vanilla):
        """Vanilla always wins; between two mods, the first one scanned keeps the slot."""
        if key not in self._index or vanilla:
            self._index[key] = source

    def _scan_zip(self, zf, jar_path, nested, vanilla):
        found = 0
        for entry in zf.namelist():
            # assets/<namespace>/textures/<kind>/<path>.png
            if not entry.startswith("assets/") or not entry.endswith(".png"):
                continue
            parts = entry.split("/")
            if len(parts) < 5 or parts[2] != "textures" or parts[3] not in KINDS:
                continue
            path = "/".join(parts[4:])[:-4]          # keeps subfolders some mods use
            self._add(f"{parts[3]}/{path}", (jar_path, nested, entry), vanilla)
            found += 1
        return found

    def _scan_jar(self, jar_path, vanilla):
        total = 0
        try:
            with zipfile.ZipFile(jar_path) as zf:
                total += self._scan_zip(zf, jar_path, None, vanilla)
                for entry in zf.namelist():
                    if not entry.startswith(NESTED_PREFIX) or not entry.endswith(".jar"):
                        continue
                    try:
                        with zipfile.ZipFile(io.BytesIO(zf.read(entry))) as inner:
                            total += self._scan_zip(inner, jar_path, entry, vanilla)
                    except (zipfile.BadZipFile, KeyError, OSError):
                        continue  # a broken nested jar must not sink the whole scan
        except (zipfile.BadZipFile, OSError):
            return 0
        if total:
            self._sources.append({"jar": jar_path.name, "textures": total})
        return total

    def _build(self):
        """Caller holds the lock."""
        self._built = True
        mods, vanilla = self._jars()
        for jar in mods:
            self._scan_jar(jar, vanilla=False)
        for jar in vanilla:
            self._scan_jar(jar, vanilla=True)

    def ensure_built(self):
        with self._lock:
            if not self._built:
                self._build()
            return bool(self._index)

    # ---- lookup ------------------------------------------------------------

    def resolve(self, path):
        """
        A registry path -> the index key that best represents it, or None.

        Item texture first, then the block face fallbacks. Returns the key rather than the bytes
        so the page can build a URL and let the browser cache it.
        """
        if not path or not SAFE_NAME.match(path):
            return None
        self.ensure_built()
        with self._lock:
            if f"item/{path}" in self._index:
                return f"item/{path}"
            for suffix in BLOCK_SUFFIXES:
                key = f"block/{path}{suffix}"
                if key in self._index:
                    return key
            # Last resort: a few blocks are registered as `<name>_block` but their texture is just
            # `<name>` — `magma_block` is `magma.png`. Tried only after the full name has failed,
            # so `slime_block`, which does have its own texture, is never diverted here.
            if path.endswith("_block"):
                stem = path[: -len("_block")]
                for key in (f"item/{stem}", f"block/{stem}"):
                    if key in self._index:
                        return key
        return None

    def png(self, key):
        """
        The texture bytes for an index key, or None.

        Serves only keys already in the index, which is what makes the icon route safe to build
        straight from a URL: a path with `..` in it was never indexed and simply misses.
        """
        self.ensure_built()
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            source = self._index.get(key)
        if not source:
            return None

        jar_path, nested, entry = source
        try:
            with zipfile.ZipFile(jar_path) as zf:
                if nested:
                    with zipfile.ZipFile(io.BytesIO(zf.read(nested))) as inner:
                        data = inner.read(entry)
                else:
                    data = zf.read(entry)
        except (zipfile.BadZipFile, KeyError, OSError):
            return None

        with self._lock:
            if len(self._cache) >= CACHE_MAX:
                self._cache.clear()
            self._cache[key] = data
        return data

    def manifest(self, names):
        """
        Maps the readable names in an inventory to icon keys, in one request.

        The page asks once per refresh instead of firing a 404-prone <img> per item, so an
        unrecognised name costs nothing and simply renders as a lettered tile.
        """
        ready = self.ensure_built()
        icons = {}
        for name in names or []:
            key = self.resolve(readable_to_path(name))
            if key:
                icons[name] = key
        return {"ready": ready, "icons": icons}

    def status(self):
        self.ensure_built()
        with self._lock:
            return {
                "ready": bool(self._index),
                "textures": len(self._index),
                "game_dir": str(self._game_dir) if self._game_dir else "",
                "jars": len(self._sources),
                "sources": sorted(self._sources, key=lambda s: -s["textures"])[:12],
            }
