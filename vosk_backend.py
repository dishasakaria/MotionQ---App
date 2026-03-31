"""
vosk_backend.py — Two-mode cross-platform Vosk engine.

MODE 1: StrictListener  — grammar-constrained, zero false positives.
        Used by mainController.py for mode switching (eye/smile/head/voice/keyboard).

MODE 2: SmartListener   — free-form + prefix validation, handles dynamic commands.
        Used by voice_commands.py where commands have variable arguments
        e.g. "search for cats", "open notepad", "type hello world".

Both modes:
  • Indian-accent Vosk model (vosk-model-small-en-in-0.4)
  • Dynamic RMS noise floor  (adapts to the room)
  • Pre-speech buffer        (no clipped first syllables)
  • sounddevice backend      (stable on Windows / Linux / macOS)
  • Auto-downloads model on first run
"""

import os
import json
import time
import queue
import zipfile
import urllib.request
from collections import deque

import numpy as np

try:
    import sounddevice as sd
    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False
    print("⚠️  sounddevice not installed — run: pip install sounddevice")

try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False
    print("⚠️  vosk not installed — run: pip install vosk")

# ── Model paths ───────────────────────────────────────────────────────────────
MODEL_URL   = "https://alphacephei.com/vosk/models/vosk-model-small-en-in-0.4.zip"
MODEL_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model-en-in")
SAMPLE_RATE = 16000

# ── MODE 1: Strict grammar (mainController mode-switch words) ─────────────────
STRICT_GRAMMAR = ["eye", "smile", "head", "voice", "keyboard", "[unk]"]

# ── MODE 2: Smart prefixes (voice_commands.py) ───────────────────────────────
# A command is VALID if its first word(s) match one of these prefixes.
# Everything after the prefix is accepted as the dynamic argument.
# This allows "search for cats", "open notepad", "type hello world" etc.
# ── Known valid arguments per prefix (for fuzzy correction) ──────────────────
# When Vosk mishears "notepad" as "northward", fuzzy matching snaps it back.
KNOWN_ARGS = {
    "open": [
        # apps
        "notepad", "calculator", "terminal", "files", "spotify", "vscode",
        "vlc", "zoom", "discord", "slack", "settings", "chrome", "firefox",
        "edge", "word", "excel", "powerpoint", "paint", "explorer",
        "task manager",
        # websites
        "google", "youtube", "gmail", "maps", "github", "netflix",
        "whatsapp", "telegram", "linkedin", "instagram", "twitter",
        "reddit", "amazon", "facebook", "meet", "drive", "calendar",
        "photos", "docs", "sheets", "slides", "notion", "figma",
        "chatgpt", "claude", "translate", "weather", "news", "keep",
        # folders
        "downloads", "documents", "pictures", "desktop", "music", "videos",
        # misc
        "keyboard", "magnifier", "bookmarks", "trash", "recycle bin",
        "inbox", "find",
    ],
    "search": [
        # free-form — keep as-is, no correction
    ],
    "youtube": [],
    "switch to": [
        "chrome", "firefox", "edge", "notepad", "explorer", "terminal",
        "vscode", "spotify", "zoom", "discord", "slack", "word", "excel",
        "powerpoint", "paint", "calculator", "settings", "task manager",
    ],
    "go to tab": ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
    "whatsapp": ["mom", "dad", "boss", "friend"],
    "message":  ["mom", "dad", "boss", "friend"],
    "email":    ["mom", "dad", "boss", "friend"],
    "call":     ["mom", "dad", "boss", "friend"],
    "quick reply": ["yes", "no", "busy", "later", "thanks", "welcome",
                    "on my way", "sorry", "ok", "done"],
    "run macro":    [],
    "play macro":   [],
    "delete macro": [],
}

COMMAND_PREFIXES = {
    # ── single-word fixed commands (no argument needed) ───────────────────────
    "click", "copy", "paste", "cut", "undo", "redo", "find",
    "save", "print", "rename", "refresh", "reload", "bookmark",
    "minimize", "maximize", "dictate", "mute", "unmute",
    "play", "pause", "next", "previous", "screenshot",
    "battery", "time", "date", "help", "commands", "weather", "news",
    "calculator", "escape", "enter",

    # ── two-word fixed commands ───────────────────────────────────────────────
    "stop voice", "exit voice", "quit voice", "face control", "back to face",
    "open keyboard", "virtual keyboard", "on screen keyboard",
    "stop dictating", "stop dictation", "dictation mode",
    "read page", "read screen", "read this", "read aloud",
    "new tab", "close tab", "next tab", "previous tab",
    "go back", "go forward", "hard refresh", "force refresh",
    "open bookmarks", "full screen", "fullscreen",
    "zoom in", "zoom out", "reset zoom", "default zoom",
    "clear history", "developer tools", "inspect element",
    "last closed tab", "reopen tab", "restore tab",
    "pin tab", "duplicate tab", "mute tab", "list tabs", "show tabs",
    "clear clipboard", "empty clipboard", "show clipboard",
    "clipboard history", "copy all",
    "scroll up", "scroll down", "page up", "page down",
    "scroll top", "go top", "scroll bottom", "go bottom",
    "left click", "right click", "double click", "middle click",
    "mouse center",
    "select all", "save as", "save file",
    "press enter", "hit enter", "press escape", "press esc",
    "press space", "press spacebar", "press tab",
    "press backspace", "delete word", "press delete",
    "volume up", "volume down",
    "next song", "next track", "previous song", "previous track",
    "new folder", "create folder", "new file",
    "empty trash", "empty recycle bin", "open recycle bin", "open trash",
    "switch window", "alt tab", "show desktop", "snap left", "snap right",
    "close window", "close app",
    "compose email", "new email", "write email",
    "check inbox", "check email", "open inbox",
    "reply email", "zoom meeting", "google meet",
    "lock screen", "lock computer",
    "wifi on", "wifi off", "turn on wifi", "turn off wifi",
    "disk usage", "disk space", "task manager",
    "open magnifier", "narrator on", "narrator off",
    "turn on narrator", "turn off narrator",
    "high contrast", "toggle contrast",
    "bigger text", "increase text size", "larger font",
    "sticky keys",
    "list quick replies", "show quick replies",
    "list macros", "show macros",
    "record macro", "start recording", "start macro",
    "stop recording", "stop macro",
    "quick reply", "send quick",
    "add quick reply", "save reply",
    "run macro", "play macro", "delete macro",
    "window left", "window right",
    "new doc", "new sheet", "new presentation",
    "new google doc", "new google sheet", "new google slide",

    # ── variable-argument prefixes ────────────────────────────────────────────
    # Everything AFTER the prefix is the dynamic argument.
    "open",           # open notepad / open youtube / open downloads / open chrome
    "search",         # search for cats / search python tutorials
    "youtube",        # youtube lo-fi music
    "type",           # type hello world
    "write",          # write my address is
    "click on",       # click on search bar
    "switch to",      # switch to chrome / switch to notepad
    "focus",          # focus chrome
    "go to tab",      # go to tab 3
    "go to window",   # go to window explorer
    "translate",      # translate how are you in french
    "define",         # define entropy
    "definition of",  # definition of machine learning
    "meaning of",     # meaning of ephemeral
    "set timer",      # set timer 5 minutes
    "start timer",    # start timer 30 seconds
    "set alarm",      # set alarm 7 am
    "alarm at",       # alarm at 8 30
    "remind me",      # remind me to call mom
    "reminder",       # reminder team meeting
    "send email to",  # send email to boss
    "email",          # email dad
    "whatsapp",       # whatsapp friend
    "message",        # message mom
    "call",           # call dad
    "video call",     # video call sister
    "schedule meeting",  # schedule meeting tomorrow
    "zoom page",      # zoom page 150
    "add event",      # add event birthday
    "move mouse",     # move mouse left / right / up / down
}


# ── Model downloader ──────────────────────────────────────────────────────────

def _download_model() -> bool:
    if os.path.isdir(MODEL_DIR):
        return True

    zip_path = MODEL_DIR + ".zip"
    print(f"\n📥 Downloading Indian-accent Vosk model (~36 MB) …")
    print(f"   URL    : {MODEL_URL}")
    print(f"   Target : {MODEL_DIR}\n")

    def _progress(block, block_size, total):
        done = block * block_size
        if total > 0:
            pct = min(100, done * 100 // total)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r   [{bar}] {pct}%", end="", flush=True)

    try:
        urllib.request.urlretrieve(MODEL_URL, zip_path, reporthook=_progress)
        print("\n✅ Download complete — extracting …")

        parent = os.path.dirname(MODEL_DIR)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(parent)

        for name in os.listdir(parent):
            full = os.path.join(parent, name)
            if os.path.isdir(full) and "en-in" in name and full != MODEL_DIR:
                os.rename(full, MODEL_DIR)
                break

        os.remove(zip_path)
        print(f"✅ Model ready: {MODEL_DIR}\n")
        return True

    except Exception as e:
        print(f"\n❌ Download failed: {e}")
        print("   Download manually from:")
        print(f"   {MODEL_URL}")
        print(f"   Extract and rename folder to: {MODEL_DIR}\n")
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        return False


# ── Shared audio mixin ────────────────────────────────────────────────────────

class _AudioMixin:
    def _init_audio(self):
        self._audio_q        = queue.Queue()
        self._noise_floor    = 0.005
        self._adapt_rate     = 0.05
        self._silence_factor = 1.8
        self._pre_buffer     = deque(maxlen=10)
        self._is_speaking    = False
        self._silent_frames  = 0
        self._silence_timeout = int(SAMPLE_RATE * 0.8)

    def calibrate(self, seconds: float = 2.0):
        if not SD_AVAILABLE:
            return
        print(f"🎤 Calibrating noise floor ({seconds:.0f}s — stay quiet) …")
        samples = []

        def _cb(indata, frames, ti, status):
            samples.append(float(np.sqrt(np.mean(indata ** 2))))

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            blocksize=1600, callback=_cb):
            time.sleep(seconds)

        if samples:
            self._noise_floor = float(np.mean(samples))
            thr = self._noise_floor * self._silence_factor
            print(f"✅ Noise floor: {self._noise_floor:.4f} | Threshold: {thr:.4f}\n")

    def _audio_callback(self, indata, frames, time_info, status):
        rms = float(np.sqrt(np.mean(indata ** 2)))
        threshold = self._noise_floor * self._silence_factor

        if not self._is_speaking:
            self._noise_floor = (
                (1 - self._adapt_rate) * self._noise_floor
                + self._adapt_rate * rms
            )
            self._pre_buffer.append(indata.copy())

        if rms > threshold:
            if not self._is_speaking:
                self._is_speaking = True
                for chunk in self._pre_buffer:
                    self._audio_q.put(("AUDIO", chunk))
            self._silent_frames = 0
            self._audio_q.put(("AUDIO", indata.copy()))

        elif self._is_speaking:
            self._silent_frames += frames
            self._audio_q.put(("AUDIO", indata.copy()))
            if self._silent_frames > self._silence_timeout:
                self._audio_q.put(("END", None))
                self._is_speaking   = False
                self._silent_frames = 0
                self._pre_buffer.clear()


# ── MODE 1: StrictListener ────────────────────────────────────────────────────

class StrictListener(_AudioMixin):
    """
    Grammar-constrained — ONLY yields exact words from the grammar list.
    Use this in mainController.py for mode switching.

    Example:
        listener = StrictListener()
        for word in listener.listen():
            # word is always one of: eye, smile, head, voice, keyboard
    """

    def __init__(self, grammar: list = None, cooldown: float = 2.5):
        self.ready    = False
        self.cooldown = cooldown
        self._init_audio()

        if not SD_AVAILABLE or not VOSK_AVAILABLE:
            return
        if not _download_model():
            return

        try:
            self._model = Model(MODEL_DIR)
        except Exception as e:
            print(f"❌ Vosk model load error: {e}")
            return

        g = grammar or STRICT_GRAMMAR
        self._rec   = KaldiRecognizer(self._model, SAMPLE_RATE, json.dumps(g))
        self._valid = {w for w in g if w != "[unk]"}
        self.ready  = True
        print(f"✅ StrictListener ready — grammar: {sorted(self._valid)}")

    def listen(self):
        if not self.ready:
            return
        self.calibrate()
        last_yield = 0.0

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            blocksize=1600, dtype="float32",
                            callback=self._audio_callback):
            while True:
                try:
                    kind, chunk = self._audio_q.get(timeout=0.1)
                except queue.Empty:
                    continue

                if kind == "AUDIO":
                    pcm = (chunk * 32768).astype(np.int16).tobytes()
                    self._rec.AcceptWaveform(pcm)

                elif kind == "END":
                    res  = json.loads(self._rec.FinalResult())
                    text = res.get("text", "").strip().lower()

                    if not text or text not in self._valid:
                        continue

                    now = time.time()
                    if now - last_yield < self.cooldown:
                        continue

                    last_yield = now
                    print(f"✅ COMMAND: {text.upper()}")
                    yield text


# ── MODE 2: SmartListener ─────────────────────────────────────────────────────

class SmartListener(_AudioMixin):
    """
    Free-form Vosk + prefix validation.
    Accepts any phrase that STARTS WITH a known command prefix.
    Handles dynamic arguments: "search for cats", "open notepad",
    "type hello world", "go to tab 3", "translate hello in french", etc.
    Rejects noise, gibberish, and unrecognised phrases.

    Example:
        listener = SmartListener()
        for text in listener.listen():
            # text = "open notepad" / "search for cats" / "scroll up" …
    """

    def __init__(self, cooldown: float = 1.5):
        self.ready    = False
        self.cooldown = cooldown
        self._init_audio()
        self._single = {p for p in COMMAND_PREFIXES if len(p.split()) == 1}
        self._multi  = sorted(
            [p for p in COMMAND_PREFIXES if len(p.split()) > 1],
            key=len, reverse=True   # check longer prefixes first
        )

        if not SD_AVAILABLE or not VOSK_AVAILABLE:
            return
        if not _download_model():
            return

        try:
            self._model = Model(MODEL_DIR)
        except Exception as e:
            print(f"❌ Vosk model load error: {e}")
            return

        self._rec  = KaldiRecognizer(self._model, SAMPLE_RATE)
        self.ready = True
        print(f"✅ SmartListener ready — {len(COMMAND_PREFIXES)} command prefixes loaded")

    def _fuzzy_correct(self, text: str) -> str:
        """
        Try to snap misheard argument words to the closest known valid word.
        e.g. "open northward" → "open notepad"
             "open go will"   → "open google"
        Uses difflib — no extra dependencies.
        Returns corrected text, or original if no good match found.
        """
        import difflib

        words = text.split()
        if not words:
            return text

        # Find which prefix matched
        matched_prefix = None
        for prefix in self._multi:
            if text == prefix or text.startswith(prefix + " "):
                matched_prefix = prefix
                break
        if matched_prefix is None and words[0] in self._single:
            matched_prefix = words[0]

        if matched_prefix is None:
            return text

        # Get the argument (everything after the prefix)
        arg = text[len(matched_prefix):].strip()
        if not arg:
            return text

        # Look up known args for this prefix
        known = KNOWN_ARGS.get(matched_prefix, [])
        if not known:
            return text  # free-form argument (e.g. "search for cats") — keep as-is

        matches = difflib.get_close_matches(arg, known, n=1, cutoff=0.45)
        if matches:
            corrected = matched_prefix + " " + matches[0]
            if corrected != text:
                print(f"🔧 Corrected: '{text}' → '{corrected}'")
            return corrected

        return text

    def _is_valid(self, text: str) -> bool:
        if not text:
            return False
        words = text.split()
        if not words:
            return False

        # Single-word commands
        if len(words) == 1 and words[0] in self._single:
            return True

        # Multi-word prefixes (checked longest first)
        for prefix in self._multi:
            if text == prefix or text.startswith(prefix + " "):
                return True

        # Single-word prefix with arguments (e.g. "open notepad")
        if words[0] in self._single and len(words) >= 2:
            return True

        return False

    def listen(self):
        if not self.ready:
            return
        self.calibrate()
        last_yield = 0.0

        print("🔊 Listening for voice commands …\n")

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            blocksize=1600, dtype="float32",
                            callback=self._audio_callback):
            while True:
                try:
                    kind, chunk = self._audio_q.get(timeout=0.1)
                except queue.Empty:
                    continue

                if kind == "AUDIO":
                    pcm = (chunk * 32768).astype(np.int16).tobytes()
                    self._rec.AcceptWaveform(pcm)

                elif kind == "END":
                    res  = json.loads(self._rec.FinalResult())
                    text = res.get("text", "").strip().lower()

                    # Fix common misheard words before validation
                    text = _apply_phonetic_fixes(text)

                    if not self._is_valid(text):
                        if text:
                            print(f"🚫 Ignored (no prefix match): '{text}'")
                        continue

                    # Apply fuzzy correction to argument words
                    text = self._fuzzy_correct(text)

                    now = time.time()
                    if now - last_yield < self.cooldown:
                        print(f"⏳ Cooldown — ignored: '{text}'")
                        continue

                    last_yield = now
                    print(f"✅ COMMAND: '{text}'")
                    yield text


# ── Phonetic correction dictionary ───────────────────────────────────────────
# Maps what the small Indian-accent Vosk model commonly mishears
# → to the correct command word.
# Add more entries here as you discover new misheard words.
PHONETIC_FIXES = {
    # App names
    "northward":    "notepad",
    "north pad":    "notepad",
    "note bad":     "notepad",
    "no bad":       "notepad",
    "not bad":      "notepad",
    "no pad":       "notepad",
    "go will":      "google",
    "go well":      "google",
    "goal":         "google",
    "gogol":        "google",
    "google will":  "google",
    "you too":      "youtube",
    "you tube":     "youtube",
    "you to":       "youtube",
    "from":         "chrome",
    "crom":         "chrome",
    "crome":        "chrome",
    "exam":         "excel",
    "excell":       "excel",
    "note paid":    "notepad",
    "file":         "files",
    "file manager": "files",
    "dis cord":     "discord",
    "fire fox":     "firefox",
    "face book":    "facebook",
    "linked in":    "linkedin",
    "what's app":   "whatsapp",
    "what sap":     "whatsapp",
    "insta":        "instagram",
    "you tube search": "youtube",
    "git hub":      "github",
    "net fix":      "netflix",
    "amazon in":    "amazon",

    # Commands
    "school up":    "scroll up",
    "school down":  "scroll down",
    "screw up":     "scroll up",
    "screw down":   "scroll down",
    "scroll of":    "scroll up",
    "clique":       "click",
    "clip":         "click",
    "left clique":  "left click",
    "right clique": "right click",
    "double clique":"double click",
    "muted":        "mute",
    "un mute":      "unmute",
    "un do":        "undo",
    "re do":        "redo",
    "cop e":        "copy",
    "past":         "paste",
    "screen shot":  "screenshot",
    "screen shut":  "screenshot",
    "mike":         "mute",
    "mike off":     "mute",
    "full-screen":  "fullscreen",
    "full screen":  "fullscreen",
    "book mark":    "bookmark",
    "tab switch":   "next tab",
    "next step":    "next tab",
    "previous step":"previous tab",
    "volume of":    "volume off",
    "volume app":   "volume up",
    "loud her":     "louder",
    "quit her":     "quieter",

    # Websites
    "male":         "gmail",
    "g mail":       "gmail",
    "meet":         "meet",
    "cal":          "calendar",
    "cali":         "calendar",
    "dr. eve":      "drive",
    "clause":       "claude",
    "chat g p t":   "chatgpt",
    "chat gpt":     "chatgpt",
    "notion":       "notion",
    "fig ma":       "figma",

    # Modes
    "i":            "eye",
    "ai":           "eye",
    "smile mode":   "smile",
    "head mode":    "head",
    "voice mode":   "voice",
    "keyboard mode":"keyboard",
}


def _apply_phonetic_fixes(text: str) -> str:
    """
    Replace misheard words/phrases with their correct equivalents.
    Checks multi-word fixes first (longest match wins),
    then falls back to single-word replacement.
    """
    if not text:
        return text

    # Direct full-phrase match
    if text in PHONETIC_FIXES:
        corrected = PHONETIC_FIXES[text]
        print(f"🔧 Corrected: '{text}' → '{corrected}'")
        return corrected

    # Check all multi-word fixes as substrings
    result = text
    for wrong, right in sorted(PHONETIC_FIXES.items(),
                                key=lambda x: len(x[0]), reverse=True):
        if " " in wrong and wrong in result:
            result = result.replace(wrong, right)

    # Single-word token replacement
    tokens = result.split()
    corrected_tokens = []
    for token in tokens:
        fix = PHONETIC_FIXES.get(token)
        corrected_tokens.append(fix if fix else token)
    result = " ".join(corrected_tokens)

    if result != text:
        print(f"🔧 Corrected: '{text}' → '{result}'")

    return result


# ── Backwards-compatible alias ────────────────────────────────────────────────
RobustVoskListener = SmartListener