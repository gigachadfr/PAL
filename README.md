# PlayerActionLogger (PAL)

**PlayerActionLogger (PAL)** is a Minecraft mod that logs the local player's actions into a `.log` file inside your Minecraft installation.  
It was originally developed for LLM usage and commentary purposes, but can be used for any type of gameplay tracking or automation.

---

## 🪶 Overview

-  **Minecraft Version:** 1.20.4  
-  **Mod Loader:** Fabric  
-  **Log Location:**  ~/(yourminecraftinstall)/logs/player_actions

-  **Main Goal:** Provide a simple way to record player actions for use with AI scripts or analysis tools.  
-  **Python Script:** This repository also includes a companion script, `ai_minecraft_bot.py`, designed to read the log and interact with the game (gemini and elevenlabs api key are required) — though the mod can be used independently.

---

## 💡 Why I Made This

I created this mod mostly because I saw catsdontlikecofee's videos (https://www.tiktok.com/@iiillii11iilliill) and this guy hasn't posted his work publicly yet so I decided to make something similar myself with the help of AI tools for the mod not the python script, as I don't personally know Java.  
The main idea was to build something functional for AI/LLM experiments inside Minecraft —  
to observe and comment gameplay based on the log

---

## 🧱 Installation

1. Install **Fabric Loader** (for Minecraft 1.20.4).  
2. Download the latest release of **PlayerActionLogger (PAL)** from this repository or CurseForge.  
3. Place the `.jar` file into your `mods` folder inside your Minecraft directory.  
4. Launch the game — the mod will automatically start logging your actions.

---

## 🐍 python script usage

all the things that you will setup will be in a .env file beside the python script

1. install the python script and launch it
2. set your api key for gemini ai and elevenlabs
3. set the ai voice id for elevenlabs you can find it on their site i personnaly use WtA85syCrJwasGeHGH2p
4. set the directory of were your file will or as been created
5. have fun

you can change all the settings like auto send cooldown but keep in mind that using low cooldown or better model will use the api limit a lot faster

---

## License

This project is licensed under the MIT License.
Feel free to use, modify, and share it — attribution is appreciated.
