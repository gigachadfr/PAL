# Minecraft AI Commentator

An AI-powered commentator bot that watches your Minecraft gameplay logs and provides real-time commentary with voice synthesis. The bot uses Google's Gemini AI for intelligent responses and ElevenLabs for text-to-speech conversion.

## Features

- 📝 Real-time monitoring of Minecraft log files
- 🤖 AI-powered commentary using Google Gemini
- 🔊 Voice synthesis with ElevenLabs TTS
- 💾 Chat history saving and loading
- ⚙️ Configurable system prompts and intervals
- 🎯 Trigger-based responses (CHAT and IMPORTANT keywords)
- ⏰ Automatic timed updates

## Prerequisites

- Python 3.8 or higher
- Minecraft 1.20.4 with the playeractionlogger mod or any mod that do a transcript with the same format
- Google Gemini API key
- ElevenLabs API key(you can add as many as you want) and Voice ID

## Getting API Keys

### Google Gemini API Key

1. Visit [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Sign in with your Google account
3. Click "Create API Key"
4. Copy your API key and save it securely

**Note:** Gemini API has a free tier with generous limits for personal use.

### ElevenLabs API Key and Voice ID

1. Visit [ElevenLabs](https://elevenlabs.io/)
2. Sign up for a free account
3. Navigate to your [Profile Settings](https://elevenlabs.io/app/settings)
4. Find your API key under the "API Key" section
5. To get a Voice ID:
   - Go to [Voice Library](https://elevenlabs.io/app/voice-library)
   - Choose a voice or create your own
   - Click on the voice and copy the Voice ID from the URL or settings

**Note:** Free tier only includes 10,000 characters per month.

## Configuration

On first run, the application will guide you through an interactive setup wizard where you'll need to provide:

1. **Gemini API Key** - Your Google Gemini API key
2. **ElevenLabs API Key** - Your ElevenLabs API key
3. **Voice ID** - The ElevenLabs voice ID you want to use
4. **Log Directory** - Full path to the directory containing Minecraft `.log` files

### Advanced Settings

You can customize:

- **Gemini Model** - Default: `gemini-2.0-flash`
- **ElevenLabs Model** - Default: `Turbo V2.5`
- **System Prompt** - Customize the AI's personality and behavior
- **Check Interval** - How often to check for log changes (seconds)
- **Send Interval** - Auto-send interval for updates (seconds)

- **Multiple Eleven Labs API keys** - change when the current one is not working

## Usage

1. Run the application:
   ```bash
   pip install requirements.txt
   python ai_minecraft_bot.py
   ```

2. Select "Start AI Commentator" from the main menu

3. Choose whether to load a previous chat or start fresh

4. The bot will start monitoring your Minecraft logs

5. The AI will respond when:
   - Keywords "CHAT" or "IMPORTANT" appear in new log lines
   - The auto-send interval timer triggers

6. Press `Ctrl+C` to stop monitoring (chat history will be saved automatically)

## How It Works

1. **Log Monitoring**: The bot continuously watches your specified log directory for `.log` files
2. **Change Detection**: When new content is detected, it extracts the difference
3. **AI Processing**: New content is sent to Gemini AI with your custom system prompt
4. **Voice Synthesis**: The AI's response is converted to speech using ElevenLabs
5. **Audio Playback**: The generated audio is automatically played
6. **History Saving**: All conversations are saved for future reference

## Chat History

Chat histories are automatically saved in the `chat_history/` directory as JSON files. You can:

- Load previous conversations to continue where you left off
- Review past interactions
- Build upon previous context

## Audio Playback

The application supports audio playback on:

- **Windows**: Uses default media player
- **macOS**: Uses `afplay`
- **Linux**: Uses `mpg123` (ensure it's installed: `sudo apt-get install mpg123`)

## Troubleshooting

### No log file found
- Ensure Minecraft is running and generating logs
- Verify the log directory path is correct
- Check that logging is enabled in your Minecraft settings

### API Errors
- Verify your API keys are correct
- Check your API quota limits
- Ensure you have an active internet connection

### Audio not playing
- **Linux users**: Install mpg123 (`sudo apt-get install mpg123`)
- Check your system's audio settings
- Verify audio drivers are working

## Example System Prompts

**Tsundere Commentator** (default):
```
context: Currently you only receive the logs of the users actions, you must act as if you were seeing their Minecraft gameplay directly. personality: you act like a tsundere and react to what the user is doing dont hesitate to trash them when they do something bad
```

## License

This project is provided as-is for personal use.

## Contributing

Feel free to submit issues, fork the repository, and create pull requests for any improvements.

## Disclaimer

This bot requires active API keys from Google and ElevenLabs. Be mindful of your API usage limits and costs. The free tiers should be sufficient for casual use.
