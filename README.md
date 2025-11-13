# PlayerActionLogger (PAL)

**PlayerActionLogger (PAL)** is a Minecraft mod that logs the local player's actions into a `.log` file inside your Minecraft installation.  
It was originally developed for LLM usage and commentary purposes, but can be used for any type of gameplay tracking or automation.

if you made video with it please credit this project

---

## 🪶 Overview

- **Minecraft Version:** 1.20.4  
- **Mod Loader:** Fabric  
- **Log Location:** `~/(your_minecraft_install)/logs/player_actions`  
- **Main Goal:** Provide a simple way to record player actions for use with AI scripts or analysis tools.  
- **Python Script:** This repository also includes a companion script, `ai_minecraft_bot.py`, designed to read the log and interact with the game (Gemini and ElevenLabs API keys are required) — though the mod can be used independently.

here is a exemple of what it will look like
![log screen](https://github.com/gigachadfr/PAL/blob/main/screen/exemple.png?raw=true)

---

## 💡 Why I Made This

I created this mod mostly because I saw **catsdontlikecofee**'s videos ([TikTok link](https://www.tiktok.com/@iiillii11iilliill)), and since he hasn’t released his work publicly yet, I decided to make something similar myself.  
I used AI tools to help build the mod (since I don’t personally know Java), but not the Python script.  
The main idea was to build something functional for AI/LLM experiments inside Minecraft to observe and comment on gameplay based on the log.

---

## 🧱 Installation

1. Install **Fabric Loader** (for Minecraft 1.20.4).  
2. Download the latest release of **PlayerActionLogger (PAL)** from this repository or CurseForge.  
3. Place the `.jar` file into your `mods` folder inside your Minecraft directory.  
4. Launch the game — the mod will automatically start logging your actions.

---

## 🐍 Python Script Usage

All configuration is handled through a `.env` file located next to the Python script.

1. Install the Python script and run it once.  
2. Set your API keys for **Gemini AI** and **ElevenLabs**.  
3. Set the **AI voice ID** for ElevenLabs — you can find it on their site. (I personally use `WtA85syCrJwasGeHGH2p`.)  
4. Set the directory where your `.log` file is or will be created.  
5. Have fun!

You can tweak all settings (such as auto-send cooldown),  
but keep in mind that using a lower cooldown or a better AI model will consume your API limits much faster.

---

## 🧾 License

This project is licensed under the **MIT License**.  
Feel free to use, modify, and share it — attribution is appreciated.
