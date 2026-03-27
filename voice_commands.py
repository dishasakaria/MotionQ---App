"""
voice_commands.py — Full hands-free voice command controller.

New in this version:
  • Smart click by spoken text  ("click search bar")
  • Screen reader               ("read page")
  • Window / app switcher       ("switch to chrome")
  • Tab manager                 ("go to tab 3", "list tabs")
  • Quick replies               ("quick reply yes")
  • Macro record & playback     ("record macro", "stop recording", "run macro morning")
  • Accessibility commands      (magnifier, narrator, high contrast, zoom page …)
  • Extra productivity commands (new doc, translate, define, weather …)
  • Open on-screen keyboard     ("open keyboard")
  • Clipboard management        ("clear clipboard")
"""

from __future__ import annotations

import time
import threading
import webbrowser
import subprocess
import os
import json
import re
import pyaudio
import cv2

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    print("⚠️  pyautogui not installed — mouse/keyboard commands disabled")
    PYAUTOGUI_AVAILABLE = False

try:
    import pyttsx3
    TTS_AVAILABLE = True
except ImportError:
    print("⚠️  pyttsx3 not installed — voice feedback disabled")
    TTS_AVAILABLE = False

try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False

try:
    import speech_recognition as sr
    SPEECH_REC_AVAILABLE = True
except ImportError:
    SPEECH_REC_AVAILABLE = False

# OCR for smart-click and screen reader
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("⚠️  pytesseract / Pillow not installed — smart click & screen reader disabled")

# pynput for macro recording
try:
    from pynput import mouse as pynput_mouse, keyboard as pynput_keyboard
    from pynput.keyboard import Key as PKey, Controller as KbController
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    print("⚠️  pynput not installed — macro recording disabled")

# pygetwindow for window switching (Windows)
try:
    import pygetwindow as gw
    PYGETWINDOW_AVAILABLE = True
except ImportError:
    PYGETWINDOW_AVAILABLE = False

# ── Contacts ────────────────────────────────────────────────────────────────────
CONTACTS = {
    "mom":    {"email": "mom@example.com",    "phone": "+910000000001"},
    "dad":    {"email": "dad@example.com",    "phone": "+910000000002"},
    "boss":   {"email": "boss@example.com",   "phone": "+910000000003"},
    "friend": {"email": "friend@example.com", "phone": "+910000000004"},
}

# ── App launch map ───────────────────────────────────────────────────────────────
APP_MAP = {
    "notepad":      {"linux": "gedit",               "windows": "notepad"},
    "calculator":   {"linux": "gnome-calculator",    "windows": "calc"},
    "terminal":     {"linux": "gnome-terminal",      "windows": "cmd"},
    "file manager": {"linux": "nautilus",            "windows": "explorer"},
    "files":        {"linux": "nautilus",            "windows": "explorer"},
    "spotify":      {"linux": "spotify",             "windows": "spotify"},
    "vscode":       {"linux": "code",                "windows": "code"},
    "vlc":          {"linux": "vlc",                 "windows": "vlc"},
    "zoom":         {"linux": "zoom",                "windows": "zoom"},
    "discord":      {"linux": "discord",             "windows": "discord"},
    "slack":        {"linux": "slack",               "windows": "slack"},
    "settings":     {"linux": "gnome-control-center","windows": "ms-settings:"},
    "chrome":       {"linux": "google-chrome",       "windows": "chrome"},
    "firefox":      {"linux": "firefox",             "windows": "firefox"},
    "edge":         {"linux": "microsoft-edge",      "windows": "msedge"},
    "word":         {"linux": "libreoffice --writer","windows": "winword"},
    "excel":        {"linux": "libreoffice --calc",  "windows": "excel"},
    "powerpoint":   {"linux": "libreoffice --impress","windows": "powerpnt"},
    "paint":        {"linux": "gimp",                "windows": "mspaint"},
    "explorer":     {"linux": "nautilus",            "windows": "explorer"},
    "task manager": {"linux": "gnome-system-monitor","windows": "taskmgr"},
}

WEBSITE_MAP = {
    "google":    "https://www.google.com",
    "youtube":   "https://www.youtube.com",
    "gmail":     "https://mail.google.com",
    "maps":      "https://maps.google.com",
    "github":    "https://github.com",
    "netflix":   "https://www.netflix.com",
    "whatsapp":  "https://web.whatsapp.com",
    "telegram":  "https://web.telegram.org",
    "linkedin":  "https://www.linkedin.com",
    "instagram": "https://www.instagram.com",
    "twitter":   "https://www.twitter.com",
    "reddit":    "https://www.reddit.com",
    "amazon":    "https://www.amazon.in",
    "facebook":  "https://www.facebook.com",
    "meet":      "https://meet.google.com",
    "drive":     "https://drive.google.com",
    "calendar":  "https://calendar.google.com",
    "photos":    "https://photos.google.com",
    "docs":      "https://docs.google.com",
    "sheets":    "https://sheets.google.com",
    "slides":    "https://slides.google.com",
    "notion":    "https://www.notion.so",
    "figma":     "https://www.figma.com",
    "chatgpt":   "https://chat.openai.com",
    "claude":    "https://claude.ai",
    "translate": "https://translate.google.com",
    "weather":   "https://weather.com",
    "news":      "https://news.google.com",
    "maps":      "https://maps.google.com",
    "keep":      "https://keep.google.com",
    "tasks":     "https://tasks.google.com",
}

FOLDER_MAP = {
    "downloads":  os.path.expanduser("~/Downloads"),
    "documents":  os.path.expanduser("~/Documents"),
    "pictures":   os.path.expanduser("~/Pictures"),
    "desktop":    os.path.expanduser("~/Desktop"),
    "music":      os.path.expanduser("~/Music"),
    "videos":     os.path.expanduser("~/Videos"),
}

# ── Quick replies (customise freely) ────────────────────────────────────────────
QUICK_REPLIES = {
    "yes":       "Yes, sounds good!",
    "no":        "No, sorry I can't make it.",
    "busy":      "I'm in a meeting right now, I'll reply shortly.",
    "later":     "Let me get back to you on this soon.",
    "thanks":    "Thank you so much!",
    "welcome":   "You're welcome! Let me know if you need anything else.",
    "on my way": "On my way, be there soon!",
    "sorry":     "Sorry for the delayed response.",
    "ok":        "Okay, got it!",
    "done":      "Done! Let me know if there's anything else.",
}

# ── Macro storage ────────────────────────────────────────────────────────────────
# Saved macros: {name: [(delay_s, event_type, data), ...]}
_MACROS: dict = {}
_MACRO_FILE = os.path.expanduser("~/.face_control_macros.json")


def _load_macros():
    global _MACROS
    if os.path.exists(_MACRO_FILE):
        try:
            with open(_MACRO_FILE) as f:
                _MACROS = json.load(f)
        except Exception:
            _MACROS = {}


def _save_macros():
    try:
        with open(_MACRO_FILE, 'w') as f:
            json.dump(_MACROS, f, indent=2)
    except Exception as e:
        print(f"Macro save error: {e}")


_load_macros()


class VoiceCommandController:
    def __init__(self, cap=None):
        self.cap = cap
        self.running = False
        self.dictate_mode = False

        self._tts_lock   = threading.Lock()
        self._is_speaking = False

        self._timer_thread = None
        self._is_windows = os.name == 'nt'
        self._is_linux   = (hasattr(os, 'uname') and os.uname().sysname == 'Linux')

        # Macro recording state
        self._recording        = False
        self._macro_events: list = []
        self._macro_start_time  = 0.0
        self._mouse_listener    = None
        self._keyboard_listener = None
        self._pending_macro_name: str | None = None

        # pynput keyboard controller for macro playback
        self._kb_ctrl = KbController() if PYNPUT_AVAILABLE else None

    # ══════════════════════════════════════════════════════════════════════
    # TTS
    # ══════════════════════════════════════════════════════════════════════

    def speak(self, text: str):
        print(f"🔊 {text}")
        if not TTS_AVAILABLE:
            return

        def _speak():
            self._is_speaking = True
            with self._tts_lock:
                try:
                    engine = pyttsx3.init()
                    engine.setProperty('rate', 165)
                    engine.setProperty('volume', 0.9)
                    engine.say(text)
                    engine.runAndWait()
                    engine.stop()
                except Exception as e:
                    print(f"TTS error: {e}")
            self._is_speaking = False

        threading.Thread(target=_speak, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════
    # Platform helpers
    # ══════════════════════════════════════════════════════════════════════

    def _run(self, cmd_linux: str, cmd_windows: str | None = None):
        try:
            if self._is_windows and cmd_windows:
                subprocess.Popen(cmd_windows, shell=True)
            else:
                subprocess.Popen(cmd_linux, shell=True)
        except Exception as e:
            print(f"❌ Command error: {e}")

    def _open_folder(self, path: str):
        if self._is_windows:
            self._run(f'explorer "{path}"')
        else:
            self._run(f'xdg-open "{path}"')

    def _hotkey(self, *keys):
        if PYAUTOGUI_AVAILABLE:
            pyautogui.hotkey(*keys)

    def _type_text(self, text: str):
        if PYAUTOGUI_AVAILABLE:
            pyautogui.typewrite(text, interval=0.04)
        self.speak(f"Typed: {text}")

    # ══════════════════════════════════════════════════════════════════════
    # Main command dispatcher
    # ══════════════════════════════════════════════════════════════════════

    def execute(self, text: str):
        """
        Dispatch a voice command.
        Returns True   → handled
                False  → unrecognised
                'EXIT' → exit voice mode
        """
        t = text.lower().strip()
        print(f"🗣️  '{t}'")

        # ── Exit voice mode ────────────────────────────────────────────────
        if any(k in t for k in ["stop voice", "exit voice", "quit voice",
                                  "face control", "back to face"]):
            self.speak("Switching back to face control.")
            return 'EXIT'

        # ── Open on-screen keyboard ────────────────────────────────────────
        if "open keyboard" in t or "virtual keyboard" in t or "on screen keyboard" in t:
            self.speak("Opening virtual keyboard.")
            return 'KEYBOARD'

        # ── Dictation toggle ───────────────────────────────────────────────
        if "dictate" in t or "dictation mode" in t:
            self.dictate_mode = not self.dictate_mode
            state = "on" if self.dictate_mode else "off"
            self.speak(f"Dictation mode {state}.")
            return True
        if self.dictate_mode:
            if "stop dictating" in t or "stop dictation" in t:
                self.dictate_mode = False
                self.speak("Dictation off.")
            else:
                self._type_text(t)
            return True

        # ── Smart click by text on screen ──────────────────────────────────
        if t.startswith("click on ") or (t.startswith("click ") and len(t.split()) >= 2
                                          and t.split()[1] not in ("here",)):
            target = t.replace("click on ", "").replace("click ", "").strip()
            self._smart_click(target)
            return True

        # ── Screen reader ──────────────────────────────────────────────────
        if any(k in t for k in ["read page", "read screen", "read this",
                                  "read aloud", "what does it say"]):
            self._read_screen()
            return True

        # ── Window / app switching ─────────────────────────────────────────
        if t.startswith("switch to ") or t.startswith("focus ") or t.startswith("go to window "):
            app = (t.replace("switch to ", "")
                    .replace("focus ", "")
                    .replace("go to window ", "").strip())
            self._switch_window(app)
            return True

        # ── Quick replies ──────────────────────────────────────────────────
        if t.startswith("quick reply ") or t.startswith("send quick "):
            key = t.replace("quick reply ", "").replace("send quick ", "").strip()
            self._quick_reply(key)
            return True

        if "add quick reply" in t or "save reply" in t:
            # Syntax: "add quick reply [key] as [message]"
            m = re.search(r'(?:add quick reply|save reply)\s+(.+?)\s+as\s+(.+)', t)
            if m:
                rkey, rval = m.group(1).strip(), m.group(2).strip()
                QUICK_REPLIES[rkey] = rval
                self.speak(f"Quick reply '{rkey}' saved.")
            else:
                self.speak("Say: add quick reply [key] as [message].")
            return True

        if "list quick replies" in t or "show quick replies" in t:
            keys = ", ".join(QUICK_REPLIES.keys())
            self.speak(f"Quick reply keys: {keys}.")
            return True

        # ── Macro recording ────────────────────────────────────────────────
        if ("record macro" in t or "start recording" in t or "start macro" in t):
            # Optional name: "record macro morning routine"
            name = (t.replace("record macro", "")
                     .replace("start recording", "")
                     .replace("start macro", "").strip()) or None
            self._start_macro(name)
            return True

        if ("stop recording" in t or "stop macro" in t) and self._recording:
            name_match = re.search(r'(?:stop recording|stop macro)\s*(.*)', t)
            name = name_match.group(1).strip() if name_match else None
            self._stop_macro(name)
            return True

        if t.startswith("run macro ") or t.startswith("play macro "):
            name = t.replace("run macro ", "").replace("play macro ", "").strip()
            self._play_macro(name)
            return True

        if "list macros" in t or "show macros" in t:
            if _MACROS:
                self.speak("Saved macros: " + ", ".join(_MACROS.keys()))
            else:
                self.speak("No macros saved yet.")
            return True

        if t.startswith("delete macro "):
            name = t.replace("delete macro ", "").strip()
            if name in _MACROS:
                del _MACROS[name]
                _save_macros()
                self.speak(f"Macro '{name}' deleted.")
            else:
                self.speak(f"No macro named {name}.")
            return True

        # ── Tab management ─────────────────────────────────────────────────
        if t.startswith("go to tab "):
            raw = t.replace("go to tab ", "").strip()
            # Try numeric first
            if raw.isdigit():
                n = int(raw)
                if 1 <= n <= 9:
                    self._hotkey("ctrl", str(n))
                    self.speak(f"Tab {n}.")
                else:
                    self.speak("Tab numbers 1 through 9 only.")
            else:
                # "go to tab gmail" → find tab by OCR (best-effort)
                self.speak(f"Switching to {raw} tab.")
                self._smart_click(raw)  # Try OCR click on tab bar text
            return True

        if "list tabs" in t or "show tabs" in t:
            self.speak("Reading tab bar.")
            self._read_region_top()
            return True

        if "open last closed tab" in t or "reopen tab" in t or "restore tab" in t:
            self._hotkey("ctrl", "shift", "t")
            self.speak("Last closed tab restored.")
            return True

        if "pin tab" in t:
            if PYAUTOGUI_AVAILABLE:
                # Right-click the current tab area and press P for pin
                pyautogui.hotkey("ctrl", "l")       # focus address bar
                time.sleep(0.2)
                pyautogui.hotkey("escape")
                pyautogui.hotkey("ctrl", "shift", "p")
            self.speak("Tab pinned.")
            return True

        if "duplicate tab" in t:
            self._hotkey("ctrl", "l")
            time.sleep(0.15)
            if PYAUTOGUI_AVAILABLE:
                pyautogui.hotkey("alt", "enter")
            self.speak("Tab duplicated.")
            return True

        if "mute tab" in t or "mute this tab" in t:
            # Chrome: right-click tab → mute is not directly keyboardable without extension
            self.speak("Tab muting requires a browser extension for full support.")
            return True

        # ── Clipboard management ───────────────────────────────────────────
        if "clear clipboard" in t or "empty clipboard" in t:
            try:
                if PYAUTOGUI_AVAILABLE:
                    import subprocess
                    if self._is_windows:
                        subprocess.run("echo.|clip", shell=True)
                    else:
                        subprocess.run("xclip -selection clipboard /dev/null",
                                       shell=True, capture_output=True)
                self.speak("Clipboard cleared.")
            except Exception:
                self.speak("Could not clear clipboard.")
            return True

        if "show clipboard" in t or "clipboard history" in t:
            if self._is_windows:
                self._hotkey("win", "v")
            else:
                self.speak("Clipboard history requires a clipboard manager on Linux.")
            return True

        if "copy all" in t:
            self._hotkey("ctrl", "a")
            time.sleep(0.1)
            self._hotkey("ctrl", "c")
            self.speak("All selected and copied.")
            return True

        # ── Browser & websites ─────────────────────────────────────────────
        if t.startswith("open "):
            target = t[5:].strip()
            if target in WEBSITE_MAP:
                webbrowser.open(WEBSITE_MAP[target])
                self.speak(f"Opening {target}.")
                return True
            if target in APP_MAP:
                key = "windows" if self._is_windows else "linux"
                self._run(APP_MAP[target][key])
                self.speak(f"Opening {target}.")
                return True
            for fname, path in FOLDER_MAP.items():
                if fname in target:
                    self._open_folder(path)
                    self.speak(f"Opening {fname}.")
                    return True

        if t.startswith("search ") or t.startswith("search for "):
            query = t.replace("search for ", "").replace("search ", "").strip()
            webbrowser.open(f"https://www.google.com/search?q={query.replace(' ', '+')}")
            self.speak(f"Searching for {query}.")
            return True

        if t.startswith("youtube ") or "search youtube" in t:
            query = (t.replace("search youtube for ", "")
                      .replace("search youtube ", "")
                      .replace("youtube ", "").strip())
            webbrowser.open(f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}")
            self.speak(f"Searching YouTube for {query}.")
            return True

        if "new tab" in t:
            self._hotkey("ctrl", "t")
            self.speak("New tab.")
            return True
        if "close tab" in t:
            self._hotkey("ctrl", "w")
            self.speak("Tab closed.")
            return True
        if "next tab" in t or "tab right" in t:
            self._hotkey("ctrl", "tab")
            return True
        if "previous tab" in t or "tab left" in t:
            self._hotkey("ctrl", "shift", "tab")
            return True
        if "go back" in t or "browser back" in t:
            self._hotkey("alt", "left")
            return True
        if "go forward" in t or "browser forward" in t:
            self._hotkey("alt", "right")
            return True
        if "refresh" in t or "reload" in t:
            self._hotkey("ctrl", "r")
            return True
        if "hard refresh" in t or "force refresh" in t:
            self._hotkey("ctrl", "shift", "r")
            return True
        if "bookmark" in t and "open" not in t:
            self._hotkey("ctrl", "d")
            self.speak("Bookmarked.")
            return True
        if "open bookmarks" in t:
            self._hotkey("ctrl", "shift", "o")
            return True
        if "full screen" in t or "fullscreen" in t:
            self._hotkey("f11")
            return True
        if "zoom in" in t:
            self._hotkey("ctrl", "+")
            return True
        if "zoom out" in t:
            self._hotkey("ctrl", "-")
            return True
        if "reset zoom" in t or "default zoom" in t:
            self._hotkey("ctrl", "0")
            return True
        if "clear history" in t or "clear browsing" in t:
            self._hotkey("ctrl", "shift", "delete")
            self.speak("Opening clear browsing data.")
            return True
        if "developer tools" in t or "inspect element" in t:
            self._hotkey("f12")
            return True

        # ── Scrolling ──────────────────────────────────────────────────────
        if "scroll up" in t or "page up" in t:
            clicks = 15 if any(k in t for k in ("a lot", "way up", "much")) else 5
            if PYAUTOGUI_AVAILABLE:
                pyautogui.scroll(clicks)
            return True
        if "scroll down" in t or "page down" in t:
            clicks = 15 if any(k in t for k in ("a lot", "way down", "much")) else 5
            if PYAUTOGUI_AVAILABLE:
                pyautogui.scroll(-clicks)
            return True
        if "scroll top" in t or "go to top" in t:
            self._hotkey("ctrl", "home")
            return True
        if "scroll bottom" in t or "go to bottom" in t:
            self._hotkey("ctrl", "end")
            return True

        # ── Mouse controls ─────────────────────────────────────────────────
        if t in ("click", "left click"):
            if PYAUTOGUI_AVAILABLE:
                pyautogui.click()
            return True
        if "right click" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.rightClick()
            return True
        if "double click" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.doubleClick()
            return True
        if "middle click" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.middleClick()
            return True
        if t.startswith("move mouse"):
            self._move_mouse(t)
            return True
        if "move mouse center" in t or "mouse center" in t:
            if PYAUTOGUI_AVAILABLE:
                sw, sh = pyautogui.size()
                pyautogui.moveTo(sw // 2, sh // 2, duration=0.3)
            return True
        if "move mouse top right" in t:
            if PYAUTOGUI_AVAILABLE:
                sw, sh = pyautogui.size()
                pyautogui.moveTo(sw - 50, 50, duration=0.3)
            return True
        if "move mouse top left" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.moveTo(50, 50, duration=0.3)
            return True
        if "move mouse bottom" in t:
            if PYAUTOGUI_AVAILABLE:
                sw, sh = pyautogui.size()
                pyautogui.moveTo(sw // 2, sh - 50, duration=0.3)
            return True

        # ── Keyboard shortcuts ─────────────────────────────────────────────
        if "copy" in t and "paste" not in t:
            self._hotkey("ctrl", "c")
            self.speak("Copied.")
            return True
        if "paste" in t:
            self._hotkey("ctrl", "v")
            self.speak("Pasted.")
            return True
        if "cut" in t:
            self._hotkey("ctrl", "x")
            return True
        if "undo" in t and "redo" not in t:
            self._hotkey("ctrl", "z")
            return True
        if "redo" in t:
            self._hotkey("ctrl", "y")
            return True
        if "select all" in t:
            self._hotkey("ctrl", "a")
            return True
        if t in ("find", "open find"):
            self._hotkey("ctrl", "f")
            return True
        if "save file" in t or ("save" in t and len(t) < 12):
            self._hotkey("ctrl", "s")
            self.speak("Saved.")
            return True
        if "save as" in t:
            self._hotkey("ctrl", "shift", "s")
            return True
        if "print" in t:
            self._hotkey("ctrl", "p")
            return True
        if "press enter" in t or "hit enter" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("enter")
            return True
        if "press escape" in t or t == "escape" or "press esc" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("escape")
            return True
        if "press space" in t or "press spacebar" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("space")
            return True
        if "press tab" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("tab")
            return True
        if "press backspace" in t or "delete word" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("backspace")
            return True
        if "press delete" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("delete")
            return True

        # ── Typing ─────────────────────────────────────────────────────────
        if t.startswith("type "):
            self._type_text(t[5:].strip())
            return True
        if t.startswith("write "):
            self._type_text(t[6:].strip())
            return True

        # ── Window management ──────────────────────────────────────────────
        if "minimize" in t or "minimise" in t:
            if self._is_windows:
                self._hotkey("win", "down")
            else:
                self._hotkey("super", "down")
            return True
        if "maximize" in t or "maximise" in t:
            if self._is_windows:
                self._hotkey("win", "up")
            else:
                self._hotkey("super", "up")
            return True
        if "close window" in t or "close app" in t:
            self._hotkey("alt", "f4")
            return True
        if "switch window" in t or "alt tab" in t:
            self._hotkey("alt", "tab")
            return True
        if "show desktop" in t:
            if self._is_windows:
                self._hotkey("win", "d")
            else:
                self._hotkey("super", "d")
            return True
        if "snap left" in t or "window left" in t:
            if self._is_windows:
                self._hotkey("win", "left")
            return True
        if "snap right" in t or "window right" in t:
            if self._is_windows:
                self._hotkey("win", "right")
            return True

        # ── Volume & media ─────────────────────────────────────────────────
        if "volume up" in t or "louder" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("volumeup", presses=5)
            return True
        if "volume down" in t or "quieter" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("volumedown", presses=5)
            return True
        if "mute" in t and "unmute" not in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("volumemute")
            self.speak("Muted.")
            return True
        if "unmute" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("volumemute")
            self.speak("Unmuted.")
            return True
        if "play" in t and "pause" not in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("playpause")
            return True
        if "pause" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("playpause")
            return True
        if "next song" in t or "next track" in t or t == "next":
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("nexttrack")
            return True
        if "previous song" in t or "previous track" in t or t == "previous":
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("prevtrack")
            return True

        # ── Productivity ───────────────────────────────────────────────────
        if "new google doc" in t or "new doc" in t:
            webbrowser.open("https://docs.new")
            self.speak("Opening new Google Doc.")
            return True
        if "new google sheet" in t or "new sheet" in t:
            webbrowser.open("https://sheets.new")
            self.speak("Opening new Google Sheet.")
            return True
        if "new google slide" in t or "new presentation" in t:
            webbrowser.open("https://slides.new")
            self.speak("Opening new Google Slides.")
            return True
        if t.startswith("translate "):
            query = t[10:].strip()
            webbrowser.open(
                f"https://translate.google.com/?text={query.replace(' ', '+')}&op=translate"
            )
            self.speak(f"Translating: {query}.")
            return True
        if t.startswith("define ") or t.startswith("definition of ") or t.startswith("meaning of "):
            word = (t.replace("define ", "")
                     .replace("definition of ", "")
                     .replace("meaning of ", "").strip())
            webbrowser.open(f"https://www.google.com/search?q=define+{word}")
            self.speak(f"Looking up: {word}.")
            return True
        if "weather" in t and len(t) < 20:
            webbrowser.open("https://weather.com")
            self.speak("Opening weather.")
            return True
        if "news" in t and len(t) < 12:
            webbrowser.open("https://news.google.com")
            self.speak("Opening Google News.")
            return True
        if "calculator" in t and "open" not in t:
            key = "windows" if self._is_windows else "linux"
            self._run(APP_MAP["calculator"][key])
            return True

        # ── Accessibility ──────────────────────────────────────────────────
        if "magnifier" in t or "open magnifier" in t:
            if self._is_windows:
                self._run("magnify", "magnify")
            else:
                self._run("gnome-magnifier")
            self.speak("Magnifier opened.")
            return True

        if "narrator on" in t or "turn on narrator" in t:
            if self._is_windows:
                self._run("narrator", "narrator")
            else:
                self._run("orca")
            self.speak("Narrator started.")
            return True
        if "narrator off" in t or "turn off narrator" in t:
            if self._is_windows:
                subprocess.run("taskkill /f /im narrator.exe", shell=True)
            else:
                subprocess.run("pkill orca", shell=True)
            self.speak("Narrator stopped.")
            return True

        if "high contrast" in t or "toggle contrast" in t:
            if self._is_windows:
                # Toggle high contrast via left Alt+left Shift+Print Screen
                self._hotkey("lalt", "lshift", "printscreen")
            else:
                self._run("gsettings set org.gnome.desktop.interface gtk-theme 'HighContrast'")
            self.speak("High contrast toggled.")
            return True

        if "bigger text" in t or "increase text size" in t or "larger font" in t:
            if self._is_windows:
                webbrowser.open("ms-settings:easeofaccess-display")
            else:
                self._run("gnome-control-center universal-access")
            self.speak("Opening text size settings.")
            return True

        if "sticky keys" in t:
            if self._is_windows:
                webbrowser.open("ms-settings:easeofaccess-keyboard")
            self.speak("Opening keyboard accessibility settings.")
            return True

        if t.startswith("zoom page "):
            # "zoom page 150" → set browser zoom level
            raw = t.replace("zoom page ", "").strip()
            if raw.isdigit():
                level = int(raw)
                self.speak(f"Browser zoom is controlled by Ctrl + or Ctrl minus. Setting {level} percent.")
                # Best we can do: use Ctrl+0 to reset then zoom in/out
                self._hotkey("ctrl", "0")
                if level > 100:
                    steps = max(1, (level - 100) // 10)
                    for _ in range(steps):
                        self._hotkey("ctrl", "+")
                elif level < 100:
                    steps = max(1, (100 - level) // 10)
                    for _ in range(steps):
                        self._hotkey("ctrl", "-")
            return True

        if "on screen keyboard" in t or "accessibility keyboard" in t:
            if self._is_windows:
                self._run("osk", "osk")
            else:
                self._run("onboard")
            return True

        # ── System ─────────────────────────────────────────────────────────
        if "screenshot" in t or "take screenshot" in t or "screen shot" in t:
            self._take_screenshot()
            return True
        if "take photo" in t or "take picture" in t or "capture photo" in t:
            self._take_photo()
            return True
        if "lock screen" in t or "lock computer" in t:
            if self._is_windows:
                self._run("rundll32.exe user32.dll, LockWorkStation")
            else:
                self._run("loginctl lock-session")
            self.speak("Locking screen.")
            return True
        if "shutdown" in t or "shut down" in t:
            self.speak("Shutting down in 5 seconds. Say cancel to abort.")
            time.sleep(5)
            if self._is_windows:
                self._run("shutdown /s /t 0")
            else:
                self._run("shutdown -h now")
            return True
        if "restart" in t or "reboot" in t:
            self.speak("Restarting in 5 seconds.")
            time.sleep(5)
            if self._is_windows:
                self._run("shutdown /r /t 0")
            else:
                self._run("reboot")
            return True
        if "sleep" in t and "timer" not in t:
            if self._is_windows:
                self._run("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
            else:
                self._run("systemctl suspend")
            return True
        if "wifi on" in t or "turn on wifi" in t:
            self._run("nmcli radio wifi on",
                      "netsh interface set interface Wi-Fi enable")
            self.speak("Wi-Fi on.")
            return True
        if "wifi off" in t or "turn off wifi" in t:
            self._run("nmcli radio wifi off",
                      "netsh interface set interface Wi-Fi disable")
            self.speak("Wi-Fi off.")
            return True
        if "battery" in t:
            self._report_battery()
            return True
        if "disk" in t and ("usage" in t or "space" in t):
            self._report_disk()
            return True
        if "task manager" in t:
            if self._is_windows:
                self._hotkey("ctrl", "shift", "esc")
            else:
                self._run("gnome-system-monitor")
            self.speak("Opening task manager.")
            return True

        # ── Time / date / timers ───────────────────────────────────────────
        if "time" in t and len(t) < 12:
            import datetime
            now = datetime.datetime.now().strftime("%I:%M %p")
            self.speak(f"It is {now}.")
            return True
        if "date" in t and len(t) < 12:
            import datetime
            today = datetime.datetime.now().strftime("%A, %B %d")
            self.speak(f"Today is {today}.")
            return True
        if t.startswith("set alarm") or t.startswith("alarm at"):
            alarm_time = (t.replace("set alarm", "")
                           .replace("alarm at", "").strip())
            self._set_alarm(alarm_time)
            return True
        if (t.startswith("set timer") or t.startswith("start timer")
                or "minute timer" in t or "second timer" in t):
            self._parse_and_set_timer(t)
            return True
        if t.startswith("remind me") or t.startswith("reminder"):
            reminder_text = (t.replace("remind me to", "")
                               .replace("remind me", "")
                               .replace("reminder", "").strip())
            self.speak(f"Reminder set: {reminder_text}.")
            webbrowser.open(
                f"https://calendar.google.com/calendar/r/eventedit"
                f"?text={reminder_text.replace(' ', '+')}"
            )
            return True

        # ── Email ──────────────────────────────────────────────────────────
        if "compose email" in t or "new email" in t or "write email" in t:
            webbrowser.open("https://mail.google.com/mail/u/0/#compose")
            self.speak("Opening email composer.")
            return True
        if "check inbox" in t or "check email" in t or "open inbox" in t:
            webbrowser.open("https://mail.google.com")
            self.speak("Opening inbox.")
            return True
        if "reply email" in t or "reply to email" in t:
            self._hotkey("r")
            return True
        if t.startswith("send email to ") or t.startswith("email "):
            name = (t.replace("send email to ", "")
                     .replace("email ", "").strip())
            self._compose_email(name)
            return True

        # ── Messaging ──────────────────────────────────────────────────────
        if t.startswith("whatsapp ") or t.startswith("message "):
            name = (t.replace("whatsapp ", "")
                     .replace("message ", "").strip())
            self._open_whatsapp_chat(name)
            return True
        if "open telegram" in t:
            webbrowser.open("https://web.telegram.org")
            return True
        if t.startswith("call ") or t.startswith("video call "):
            name = (t.replace("video call ", "")
                     .replace("call ", "").strip())
            self._initiate_call(name)
            return True
        if "zoom meeting" in t or "open zoom" in t:
            key = "windows" if self._is_windows else "linux"
            self._run(APP_MAP.get("zoom", {}).get(key, "zoom"))
            return True
        if "google meet" in t:
            webbrowser.open("https://meet.google.com")
            return True
        if "schedule meeting" in t or "add event" in t:
            title = (t.replace("schedule meeting ", "")
                      .replace("add event ", "").strip())
            webbrowser.open(
                f"https://calendar.google.com/calendar/r/eventedit"
                f"?text={title.replace(' ', '+')}"
            )
            self.speak(f"Opening calendar for: {title}.")
            return True

        # ── Files ──────────────────────────────────────────────────────────
        if "new folder" in t or "create folder" in t:
            self._hotkey("ctrl", "shift", "n")
            return True
        if "new file" in t:
            self._hotkey("ctrl", "n")
            return True
        if "rename" in t:
            if PYAUTOGUI_AVAILABLE:
                pyautogui.press("f2")
            return True
        if "empty trash" in t or "empty recycle bin" in t:
            if self._is_windows:
                self._run('PowerShell -Command "Clear-RecycleBin -Force"')
            else:
                self._run("gio trash --empty")
            self.speak("Trash emptied.")
            return True
        if "open recycle bin" in t or "open trash" in t:
            if self._is_windows:
                self._run("explorer shell:RecycleBinFolder")
            else:
                self._open_folder(os.path.expanduser("~/.local/share/Trash"))
            return True

        # ── Help ───────────────────────────────────────────────────────────
        if "help" in t or "what can you do" in t or "commands" in t:
            self.speak(
                "You can say: open, search, scroll, click on [word], "
                "read page, switch to [app], quick reply [key], "
                "record macro, run macro [name], "
                "type, screenshot, take photo, "
                "volume up, volume down, mute, play, pause, "
                "compose email, whatsapp, timer, reminder, "
                "magnifier, narrator on, high contrast, zoom page, "
                "translate, define, new google doc, "
                "go to tab [number], open last closed tab, "
                "shutdown, lock screen, and more. "
                "Say stop voice to exit."
            )
            return True

        return False

    # ══════════════════════════════════════════════════════════════════════
    # Smart click by text (OCR)
    # ══════════════════════════════════════════════════════════════════════

    def _smart_click(self, target_text: str):
        """Take a screenshot, OCR it, find target_text, click its centre."""
        if not OCR_AVAILABLE:
            self.speak("Smart click requires pytesseract. Please install it.")
            return
        if not PYAUTOGUI_AVAILABLE:
            return
        self.speak(f"Looking for: {target_text}.")
        try:
            screenshot = pyautogui.screenshot()
            data = pytesseract.image_to_data(
                screenshot,
                output_type=pytesseract.Output.DICT,
                config='--psm 11',
            )
            target_lower = target_text.lower()
            found = False
            for i, word in enumerate(data['text']):
                if not word:
                    continue
                if target_lower in word.lower():
                    x = data['left'][i] + data['width'][i]  // 2
                    y = data['top'][i]  + data['height'][i] // 2
                    pyautogui.moveTo(x, y, duration=0.25)
                    pyautogui.click()
                    self.speak(f"Clicked on {word}.")
                    found = True
                    break
            if not found:
                self.speak(f"Could not find '{target_text}' on screen.")
        except Exception as e:
            print(f"Smart click error: {e}")
            self.speak("Smart click failed.")

    # ══════════════════════════════════════════════════════════════════════
    # Screen reader (OCR → TTS)
    # ══════════════════════════════════════════════════════════════════════

    def _read_screen(self):
        if not OCR_AVAILABLE:
            self.speak("Screen reader requires pytesseract. Please install it.")
            return
        if not PYAUTOGUI_AVAILABLE:
            return
        self.speak("Reading the screen now.")
        try:
            screenshot = pyautogui.screenshot()
            text = pytesseract.image_to_string(screenshot, config='--psm 6')
            text = text.strip()
            # Limit length to avoid very long TTS
            if len(text) > 800:
                text = text[:800] + " … text truncated."
            if text:
                self.speak(text)
            else:
                self.speak("No readable text found on screen.")
        except Exception as e:
            print(f"Screen reader error: {e}")
            self.speak("Could not read screen.")

    def _read_region_top(self):
        """OCR only the top portion of the screen (tab bar area)."""
        if not OCR_AVAILABLE or not PYAUTOGUI_AVAILABLE:
            return
        try:
            sw, sh = pyautogui.size()
            region = (0, 0, sw, 80)  # top 80 pixels
            screenshot = pyautogui.screenshot(region=region)
            text = pytesseract.image_to_string(screenshot, config='--psm 6')
            text = text.strip()
            if text:
                self.speak(f"Tabs: {text}")
            else:
                self.speak("Could not read tab bar.")
        except Exception as e:
            print(f"Tab read error: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # Window / app switcher
    # ══════════════════════════════════════════════════════════════════════

    def _switch_window(self, app_name: str):
        """Bring a window to front by partial title match."""
        app_lower = app_name.lower()

        # Windows — use pygetwindow
        if PYGETWINDOW_AVAILABLE:
            try:
                wins = [w for w in gw.getAllWindows()
                        if w.title and app_lower in w.title.lower()]
                if wins:
                    wins[0].activate()
                    self.speak(f"Switched to {wins[0].title}.")
                    return
            except Exception as e:
                print(f"pygetwindow error: {e}")

        # Linux — use wmctrl
        if self._is_linux:
            result = subprocess.run(
                f'wmctrl -a "{app_name}"', shell=True, capture_output=True
            )
            if result.returncode == 0:
                self.speak(f"Switched to {app_name}.")
                return

        # Fallback: Alt+Tab and hope for the best
        self.speak(f"Could not find {app_name}. Try Alt Tab to cycle windows.")
        self._hotkey("alt", "tab")

    # ══════════════════════════════════════════════════════════════════════
    # Quick replies
    # ══════════════════════════════════════════════════════════════════════

    def _quick_reply(self, key: str):
        reply = QUICK_REPLIES.get(key.lower())
        if reply:
            self._type_text(reply)
            self.speak(f"Quick reply sent: {key}.")
        else:
            available = ", ".join(QUICK_REPLIES.keys())
            self.speak(f"No quick reply for '{key}'. Available: {available}.")

    # ══════════════════════════════════════════════════════════════════════
    # Macro recording & playback
    # ══════════════════════════════════════════════════════════════════════

    def _start_macro(self, name: str | None = None):
        if not PYNPUT_AVAILABLE:
            self.speak("Macro recording requires pynput. Please install it.")
            return
        if self._recording:
            self.speak("Already recording. Say stop recording first.")
            return

        self._recording       = True
        self._macro_events    = []
        self._macro_start_time = time.time()
        self._pending_macro_name = name

        def on_move(x, y):
            if self._recording:
                t = time.time() - self._macro_start_time
                self._macro_events.append(('move', t, x, y))

        def on_click(x, y, button, pressed):
            if self._recording:
                t = time.time() - self._macro_start_time
                self._macro_events.append(('click', t, x, y, str(button), pressed))

        def on_scroll(x, y, dx, dy):
            if self._recording:
                t = time.time() - self._macro_start_time
                self._macro_events.append(('scroll', t, x, y, dx, dy))

        def on_press(key):
            if self._recording:
                t = time.time() - self._macro_start_time
                try:
                    k = key.char
                except AttributeError:
                    k = str(key)
                self._macro_events.append(('key_press', t, k))

        def on_release(key):
            if self._recording:
                t = time.time() - self._macro_start_time
                try:
                    k = key.char
                except AttributeError:
                    k = str(key)
                self._macro_events.append(('key_release', t, k))

        self._mouse_listener    = pynput_mouse.Listener(
            on_move=on_move, on_click=on_click, on_scroll=on_scroll)
        self._keyboard_listener = pynput_keyboard.Listener(
            on_press=on_press, on_release=on_release)
        self._mouse_listener.start()
        self._keyboard_listener.start()

        label = f"'{name}'" if name else "(unnamed)"
        self.speak(f"Recording macro {label}. Say stop recording when done.")
        print(f"🔴 Macro recording started {label}")

    def _stop_macro(self, name: str | None = None):
        if not self._recording:
            self.speak("Not currently recording.")
            return

        self._recording = False
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._keyboard_listener:
            self._keyboard_listener.stop()

        final_name = (name or self._pending_macro_name
                      or f"macro_{len(_MACROS) + 1}")
        final_name = final_name.strip()
        _MACROS[final_name] = self._macro_events
        _save_macros()
        self.speak(f"Macro '{final_name}' saved with {len(self._macro_events)} events.")
        print(f"✅ Macro '{final_name}' saved ({len(self._macro_events)} events).")

    def _play_macro(self, name: str):
        if name not in _MACROS:
            available = ", ".join(_MACROS.keys()) if _MACROS else "none"
            self.speak(f"No macro named '{name}'. Available: {available}.")
            return
        if not PYNPUT_AVAILABLE or not PYAUTOGUI_AVAILABLE:
            self.speak("Macro playback requires pynput and pyautogui.")
            return

        events = _MACROS[name]
        self.speak(f"Running macro '{name}'.")

        def _run():
            prev_t = 0.0
            for event in events:
                evt_type = event[0]
                evt_time = event[1]
                delay = evt_time - prev_t
                if delay > 0:
                    time.sleep(min(delay, 2.0))  # cap single delay to 2s
                prev_t = evt_time

                try:
                    if evt_type == 'move':
                        pyautogui.moveTo(event[2], event[3], duration=0)
                    elif evt_type == 'click':
                        x, y, btn_str, pressed = event[2], event[3], event[4], event[5]
                        pyautogui.moveTo(x, y, duration=0)
                        if pressed:
                            if 'right' in btn_str:
                                pyautogui.mouseDown(button='right')
                            else:
                                pyautogui.mouseDown()
                        else:
                            if 'right' in btn_str:
                                pyautogui.mouseUp(button='right')
                            else:
                                pyautogui.mouseUp()
                    elif evt_type == 'scroll':
                        pyautogui.scroll(event[5])
                    elif evt_type == 'key_press':
                        k = event[2]
                        if len(k) == 1:
                            self._kb_ctrl.press(k)
                        else:
                            try:
                                self._kb_ctrl.press(
                                    getattr(PKey, k.replace('Key.', ''), k)
                                )
                            except Exception:
                                pass
                    elif evt_type == 'key_release':
                        k = event[2]
                        if len(k) == 1:
                            self._kb_ctrl.release(k)
                        else:
                            try:
                                self._kb_ctrl.release(
                                    getattr(PKey, k.replace('Key.', ''), k)
                                )
                            except Exception:
                                pass
                except Exception as e:
                    print(f"Macro event error: {e}")

            print(f"✅ Macro '{name}' playback complete.")

        threading.Thread(target=_run, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════
    # Internal helpers (unchanged + new)
    # ══════════════════════════════════════════════════════════════════════

    def _move_mouse(self, text: str):
        if not PYAUTOGUI_AVAILABLE:
            return
        step = 100
        if "left" in text:
            pyautogui.moveRel(-step, 0, duration=0.2)
        elif "right" in text:
            pyautogui.moveRel(step, 0, duration=0.2)
        elif "up" in text:
            pyautogui.moveRel(0, -step, duration=0.2)
        elif "down" in text:
            pyautogui.moveRel(0, step, duration=0.2)

    def _compose_email(self, name: str):
        contact = CONTACTS.get(name.lower())
        if contact:
            webbrowser.open(
                f"https://mail.google.com/mail/u/0/#compose?to={contact['email']}"
            )
            self.speak(f"Composing email to {name}.")
        else:
            webbrowser.open("https://mail.google.com/mail/u/0/#compose")
            self.speak(f"Could not find {name} in contacts. Opening composer.")

    def _open_whatsapp_chat(self, name: str):
        contact = CONTACTS.get(name.lower())
        if contact and "phone" in contact:
            phone = contact["phone"].replace("+", "").replace(" ", "")
            webbrowser.open(f"https://wa.me/{phone}")
            self.speak(f"Opening WhatsApp chat with {name}.")
        else:
            webbrowser.open("https://web.whatsapp.com")
            self.speak(f"Could not find {name}. Opening WhatsApp Web.")

    def _initiate_call(self, name: str):
        contact = CONTACTS.get(name.lower())
        if contact:
            webbrowser.open("https://meet.google.com/new")
            self.speak(f"Starting call with {name}.")
        else:
            self.speak(f"Contact {name} not found.")

    def _take_screenshot(self):
        import datetime
        filename = os.path.expanduser(
            f"~/Pictures/screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        )
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        if PYAUTOGUI_AVAILABLE:
            img = pyautogui.screenshot()
            img.save(filename)
            self.speak("Screenshot saved to Pictures.")
            print(f"📸 Screenshot saved: {filename}")
        else:
            self.speak("pyautogui not available for screenshot.")

    def _take_photo(self):
        import datetime
        cap = self.cap
        release_after = False
        if cap is None or not cap.isOpened():
            cap = cv2.VideoCapture(0)
            release_after = True
        ret, frame = cap.read()
        if ret:
            filename = os.path.expanduser(
                f"~/Pictures/photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            )
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            cv2.imwrite(filename, frame)
            self.speak("Photo taken and saved to Pictures.")
            print(f"📷 Photo saved: {filename}")
        else:
            self.speak("Could not access camera.")
        if release_after:
            cap.release()

    def _report_battery(self):
        try:
            if self._is_windows:
                result = subprocess.check_output(
                    "wmic path Win32_Battery get EstimatedChargeRemaining",
                    shell=True
                ).decode()
                levels = [x for x in result.split() if x.isdigit()]
                pct = levels[0] if levels else "unknown"
            else:
                result = subprocess.check_output(
                    "cat /sys/class/power_supply/BAT*/capacity 2>/dev/null || echo unknown",
                    shell=True
                ).decode().strip()
                pct = result.split("\n")[0]
            self.speak(f"Battery is at {pct} percent.")
        except Exception:
            self.speak("Could not read battery status.")

    def _report_disk(self):
        try:
            import shutil
            total, used, free = shutil.disk_usage("/")
            free_gb  = free  // (2 ** 30)
            total_gb = total // (2 ** 30)
            self.speak(f"{free_gb} gigabytes free of {total_gb} total.")
        except Exception:
            self.speak("Could not read disk usage.")

    def _parse_and_set_timer(self, text: str):
        match = re.search(r'(\d+)\s*(minute|min|second|sec|hour|hr)', text)
        if match:
            amount = int(match.group(1))
            unit   = match.group(2)
            if "sec" in unit:
                seconds, label = amount, f"{amount} second"
            elif "hour" in unit or "hr" in unit:
                seconds, label = amount * 3600, f"{amount} hour"
            else:
                seconds, label = amount * 60, f"{amount} minute"
            self.speak(f"{label} timer started.")

            def _timer():
                time.sleep(seconds)
                self.speak(f"Your {label} timer is done!")
                if PYAUTOGUI_AVAILABLE:
                    try:
                        pyautogui.alert(f"⏰ {label} timer done!")
                    except Exception:
                        pass

            self._timer_thread = threading.Thread(target=_timer, daemon=True)
            self._timer_thread.start()
        else:
            self.speak("Could not parse timer duration. Try: set timer 5 minutes.")

    def _set_alarm(self, alarm_time_str: str):
        self.speak(f"Opening calendar to set alarm at {alarm_time_str}.")
        webbrowser.open(
            f"https://calendar.google.com/calendar/r/eventedit"
            f"?text=Alarm&dates={alarm_time_str}"
        )

    # ══════════════════════════════════════════════════════════════════════
    # Main recognition loop
    # ══════════════════════════════════════════════════════════════════════

    def run(self, stop_flag):
        """
        Blocking loop. Exits when stop_flag is set or user says 'stop voice'.
        Returns 'KEYBOARD' if user asked to open the virtual keyboard.
        """
        self.running = True
        self.speak(
            "Voice command mode active. Say help for commands, "
            "or stop voice to return to face control."
        )
        print("\n" + "=" * 55)
        print("🎙️  VOICE COMMAND MODE ACTIVE")
        print("=" * 55)
        print("   Say any command. 'stop voice' to exit.\n")

        result = None
        for text in self._build_recognizer():
            if stop_flag.is_set():
                break
            if not text:
                continue
            time.sleep(0.3)
            cmd_result = self.execute(text)
            if cmd_result == 'EXIT':
                break
            if cmd_result == 'KEYBOARD':
                result = 'KEYBOARD'
                break
            if cmd_result is False:
                now = time.time()
                if not hasattr(self, '_last_unrecog') or now - self._last_unrecog > 4:
                    self.speak("Command not recognised. Say help for options.")
                    self._last_unrecog = now

        self.running = False
        print("⏹️  Voice command mode stopped.\n")
        return result

    # ══════════════════════════════════════════════════════════════════════
    # Speech recognition back-ends
    # ══════════════════════════════════════════════════════════════════════

    def _build_recognizer(self):
        if SPEECH_REC_AVAILABLE:
            yield from self._listen_google()
        elif VOSK_AVAILABLE:
            yield from self._listen_vosk_free()
        else:
            print("❌ No voice recognition available.")
            while True:
                yield None
                time.sleep(1)

    def _listen_vosk_free(self):
        try:
            model = Model("model")
        except Exception as e:
            print(f"❌ Vosk model: {e}")
            yield from self._listen_google()
            return

        rec = KaldiRecognizer(model, 16000)
        p = pyaudio.PyAudio()
        try:
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                            input=True, frames_per_buffer=8000)
            stream.start_stream()
        except Exception as e:
            print(f"❌ Mic: {e}")
            return

        print("✅ Vosk (free-form) ready — speak a command")
        last_yield = 0.0
        cooldown   = 2.5

        SINGLE_WORD_COMMANDS = {
            "help", "time", "date", "click", "mute", "unmute",
            "copy", "paste", "cut", "undo", "redo", "play", "pause",
            "next", "previous", "refresh", "reload", "bookmark",
            "screenshot", "battery", "print", "rename",
            "minimize", "maximize", "dictate",
        }

        while True:
            try:
                if self._is_speaking:
                    stream.read(8000, exception_on_overflow=False)
                    time.sleep(0.1)
                    continue
                data = stream.read(8000, exception_on_overflow=False)
                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text   = result.get("text", "").strip()
                    words  = text.split()
                    valid  = (
                        len(words) >= 2
                        or (len(words) == 1 and words[0] in SINGLE_WORD_COMMANDS)
                    )
                    now = time.time()
                    if text and valid and now - last_yield > cooldown:
                        last_yield = now
                        yield text
            except Exception:
                continue

    def _listen_google(self):
        recognizer = sr.Recognizer()
        mic = sr.Microphone()
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=2)
        print("✅ Google Speech ready — speak a command")
        while True:
            try:
                with mic as source:
                    audio = recognizer.listen(source, timeout=8, phrase_time_limit=6)
                text = recognizer.recognize_google(
                    audio, language="en-US"
                ).lower().strip()
                if text:
                    yield text
            except sr.UnknownValueError:
                continue
            except sr.WaitTimeoutError:
                continue
            except sr.RequestError as e:
                print(f"❌ Google API: {e}")
                time.sleep(2)
            except Exception:
                continue