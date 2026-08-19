"""
Are Chatterbox's paralinguistic tags actually performed, or just read out loud?

Measuring audio length cannot answer this — a spoken "sigh" and a performed sigh both make the
clip longer. Only your ears can. This plays each variant of the same sentence so you can judge.

    python test_tags.py                 # play every variant, laugh + sigh
    python test_tags.py --tag gasp      # try another tag
    python test_tags.py --text "..."    # your own sentence
    python test_tags.py --keep          # leave the mp3 files in ./tag_samples/

What to listen for:
    The "bare word" variant is your reference — that is what it sounds like when the model
    SPEAKS the tag. If a bracket variant sounds identical to it, the tag is not working.
    If instead you hear an actual laugh or sigh, that syntax is the right one.
"""

import argparse
import sys
import time
from pathlib import Path

import requests

from ai_minecraft_bot import AudioPlayer, Config

SAMPLE_DIR = Path("tag_samples")
DEFAULT_SENTENCE = "Oh no you did not."


def variants(tag, sentence):
    """Every plausible syntax, plus the two references that make the result interpretable."""
    return [
        ("baseline, no tag at all", sentence),
        (f"bare word '{tag}'  <-- REFERENCE: this is the tag being SPOKEN", f"{tag} {sentence}"),
        (f"[{tag}]  square brackets (documented syntax)", f"[{tag}] {sentence}"),
        (f"<{tag}>  angle brackets", f"<{tag}> {sentence}"),
        (f"({tag}s)  parentheses", f"({tag}s) {sentence}"),
        (f"[{tag}] at the end", f"{sentence} [{tag}]"),
    ]


def synth(config, text):
    base = config.get("CHATTERBOX_URL").rstrip("/")
    payload = {
        "text": text,
        "voice_mode": config.get("CHATTERBOX_MODE") or "predefined",
        "output_format": "mp3",
        "exaggeration": float(config.get("CHATTERBOX_EXAGGERATION")),
        "temperature": float(config.get("CHATTERBOX_TEMPERATURE")),
        "cfg_weight": float(config.get("CHATTERBOX_CFG_WEIGHT")),
        "speed_factor": 1.0,
        "language": "en",
        "stream": False,
        "split_text": False,  # keep the sentence whole so chunking cannot split a tag
        "seed": 42,           # fixed seed: differences come from the tag, not from sampling
    }
    voice = config.get("CHATTERBOX_VOICE")
    if voice:
        key = ("reference_audio_filename"
               if payload["voice_mode"] == "clone" else "predefined_voice_id")
        payload[key] = voice

    try:
        response = requests.post(f"{base}/tts", json=payload, timeout=120)
    except requests.RequestException as e:
        print(f"   [ERROR] {e}")
        return None

    if response.status_code != 200:
        print(f"   [ERROR] HTTP {response.status_code}: {response.text[:160]}")
        return None
    return response.content


def main():
    parser = argparse.ArgumentParser(description="Judge Chatterbox tag support by ear.")
    parser.add_argument("--tag", default=None, help="single tag to test (default: laugh and sigh)")
    parser.add_argument("--text", default=DEFAULT_SENTENCE, help="sentence to speak")
    parser.add_argument("--keep", action="store_true", help="keep the mp3 files")
    args = parser.parse_args()

    config = Config()
    base = config.get("CHATTERBOX_URL").rstrip("/")

    try:
        info = requests.get(f"{base}/api/model-info", timeout=5).json()
    except requests.RequestException:
        print(f"Chatterbox is not answering at {base}.")
        print("Start it first:  cd Chatterbox-TTS-Server && python3.11 start.py --nvidia")
        return 1

    print(f"Model: {info.get('type')}  |  advertises tag support: "
          f"{info.get('supports_paralinguistic_tags')}")
    print(f"Voice: {config.get('CHATTERBOX_VOICE') or '(server default)'}")

    player = AudioPlayer()
    if not player.available():
        print("\n[WARN] No audio player found — install ffmpeg. You need to HEAR this test.")
        return 1

    SAMPLE_DIR.mkdir(exist_ok=True)
    tags = [args.tag] if args.tag else ["laugh", "sigh"]

    for tag in tags:
        print(f"\n{'=' * 66}\n  TAG: {tag}\n{'=' * 66}")

        for label, text in variants(tag, args.text):
            print(f"\n  {label}")
            print(f"    sent: {text!r}")

            audio = synth(config, text)
            if not audio:
                continue

            path = SAMPLE_DIR / f"{tag}_{label.split()[0].strip('[<(')}.mp3"
            path.write_bytes(audio)

            player.play(audio)
            player.queue.join()
            time.sleep(0.4)  # a beat between samples so they do not blur together

    print(f"\n{'=' * 66}")
    print("Verdict is yours:")
    print("  - If the bracket variants sound like the BARE WORD reference,")
    print("    the tags are not supported -> leave CHATTERBOX_USE_TAGS=false.")
    print("  - If one of them produced a real laugh/sigh, tell me which syntax,")
    print("    and I will switch the prompt to it and enable the feature.")

    if args.keep:
        print(f"\nSamples kept in ./{SAMPLE_DIR}/")
    else:
        for f in SAMPLE_DIR.glob("*.mp3"):
            f.unlink()
        SAMPLE_DIR.rmdir()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
