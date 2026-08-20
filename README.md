# PlayerActionLogger (PAL)

**PlayerActionLogger (PAL)** is a Minecraft mod that logs what *your* player does to a structured
log file, designed to be read by an LLM so it can commentate your gameplay live.
It ships with a companion Python bot that speaks the commentary out loud.

if you made video with it please credit this project

---

## 🪶 Overview

- **Minecraft Version:** 26.1.2 — the `mc/1.21.1` branch builds the same mod for 1.21.1
- **Mod Loader:** Fabric (client-side only)
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

## 🧱 Installation

1. Install **Fabric Loader** for Minecraft 26.1.2 (or 1.21.1 from the `mc/1.21.1` branch).
2. Install **Fabric API** (required).
3. Drop the PAL `.jar` into your `mods` folder.
4. Launch the game — logging starts as soon as you join a world.

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
