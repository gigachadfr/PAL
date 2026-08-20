# Minecraft AI Commentator

Reads the JSONL log produced by the PAL mod and reacts to your gameplay out loud, using Google
Gemini for the commentary and ElevenLabs for the voice.

## Prerequisites

- Python 3.9+
- Minecraft 1.21.1 with the PAL mod (or anything writing the same JSONL format)
- A Google Gemini API key
- An ElevenLabs API key (you can add several) and a Voice ID
- An audio player: `ffplay` (from ffmpeg), `mpv`, or `mpg123`

```bash
pip install -r requirements.txt
python ai_minecraft_bot.py
```

## Getting API keys

**Gemini** — [Google AI Studio](https://aistudio.google.com/app/apikey) → Create API key.
Free tier is generous enough for personal use.

**ElevenLabs** *(optional — see Voice below)* — [elevenlabs.io](https://elevenlabs.io/) →
Profile Settings for the key. Free tier is 10,000 characters/month, which is why replies are
capped at 1–2 sentences.

## Voice

Three backends, switched with `TTS_BACKEND`:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | Local Chatterbox if its server answers → ElevenLabs → Edge |
| `chatterbox` | Local GPU model, emotional. Falls back to Edge if the server dies mid-session |
| `elevenlabs` | ElevenLabs, falling back to Edge |
| `edge` | Edge only — free, no key, no character cap |

### Chatterbox (local, GPU, emotional) — recommended

[Chatterbox Turbo](https://www.resemble.ai/learn/models/chatterbox-turbo) is a 350M-parameter MIT
model with **emotion exaggeration control**, ~75 ms latency and 6× realtime on a consumer GPU.
Run it with [Chatterbox-TTS-Server](https://github.com/devnen/Chatterbox-TTS-Server):

```bash
git clone https://github.com/devnen/Chatterbox-TTS-Server
cd Chatterbox-TTS-Server && ./start.sh     # auto-detects the GPU, installs deps
```

It listens on `http://localhost:8004` — the default `CHATTERBOX_URL`. The bot probes it once at
startup and silently uses another backend if it is not running.

**Autostart:** menu `Voice → 6` lets the bot start the server itself. Point it at the cloned
folder and it will launch `server.py` (using the server's own virtualenv if there is one), wait
until the model is loaded, then stop it again when you quit. It only stops a server it started —
if you already had one running, it is left alone.

| Setting | Meaning |
|---|---|
| `CHATTERBOX_MODE` | `predefined` (built-in voice) or `clone` (your own sample) |
| `CHATTERBOX_VOICE` | Voice id, or the reference audio filename when cloning |
| `CHATTERBOX_EXAGGERATION` | **0.25–2.0**. 0.5 neutral, 0.4–0.6 conversational, higher is dramatic. Values outside the range are clamped |
| `CHATTERBOX_CFG_WEIGHT` | **0.2–1.0**. Lower slows the delivery and sticks closer to the reference voice |
| `CHATTERBOX_URGENT_BOOST` | Multiplier applied on `CRITICAL` events |

**Tuning the two together.** The Chatterbox docs are explicit that these are not independent:
for expressive delivery, raise `exaggeration` to 0.7+ *and* lower `cfg_weight` to about 0.3 —
a high exaggeration speeds the voice up, and a lower cfg_weight slows it back down. Raising
exaggeration alone gives a fast, rushed read.

The ranges come from the server's own validation, which took them from Chatterbox's Gradio app.
Out-of-range values are not rejected outright, they just make output unpredictable, so the bot
clamps them.

**Performance cues.** Chatterbox Turbo performs inline tags, in **square brackets**:
`[laugh]` `[chuckle]` `[sigh]` `[gasp]` `[groan]` `[cough]` `[clear throat]` `[sniff]` `[shush]`.
Any other syntax is read out loud instead, so the bot rewrites `<laugh>` and `(laughs)` to
`[laugh]` before sending, and strips cues entirely for Edge and ElevenLabs, which do not support
them. When the server is live the prompt invites the model to use one per reply.

Set `CHATTERBOX_USE_TAGS=false` to disable the whole mechanism.

Note that audio length is a bad way to test this — a performed sigh lasts about as long as the
spoken word "sigh". Use `test_tags.py`, which plays each syntax next to the spoken-word
reference so you can judge by ear.

**Measured on an RTX 3060 Ti:** ~0.5 s per line once warm (the very first call takes ~8 s while
CUDA warms up), and **4.8 GB of VRAM held for as long as the server runs**. That is the real
constraint on an 8 GB card — see below.

**The emotion follows the event.** A death or a lit creeper is `CRITICAL`, so the bot multiplies
the exaggeration by `CHATTERBOX_URGENT_BOOST` for that line — the same sentence gets delivered
with more panic than a routine mining summary. This is why the mod tags events with a level in
the first place.

### VRAM budget

The server holds ~4.8 GB for as long as it runs. On an 8 GB card that leaves roughly 3 GB for
everything else, and desktop apps eat into it more than people expect (Discord ~0.6 GB, a
Chromium browser ~0.3 GB, VS Code ~0.1 GB). Minecraft itself wants 1-1.5 GB vanilla, considerably
more with shaders.

If Minecraft stutters or fails to allocate: close the browser and Discord first, skip shaders,
and keep the render distance moderate. If it still does not fit, set `TTS_BACKEND=edge` while you
play — Edge uses no VRAM at all.

**Install note:** the server pins `torch==2.5.1+cu121`, which has no build for Python 3.13+. On a
distro whose default `python3` is newer, `./start.sh` fails with *"No matching distribution found
for torch"*. Run the launcher with an older interpreter instead — it creates its venv from
whichever Python starts it:

```bash
rm -rf venv && python3.11 start.py --nvidia
```

## Testing the voice

`test_voice.py` speaks sample commentary without needing Gemini or Minecraft running:

```bash
python test_voice.py              # sample lines on the configured backend
python test_voice.py --compare    # the same line through every backend, to A/B them
python test_voice.py --emotions   # sweep the exaggeration dial to pick your values
python test_voice.py --text "..." # your own line
```

The **Voice** menu also has *Hear the current voice*, which speaks one routine line and one
critical line with your exact settings — the quickest way to check a voice and hear what the
emotion boost actually does.

`--emotions` is the one to use for tuning: it says the same death line at exaggeration 0.5, 1.0,
1.4 and 2.0 so you can hear the difference and choose `CHATTERBOX_EXAGGERATION` plus
`CHATTERBOX_URGENT_BOOST`. It never writes to your `.env`.

**Edge TTS** uses the neural voices behind Microsoft Edge's Read Aloud. No API key, no quota.
Pick a voice with `EDGE_VOICE` (`en-US-AriaNeural`, `en-US-GuyNeural`, `fr-FR-DeniseNeural`,
`fr-FR-HenriNeural`, …); run `edge-tts --list-voices` to see all ~200.

**ElevenLabs on the free plan** has two traps:

1. **Voice Library voices return HTTP 402.** Only the "Default" (premade) voices work over the
   API without a paid plan. Menu option **Voice → 7** lists what your account can
   actually use — anything marked `premade` is safe — and lets you switch to one in one step.
2. **10,000 characters/month is roughly 10 minutes of speech**, so a single long session can
   exhaust it. When that happens in `auto` mode the bot switches to Edge and keeps talking
   instead of going quiet.

ElevenLabs has also announced that the current Default voices expire on **2026-12-31**, so
`edge` is the safer long-term default.

## How it decides when to speak

The mod tags every event with a priority, and the bot acts on it:

| Level | Behaviour |
|---|---|
| `CRITICAL` | Sent immediately and **interrupts** any clip currently playing (death, low health, lit creeper) |
| `NOTABLE` | Triggers a send, debounced a few seconds so a burst becomes one comment |
| `INFO` | Accumulated and sent when the idle timer fires |

Every send also includes the latest `scene` line, so the AI always knows where you are and what
time it is, not just what you did.

## Settings

Editable from the Settings menu (stored in `.env`):

| Setting | Default | Notes |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Chosen for its free-tier rate limits, which are more forgiving during a busy session. Google retires older model ids fairly aggressively; if you get a 404 the error message names the current replacement |
| `TTS_BACKEND` | `auto` | `auto` / `chatterbox` / `elevenlabs` / `edge` |
| `EDGE_VOICE` | `en-US-AriaNeural` | Free backend voice |
| `CHATTERBOX_*` | see Voice section | Local emotional TTS |
| `ELEVENLABS_MODEL` | `eleven_turbo_v2_5` | |
| `SYSTEM_PROMPT` | tsundere commentator | Your personality prompt. Output rules (length, no coordinates read aloud) are appended automatically, so you can rewrite this freely. |
| `SEND_INTERVAL` | `45` | Idle timer, seconds |
| `NOTABLE_DEBOUNCE` | `3` | Grouping window for NOTABLE events |
| `CHECK_INTERVAL` | `0.5` | How often the log is polled |
| `MAX_CHARS_PER_SEND` | `2000` | Budget per request; INFO lines are dropped first when over |
| `HISTORY_TURNS` | `12` | Sliding conversation window, keeps token cost flat over a long session |

A lower `SEND_INTERVAL` or a bigger model burns through your API quotas much faster.

## How it reads the log

The log is tailed by **byte offset**, not by re-reading the whole file, and the bot detects when
Minecraft restarts and truncates it — it resynchronises instead of going silent. Torn lines
(caught mid-write) are held until complete, and unparseable lines are skipped rather than fatal.

## Troubleshooting

**No log file found** — check the directory points at `logs/player_actions` inside your Minecraft
folder, and that you have joined a world at least once with the mod installed.

**No audio** — install ffmpeg (`sudo pacman -S ffmpeg`, `sudo apt install ffmpeg`). The bot warns
at startup if it cannot find a player.

**ElevenLabs errors** — a 401 or 429 makes the bot rotate to your next key automatically. If every
key fails it keeps commenting in the terminal without audio.

## Security

`.env` and `elevenlabs_keys.json` hold your keys in plain text. Both are gitignored. Do not commit
them, and do not paste them into issues.
