"""
Voice bench for the commentator — no Gemini, no Minecraft, no API cost on the local backend.

Two things it answers:
  1. Does my TTS backend actually work?
  2. What do CHATTERBOX_EXAGGERATION / CHATTERBOX_URGENT_BOOST sound like, so I can pick values?

    python test_voice.py                  # speak the sample lines on the configured backend
    python test_voice.py --backend edge   # force one backend
    python test_voice.py --compare        # same line through every available backend
    python test_voice.py --emotions       # sweep the exaggeration dial (Chatterbox only)
    python test_voice.py --text "..."     # your own line
"""

import argparse
import os
import sys
import time

from ai_minecraft_bot import TTS, AudioPlayer, Config


def override(key, value):
    """
    Changes a setting for this process only.

    Deliberately not Config.set(), which persists to .env — a test run must never silently
    rewrite the user's configuration.
    """
    os.environ[key] = str(value)

# What the bot would actually say, one line per priority level.
SAMPLES = [
    ("INFO", "Still mining. Sixty blocks of stone and not one diamond. Riveting stuff."),
    ("NOTABLE", "Oh, a diamond. Finally something worth watching you do."),
    ("CRITICAL", "Creeper! Behind you! Move, you idiot!"),
    ("CRITICAL", "And you're dead. Blown up. Again. I'm not even surprised anymore."),
]

EXAGGERATION_SWEEP = [0.5, 1.0, 1.4, 2.0]


def speak(tts, player, text, urgent=False):
    started = time.time()
    audio = tts.synthesize(text, urgent=urgent)
    elapsed = time.time() - started

    if not audio:
        print("   -> no audio produced")
        return False

    print(f"   -> {len(audio):,} bytes in {elapsed:.1f}s")
    player.play(audio)
    player.queue.join()  # wait for playback so the samples do not overlap
    return True


def run_samples(config, player, backend=None, text=None):
    if backend:
        override("TTS_BACKEND", backend)
    tts = TTS(config)
    print(f"\n=== backend: {tts.backend} ===")

    lines = [("CUSTOM", text)] if text else SAMPLES
    for level, line in lines:
        urgent = level == "CRITICAL"
        print(f"\n[{level}]{' (urgent -> more emotion)' if urgent else ''}\n   \"{line}\"")
        speak(tts, player, line, urgent=urgent)


def run_compare(config, player, text=None):
    line = text or SAMPLES[2][1]
    print(f"\nSpeaking the same line on each backend:\n   \"{line}\"")
    for backend in ("chatterbox", "elevenlabs", "edge"):
        override("TTS_BACKEND", backend)
        tts = TTS(config)
        print(f"\n--- {backend} ---")
        speak(tts, player, line, urgent=True)


def run_emotions(config, player, text=None):
    line = text or SAMPLES[3][1]
    override("TTS_BACKEND", "chatterbox")
    tts = TTS(config)

    if not tts._chatterbox_available():
        print("\nChatterbox server is not running — this mode needs it.")
        print("Start it first:  cd Chatterbox-TTS-Server && ./start.sh")
        return

    print(f"\nSame line at rising emotion levels:\n   \"{line}\"\n")
    for value in EXAGGERATION_SWEEP:
        override("CHATTERBOX_EXAGGERATION", value)
        tts = TTS(config)
        tts._chatterbox_up = True
        print(f"exaggeration = {value}")
        speak(tts, player, line)

    print(
        "\nPick the one you liked:"
        "\n  CHATTERBOX_EXAGGERATION  = your normal level"
        "\n  CHATTERBOX_URGENT_BOOST  = multiplier so CRITICAL lands on the dramatic one"
        "\n(e.g. normal 1.0 + boost 1.4 -> deaths are spoken at 1.4)"
    )


def main():
    parser = argparse.ArgumentParser(description="Test the commentator's voice.")
    parser.add_argument("--backend", choices=["auto", "chatterbox", "elevenlabs", "edge"])
    parser.add_argument("--compare", action="store_true", help="every backend, same line")
    parser.add_argument("--emotions", action="store_true", help="sweep the exaggeration dial")
    parser.add_argument("--text", help="say this instead of the samples")
    args = parser.parse_args()

    config = Config()
    player = AudioPlayer()
    if not player.available():
        print("[WARN] No audio player found — install ffmpeg (ffplay), mpv or mpg123.")
        print("       Synthesis will still be timed, you just will not hear it.")

    if args.emotions:
        run_emotions(config, player, args.text)
    elif args.compare:
        run_compare(config, player, args.text)
    else:
        run_samples(config, player, args.backend, args.text)

    player.queue.join()
    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
