import os
import time
import requests
from google import generativeai as genai
from pathlib import Path
import tempfile
import platform
import subprocess
import json
from datetime import datetime
from dotenv import load_dotenv, set_key

# ==================== CONFIGURATION ====================
ENV_FILE = '.env'
CHAT_HISTORY_DIR = 'chat_history'

class Config:
    """Configuration manager with .env file support"""
    
    def __init__(self):
        self.load_env()
    
    def load_env(self):
        """Load or create .env file"""
        if not os.path.exists(ENV_FILE):
            self._create_default_env()
        load_dotenv(ENV_FILE)
    
    def _create_default_env(self):
        """Create default .env file"""
        defaults = {
            'GEMINI_API_KEY': '',
            'ELEVENLABS_API_KEY': '',
            'VOICE_ID': '',
            'LOG_DIRECTORY': '',
            'GEMINI_MODEL': 'gemini-2.0-flash',
            'SYSTEM_PROMPT': 'context: Currently you only receive the logs of the users actions, you must act as if you were seeing their Minecraft gameplay directly. personality: you act like a tsundere and react to what the user is doing dont hesitate to trash them when they do something bad',
            'CHECK_INTERVAL': '1',
            'SEND_INTERVAL': '30'
        }
        
        with open(ENV_FILE, 'w') as f:
            for key, value in defaults.items():
                f.write(f'{key}={value}\n')
    
    def get(self, key, default=''):
        return os.getenv(key, default)
    
    def set(self, key, value):
        set_key(ENV_FILE, key, value)
        os.environ[key] = value
    
    def is_configured(self):
        required = ['GEMINI_API_KEY', 'ELEVENLABS_API_KEY', 'VOICE_ID', 'LOG_DIRECTORY']
        return all(self.get(key) for key in required)


# ==================== CHAT HISTORY MANAGER ====================
class ChatHistoryManager:
    """Manage chat history saving and loading"""
    
    def __init__(self):
        if not os.path.exists(CHAT_HISTORY_DIR):
            os.makedirs(CHAT_HISTORY_DIR)
    
    def save_chat(self, chat_history, name=None):
        """Save chat history to file"""
        if name is None:
            name = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        filepath = os.path.join(CHAT_HISTORY_DIR, f'{name}.json')
        
        # Convert history to serializable format
        serializable_history = []
        for msg in chat_history:
            serializable_history.append({
                'role': msg.role,
                'parts': [part.text for part in msg.parts]
            })
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(serializable_history, f, indent=2, ensure_ascii=False)
        
        print(f"[SAVE] Chat history saved to: {filepath}")
        return filepath
    
    def list_saved_chats(self):
        """List all saved chat histories"""
        files = [f for f in os.listdir(CHAT_HISTORY_DIR) if f.endswith('.json')]
        return sorted(files, reverse=True)
    
    def load_chat(self, filename):
        """Load chat history from file"""
        filepath = os.path.join(CHAT_HISTORY_DIR, filename)
        
        if not os.path.exists(filepath):
            print(f"[ERROR] Chat history file not found: {filepath}")
            return None
        
        with open(filepath, 'r', encoding='utf-8') as f:
            history = json.load(f)
        
        print(f"[LOAD] Chat history loaded from: {filepath}")
        return history


# ==================== FILE WATCHER ====================
class LogFileWatcher:
    """Watch for log files and monitor changes"""
    
    def __init__(self, directory):
        self.directory = directory
        self.log_file = None
        self.last_content = ""
    
    def find_log_file(self):
        """Find first .log file in directory"""
        if not os.path.exists(self.directory):
            return None
        
        for file in os.listdir(self.directory):
            if file.endswith('.log'):
                return os.path.join(self.directory, file)
        return None
    
    def wait_for_log_file(self):
        """Wait until a log file appears"""
        print(f"[WAIT] Waiting for .log file in: {self.directory}")
        while True:
            log_file = self.find_log_file()
            if log_file:
                self.log_file = log_file
                print(f"[FOUND] Log file found: {log_file}")
                return log_file
            time.sleep(1)
    
    def read_file(self):
        """Read log file content"""
        if not self.log_file or not os.path.exists(self.log_file):
            return ""
        
        try:
            with open(self.log_file, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            print(f"[ERROR] Error reading file: {e}")
            return ""


# ==================== AI HANDLER ====================
class AIHandler:
    """Handle Gemini AI and ElevenLabs TTS"""
    
    def __init__(self, config):
        self.config = config
        self.model = None
        self.chat = None
        self.chat_manager = ChatHistoryManager()
    
    def init_gemini(self, chat_history=None):
        """Initialize Gemini API"""
        genai.configure(api_key=self.config.get('GEMINI_API_KEY'))
        
        self.model = genai.GenerativeModel(
            model_name=self.config.get('GEMINI_MODEL'),
            system_instruction=self.config.get('SYSTEM_PROMPT')
        )
        
        # Start chat with history if provided
        history = []
        if chat_history:
            history = chat_history
        
        self.chat = self.model.start_chat(history=history)
        print("[INIT] Gemini chat initialized!")
    
    def send_message(self, text):
        """Send message to Gemini and synthesize response"""
        if not self.chat:
            print("[ERROR] Chat not initialized!")
            return
        
        try:
            print(f"\n[SEND] Sending to Gemini...")
            response = self.chat.send_message(text)
            
            if not response or not hasattr(response, 'text'):
                print("[ERROR] Invalid Gemini response")
                return
            
            gemini_response = response.text
            print(f"[GEMINI] Response: {gemini_response[:100]}...")
            
            # Synthesize and play audio
            self._synthesize_and_play(gemini_response)
            
        except Exception as e:
            print(f"[ERROR] Error in send_message(): {e}")
    
    def _synthesize_and_play(self, text):
        """Synthesize text to speech and play"""
        print(f"[ELEVENLABS] Synthesizing speech...")
        
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.config.get('VOICE_ID')}"
        
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.config.get('ELEVENLABS_API_KEY')
        }
        
        data = {
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.5
            }
        }
        
        try:
            response = requests.post(url, json=data, headers=headers)
            
            if response.status_code == 200:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
                    tmp_file.write(response.content)
                    mp3_path = tmp_file.name
                
                print(f"[AUDIO] Playing audio...")
                self._play_audio(mp3_path)
                os.remove(mp3_path)
                print(f"[SUCCESS] Audio played successfully!\n")
            else:
                print(f"[ERROR] ElevenLabs error: {response.status_code} - {response.text}")
        
        except Exception as e:
            print(f"[ERROR] Error synthesizing speech: {e}")
    
    def _play_audio(self, file_path):
        """Play audio file based on OS"""
        system = platform.system()
        
        try:
            if system == "Windows":
                os.startfile(file_path)
                time.sleep(5)
            elif system == "Darwin":
                subprocess.run(["afplay", file_path])
            elif system == "Linux":
                subprocess.run(["mpg123", file_path])
            else:
                print(f"[WARNING] Unsupported OS for audio playback: {system}")
        except Exception as e:
            print(f"[ERROR] Error playing audio: {e}")
    
    def save_chat_history(self):
        """Save current chat history"""
        if self.chat and hasattr(self.chat, 'history'):
            return self.chat_manager.save_chat(self.chat.history)
        return None


# ==================== SETUP WIZARD ====================
class SetupWizard:
    """Interactive setup wizard"""
    
    def __init__(self, config):
        self.config = config
    
    def run_initial_setup(self):
        """Run initial setup if not configured"""
        print("\n" + "=" * 60)
        print("INITIAL SETUP")
        print("=" * 60)
        
        # API Keys
        if not self.config.get('GEMINI_API_KEY'):
            api_key = input("Enter your Gemini API Key: ").strip()
            self.config.set('GEMINI_API_KEY', api_key)
        
        if not self.config.get('ELEVENLABS_API_KEY'):
            api_key = input("Enter your ElevenLabs API Key: ").strip()
            self.config.set('ELEVENLABS_API_KEY', api_key)
        
        if not self.config.get('VOICE_ID'):
            voice_id = input("Enter your ElevenLabs Voice ID: ").strip()
            self.config.set('VOICE_ID', voice_id)
        
        # Log directory
        if not self.config.get('LOG_DIRECTORY'):
            log_dir = input("Enter the full path to the directory containing .log files: ").strip()
            self.config.set('LOG_DIRECTORY', log_dir)
        
        print("\n[SUCCESS] Initial setup complete!")
    
    def show_advanced_settings(self):
        """Show and edit advanced settings"""
        print("\n" + "=" * 60)
        print("ADVANCED SETTINGS")
        print("=" * 60)
        print("1. Gemini Model:", self.config.get('GEMINI_MODEL'))
        print("2. System Prompt:", self.config.get('SYSTEM_PROMPT')[:50] + "...")
        print("3. Check Interval:", self.config.get('CHECK_INTERVAL'), "seconds")
        print("4. Send Interval:", self.config.get('SEND_INTERVAL'), "seconds")
        print("5. Back to main menu")
        
        choice = input("\nSelect option to edit (1-5): ").strip()
        
        if choice == '1':
            model = input("Enter Gemini model name: ").strip()
            if model:
                self.config.set('GEMINI_MODEL', model)
        elif choice == '2':
            prompt = input("Enter system prompt: ").strip()
            if prompt:
                self.config.set('SYSTEM_PROMPT', prompt)
        elif choice == '3':
            interval = input("Enter check interval (seconds): ").strip()
            if interval.isdigit():
                self.config.set('CHECK_INTERVAL', interval)
        elif choice == '4':
            interval = input("Enter send interval (seconds): ").strip()
            if interval.isdigit():
                self.config.set('SEND_INTERVAL', interval)


# ==================== MAIN APPLICATION ====================
class MinecraftAICommentator:
    """Main application"""
    
    def __init__(self):
        self.config = Config()
        self.wizard = SetupWizard(self.config)
        self.ai_handler = None
        self.log_watcher = None
        self.last_send_time = time.time()
    
    def run(self):
        """Main entry point"""
        print("=" * 60)
        print("MINECRAFT AI COMMENTATOR")
        print("=" * 60)
        
        # Initial setup if needed
        if not self.config.is_configured():
            self.wizard.run_initial_setup()
        
        # Main menu
        while True:
            print("\n" + "=" * 60)
            print("MAIN MENU")
            print("=" * 60)
            print("1. Start AI Commentator")
            print("2. Configure Settings")
            print("3. Exit")
            
            choice = input("\nSelect option (1-3): ").strip()
            
            if choice == '1':
                self._start_commentator()
            elif choice == '2':
                self.wizard.show_advanced_settings()
            elif choice == '3':
                print("\n[EXIT] Goodbye!")
                break
    
    def _start_commentator(self):
        """Start the AI commentator"""
        print("\n" + "=" * 60)
        print("STARTING AI COMMENTATOR")
        print("=" * 60)
        
        # Initialize AI handler
        self.ai_handler = AIHandler(self.config)
        
        # Ask about loading previous chat
        chat_manager = ChatHistoryManager()
        saved_chats = chat_manager.list_saved_chats()
        
        chat_history = None
        if saved_chats:
            print(f"\n[INFO] Found {len(saved_chats)} saved chat(s)")
            print("0. Start fresh chat")
            for i, chat_file in enumerate(saved_chats[:10], 1):
                print(f"{i}. {chat_file}")
            
            choice = input("\nSelect chat to load (0 for fresh): ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(saved_chats):
                chat_history = chat_manager.load_chat(saved_chats[int(choice) - 1])
        
        # Initialize Gemini
        self.ai_handler.init_gemini(chat_history)
        
        # Initialize log watcher
        log_dir = self.config.get('LOG_DIRECTORY')
        self.log_watcher = LogFileWatcher(log_dir)
        
        # Wait for log file if needed
        if not self.log_watcher.find_log_file():
            self.log_watcher.wait_for_log_file()
        else:
            self.log_watcher.log_file = self.log_watcher.find_log_file()
            print(f"[FOUND] Using log file: {self.log_watcher.log_file}")
        
        # Initialize last content
        self.log_watcher.last_content = self.log_watcher.read_file()
        
        # Main monitoring loop
        self._monitoring_loop()
    
    def _monitoring_loop(self):
        """Main monitoring loop"""
        print("\n[START] Monitoring started...")
        print(f"Check interval: {self.config.get('CHECK_INTERVAL')}s")
        print(f"Auto-send interval: {self.config.get('SEND_INTERVAL')}s")
        print("Triggers: CHAT or IMPORTANT in last line")
        print("Press Ctrl+C to stop\n")
        
        try:
            while True:
                current_content = self.log_watcher.read_file()
                
                if current_content != self.log_watcher.last_content:
                    lines = current_content.strip().split('\n')
                    
                    if lines:
                        last_line = lines[-1]
                        
                        # Check for CHAT or IMPORTANT in last line
                        last_line_upper = last_line.upper()
                        if "CHAT" in last_line_upper or "IMPORTANT" in last_line_upper:
                            trigger = "CHAT" if "CHAT" in last_line_upper else "IMPORTANT"
                            print(f"\n[TRIGGER] Detected '{trigger}' in last line!")
                            
                            old_lines = self.log_watcher.last_content.strip().split('\n') if self.log_watcher.last_content else []
                            new_lines = lines[len(old_lines):]
                            
                            if new_lines:
                                difference = '\n'.join(new_lines)
                                print(f"[DIFF] New content:\n{difference}\n")
                                self.ai_handler.send_message(difference)
                                self.last_send_time = time.time()
                            
                            self.log_watcher.last_content = current_content
                
                # Auto-send timer
                time_since_last_send = time.time() - self.last_send_time
                send_interval = int(self.config.get('SEND_INTERVAL'))
                
                if time_since_last_send >= send_interval:
                    print(f"\n[TIMER] {send_interval}s elapsed - Auto-send")
                    
                    old_lines = self.log_watcher.last_content.strip().split('\n') if self.log_watcher.last_content else []
                    current_lines = current_content.strip().split('\n') if current_content else []
                    
                    if len(current_lines) > len(old_lines):
                        new_lines = current_lines[len(old_lines):]
                        difference = '\n'.join(new_lines)
                        
                        if difference.strip():
                            print(f"[DIFF] New content:\n{difference}\n")
                            self.ai_handler.send_message(difference)
                    else:
                        print("[INFO] No new content to send.")
                    
                    self.last_send_time = time.time()
                    self.log_watcher.last_content = current_content
                
                time.sleep(int(self.config.get('CHECK_INTERVAL')))
        
        except KeyboardInterrupt:
            print("\n[STOP] Interrupted by user")
        
        finally:
            # Save chat history
            print("\n[SAVE] Saving chat history...")
            self.ai_handler.save_chat_history()
            print("\n[DONE] Monitoring stopped")


# ==================== ENTRY POINT ====================
if __name__ == "__main__":
    app = MinecraftAICommentator()
    app.run()
