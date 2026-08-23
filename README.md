# PlayerActionLogger (PAL)

**PlayerActionLogger (PAL)** is a Minecraft mod that logs what *your* player does to a structured
log file, designed to be read by an LLM so it can commentate your gameplay live.
It ships with a companion Python bot that speaks the commentary out loud.

if you made video with it please credit this project

![The dashboard](screen/dashboard.png)

*The companion bot's dashboard: live vitals, the inventory with real item icons, ElevenLabs key
quotas, the event feed, and Start/Stop for the commentator — no terminal needed.*

---

## 🪶 Overview

- **Minecraft Versions:** 26.1.2, 1.21.11 and 1.21.1, each in its own folder — see below
- **Mod Loader:** Fabric, plus a NeoForge build for 1.21.1 (client-side only)
- **Log Location:** `<your minecraft folder>/logs/player_actions/session.log`
- **Format:** JSONL — one JSON event per line
- **Live state:** `player_state.json` in the same folder, rewritten every second
- **Works in multiplayer:** yes, and it only ever logs your own player
- **Python Script:** `ai_minecraft_bot.py` reads the log and reacts out loud
  (Gemini + ElevenLabs API keys required) — the mod works fine on its own too.

---

## 💡 Why I Made This

I created this mod mostly because I saw **catsdontlikecofee**'s videos
([TikTok link](https://www.tiktok.com/@iiillii11iilliill)), and since he hasn't released his work
publicly yet, I decided to make something similar myself.
I used AI tools to help build the mod (since I don't personally know Java), but not the Python script.
The main idea was to build something functional for AI/LLM experiments inside Minecraft, to
observe and comment on gameplay based on the log.

---

## 📁 One folder per Minecraft version

Versions live side by side in this repository rather than on separate branches, so you can build
any of them from one checkout:

| Folder | Minecraft | Loader | Mappings | Mod version |
|---|---|---|---|---|
| [`26.1.2/`](26.1.2/) | 26.1.2 | Fabric | none — 26.1 ships unobfuscated | 2.2.0 |
| [`1.21.11/`](1.21.11/) | 1.21.11 | Fabric | Mojang official | 2.2.0 |
| [`1.21.1/`](1.21.1/) | 1.21.1 | Fabric | Mojang official | 2.2.0 |
| [`1.21.1(neoforge)/`](<1.21.1(neoforge)/>) | 1.21.1 | NeoForge 21.1 | Mojang official | 2.2.0 |

**All four are the same mod.** One version number means one feature set, so a jar labelled
2.2.0 behaves the same whichever game — and whichever loader — it is for.

Each folder is a self-contained Gradle project:

```bash
cd 1.21.11
./gradlew build          # -> build/libs/PAL-Fabric_1.21.11-2.2.0.jar
```

Jars are named **`PAL-<Loader>_<Minecraft version>-<mod version>.jar`**, because several builds
of this mod now end up in the same downloads folder and `PAL-2.2.0.jar` said nothing about which
game it was for.

### One source tree, not three

Every folder is built against **Mojang's official names**, including the two versions Yarn still
covers. Yarn would work there, but 26.1 is unobfuscated and so already uses Mojang's names — and
a mod written twice in two naming schemes is a mod where a fix lands in one copy and is forgotten
in the other.

The result is that the three sources are nearly the same files. Measured:

- **26.1.2 → 1.21.11:** one line. `getDefaultClockTime()` became `getDayTime()`.
- **1.21.11 → 1.21.1:** 65 lines across 8 files, and every one is a real Minecraft change
  between those releases rather than a naming difference:

| What changed | 1.21.1 | 1.21.11 and later |
|---|---|---|
| Registry id class | `ResourceLocation` | `Identifier` |
| Toast host class | `ToastComponent` | `ToastManager` |
| Damage hook | `hurt(source, amount)` | `hurtServer(level, source, amount)` |
| Death screen constructor | `(Component, boolean)` | `(Component, boolean, LocalPlayer)` |
| Inventory backpack | the public `items` list | `getNonEquipmentItems()` |
| Game profile | a class, `getName()` | a record, `name()` |

Nothing was dropped to make 1.21.1 fit: the live state file, the vanilla statistics, the death
history and the Fire Resistance fix are all there.

**Mixin targets are verified, not assumed.** Every `@Inject`, `@Shadow` and `@Accessor` in the
1.21.1 and 1.21.11 builds resolves to a real obfuscated member at compile time — the generated
`playeractionlogger.refmap.json` names all nine. That is what catches a hook that silently stops
matching, which is how an earlier port reached the game and crashed on a renamed field.

### The NeoForge build

Same source tree again. Because every folder already uses Mojang's official names — and NeoForge
compiles *and runs* against those same names — the trackers, the log, the state exporter and
nine of the ten mixins are byte-for-byte what the Fabric build has. Only the loader glue differs:

| What | Fabric | NeoForge |
|---|---|---|
| Entry point | `ClientModInitializer` | `@Mod` + `@EventBusSubscriber` |
| Game folder | `FabricLoader.getInstance().getGameDir()` | `FMLPaths.GAMEDIR.get()` |
| Join / leave | `ClientPlayConnectionEvents` | `ClientPlayerNetworkEvent.LoggingIn/Out` |
| Tick | `ClientTickEvents.END_CLIENT_TICK` | `ClientTickEvent.Post` |
| Chat sent | `ClientSendMessageEvents.ALLOW_CHAT` | `ClientChatEvent` |
| Chat received | `ClientReceiveMessageEvents.CHAT` | `ClientChatReceivedEvent` |
| Commands typed | `ALLOW_COMMAND` | *no event* — see below |
| Metadata | `fabric.mod.json` | `META-INF/neoforge.mods.toml` |

**No Forgified Fabric API.** It would have worked, and it is the obvious way to move a Fabric mod
across, but it buys nothing here: the mod used exactly four Fabric API callbacks and NeoForge has
its own for all but one of them. Depending on FFAPI would mean every user installing another mod
so that four listeners could keep their original spelling.

**The exception is commands.** NeoForge has no client-side event for a command the player types:
`ClientChatEvent` is chat only, and `RegisterClientCommandsEvent` declares commands rather than
watching them. `ClientCommandMixin` hooks `ClientPacketListener.sendCommand` instead — the same
technique the mod already uses for its nine other hooks, injected at `HEAD` without cancelling,
so the command runs untouched.

**The game folder is reached through one helper**, `util/GameDir`, rather than inline in the
three classes that write files. It is the only call that differs, and keeping it in one place is
what stops the two builds drifting apart.

**No refmap here, on purpose.** Fabric runs on intermediary names and needs the mapping file;
NeoForge runs on the same Mojang names it compiled against, so the mixins need no translation.
The targets were checked all the same — all twelve `@Inject` targets across the ten mixins were
resolved against Mojang's official `client.txt` for 1.21.1 before the build was called done.

---

## 🧱 Installation

**On Fabric:**

1. Install **Fabric Loader** for your Minecraft version.
2. Install **Fabric API** (required).
3. Drop the matching PAL `.jar` into your `mods` folder — the file name tells you which
   Minecraft version it is for.
4. Launch the game — logging starts as soon as you join a world.

**On NeoForge (1.21.1):**

1. Install **NeoForge 21.1** for Minecraft 1.21.1.
2. Drop `PAL-NeoForge_1.21.1-2.2.0.jar` into your `mods` folder.
3. Launch the game.

Nothing else is needed — in particular **not Forgified Fabric API**. The NeoForge build uses
NeoForge's own events, so it adds no dependency of its own and drops straight into a modpack.

The mod is client-side only. You can use it on any server without the server needing it.

---

## 🧾 What the log looks like

Each line is one event, with a priority level and a plain-English sentence written for a model
to read:

```jsonl
{"t":"21:14:03","lvl":"INFO","type":"scene","msg":"Underground at Y=12 in a Dripstone Caves, in the Overworld, night."}
{"t":"21:14:48","lvl":"INFO","type":"mining","msg":"Finished mining — 45s, 66 blocks: 62x Stone, 4x Iron Ore."}
{"t":"21:15:02","lvl":"NOTABLE","type":"valuable_ore","msg":"Found DIAMOND ORE for the first time this session, at Y=-54!"}
{"t":"21:15:19","lvl":"CRITICAL","type":"creeper_fuse","msg":"A Creeper is hissing 2 blocks away, about to explode."}
{"t":"21:15:21","lvl":"CRITICAL","type":"death","msg":"DIED: giga_chad was blown up by a Creeper at Y=-54."}
```

### Priority levels

| Level | Meaning | What the bot does |
|---|---|---|
| `INFO` | Ambient context, already aggregated | Batched, sent on the idle timer |
| `NOTABLE` | Worth a comment | Triggers a send, grouped over a few seconds |
| `CRITICAL` | React now — death, low health, a lit creeper | Sent immediately, interrupts playback |

### What gets tracked

Scene (biome, time of day, weather, dimension, depth) · health, hunger, drowning, damage taken ·
death with the game's exact death message · nearby hostile mobs and lit creepers · mining and
building, aggregated into session summaries · valuable ore finds · kills · containers used ·
crafting milestones · advancements · chat sent and received · commands.

Routine actions are **aggregated on purpose**: you get "mined 62 stone in 45s", not 62 separate
lines. Drowning the interesting events in noise is what makes AI commentary go generic.

Emergencies are checked against what can actually hurt you: standing in lava with Fire
Resistance is not a `CRITICAL`, and neither is a long drop with Slow Falling or an elytra. A
potion you drank on purpose should not have the commentator screaming every five seconds.

---

## 📸 The live state file

Beside the log sits `player_state.json`, rewritten once a second. The log answers *what
happened*; this answers *what is true right now* — and those are very different questions. A
commentator reading only the log sees "Took 6 damage, health now 8/20" and is still calling you
half dead twenty minutes and three golden apples later.

```json
{
  "vitals":    { "health": 7.0, "max_health": 20.0, "health_state": "badly hurt",
                 "hunger": 4, "armor": 9, "doing": "sprinting",
                 "summary": "Health 7/20 (badly hurt), hunger 4/20 (hungry), armour 9, XP level 27." },
  "effects":   [ { "name": "Regeneration", "level": 2, "seconds_left": 12 } ],
  "equipment": { "main_hand": "Diamond Pickaxe (312/1561 durability left)" },
  "inventory": { "slots_used": 3, "slots_free": 33, "items": [ { "name": "Diamond", "count": 4 } ] },
  "stats":     { "deaths": 7, "killed_by": [ { "name": "Creeper", "count": 3 } ],
                 "deaths_by_cause": [ { "cause": "the fall", "count": 2 } ] }
}
```

It is written to a temporary file and moved into place, so a reader never catches it half
written. Every section carries a ready-made `summary` sentence, so the bot never has to phrase
raw numbers itself.

**The statistics are Minecraft's own.** `deaths`, `killed_by`, `kills_by_creature`,
`blocks_mined`, `hours_played` and the rest come straight from the vanilla counters the game has
always kept — nothing is recounted or estimated. The client keeps them as a cache the server
only refills on request, which is why vanilla's numbers look frozen unless you open the stats
screen; the mod asks for a refresh every 30 seconds, and immediately after a death.

Vanilla records **which creature** killed you and how often, but files every fall, lava bath and
drop into the void under one undifferentiated death counter. `deaths_by_cause` fills that gap
from the mod's own history in `deaths.json`, which survives across sessions. The cause is taken
from the death message's *translation key*, not its text, so the tally is the same on a French
client as on an English one.

---

## 🐍 Python Script Usage

```bash
cd python-script
pip install -r requirements.txt
python ai_minecraft_bot.py
```

On first run it walks you through setup:

1. **Gemini API key**
2. **ElevenLabs API key(s)** — optional, you can add several and it rotates when one hits its quota
3. **Voice ID** — see the note below
4. **Log directory** — found for you: the bot reads it from a running Minecraft, or from the
   instances on your disk, and offers the one you played last. No path to paste.

**About the voice:** the free ElevenLabs plan no longer allows Voice Library voices over the API
(HTTP 402), and its 10,000 characters/month is about 10 minutes of speech. Three backends are
supported, and the bot falls through them automatically:

- **Chatterbox** *(recommended)* — a local GPU model with real emotion control. The commentary
  gets more dramatic on deaths and creepers, because the mod tags those events as `CRITICAL`.
- **Edge TTS** — free, no API key, no character cap. Always available as the safety net.
- **ElevenLabs** — best raw quality, but tight free limits.

See [python-script/README.md](python-script/README.md#voice) for setup.

Everything else (personality prompt, models, intervals) is editable from the Settings menu — or
from the **web dashboard**, which starts with the bot on `http://127.0.0.1:8765` and shows live
vitals, inventory, deaths, the event feed, what the commentator said, and where every ElevenLabs
key stands against its quota and reset date. See
[python-script/README.md](python-script/README.md#dashboard).

Audio playback needs one of `ffplay` (ffmpeg), `mpv` or `mpg123` installed.

⚠️ Your keys live in `.env` and `elevenlabs_keys.json`. Both are gitignored — keep it that way.

---

## 🧾 License

This project is licensed under the **MIT License**.
Feel free to use, modify, and share it — attribution is appreciated.
