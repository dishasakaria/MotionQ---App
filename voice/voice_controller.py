"""
voice/voice_controller.py
─────────────────────────────────────────────────────────────────────────────
Active Voice Mode Controller.

Role in the full system
───────────────────────
  mainController.py
      └─► StrictListener  (Vosk — passive, always-on face/mode switcher)
              └─► VoiceController.run()   ← THIS FILE
                      └─► WhisperEngine   (high-accuracy offline STT)
                      └─► ParameterParser (intent + slot extraction)
                      └─► IntentRouter    (dispatches to VoiceCommandController)
                              └─► VoiceCommandController  (existing Windows actions)

This controller is active ONLY while the user is in voice mode.
It does NOT implement any Windows actions itself; those all live in
voice_commands.py and are reached through IntentRouter.

Exit phrases ("stop voice", "exit voice", "quit voice", "back to face",
"face control") cause run() to return so mainController.py can resume.

Author : <your name>
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

# ── Modular pipeline ──────────────────────────────────────────────────────────
from .whisper_engine import WhisperEngine
from .parameter_parser import ParameterParser
from .intent_router import IntentRouter         # routes parsed intent to actions

# ── Existing action layer — DO NOT duplicate ──────────────────────────────────
from voice_commands import VoiceCommandController

# ─────────────────────────────────────────────────────────────────────────────
# Module-level logger  (inherits root config set by mainController.py)
# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Phrases that signal the user wants to leave voice mode.
# Compared case-insensitively after stripping punctuation.
_EXIT_PHRASES: frozenset[str] = frozenset({
    "stop voice",
    "exit voice",
    "quit voice",
    "back to face",
    "face control",
})

# Minimum gap (seconds) between two processed commands to prevent
# Whisper echoes or mic bleed from triggering duplicate actions.
_DEFAULT_COOLDOWN_SEC: float = 1.0

# Maximum items buffered between the listener thread and the command worker.
_QUEUE_MAX: int = 24

# Seconds the listener/worker threads wait on an empty queue before
# re-checking the stop flag.  Keeps shutdown responsive.
_QUEUE_TIMEOUT: float = 0.15


# ─────────────────────────────────────────────────────────────────────────────
# VoiceController
# ─────────────────────────────────────────────────────────────────────────────

class VoiceController:
    """
    Active voice mode controller.

    Lifecycle (called by mainController.py)
    ───────────────────────────────────────
        vc = VoiceController()
        vc.run()              # blocks until exit phrase or stop_flag set
        # — user is now back in face/passive mode —

    Thread layout
    ─────────────
        caller thread : calls run(); blocks until voice mode ends
        _listen_thread: drives WhisperEngine; pushes transcripts onto _q
        _worker_thread: pops from _q; parses + routes each command
    """

    # ── construction ─────────────────────────────────────────────────────────

    def __init__(
        self,
        cooldown_sec: float = _DEFAULT_COOLDOWN_SEC,
        whisper: Optional[WhisperEngine] = None,
        parser: Optional[ParameterParser] = None,
        action_controller: Optional[VoiceCommandController] = None,
        router: Optional[IntentRouter] = None,
        
    ) -> None:
        """
        Parameters
        ──────────
        cooldown_sec
            Minimum seconds between two executed commands.  Prevents
            accidental duplicate execution from Whisper re-transcribing
            the same audio segment.
        whisper / parser / action_controller / router
            Optional pre-built instances for testing or dependency
            injection.  When omitted, defaults are constructed here.
        """
        log.debug("VoiceController: initialising …")

        # ── STT ──────────────────────────────────────────────────────────────
        try:
            self._whisper: Optional[WhisperEngine] = whisper or WhisperEngine()
        except RuntimeError as e:
            log.error(str(e))
            self._whisper = None

        # ── NLU ──────────────────────────────────────────────────────────────
        self._parser: ParameterParser = parser or ParameterParser()

        # ── Existing Windows action layer (DO NOT reimplement) ────────────────
        self._actions: VoiceCommandController = (
            action_controller or VoiceCommandController()
        )

        # ── Router: bridges parsed intent → action controller ─────────────────
        self._router: IntentRouter = router or IntentRouter(self._actions)

        # ── Cooldown ──────────────────────────────────────────────────────────
        self._cooldown_sec: float = cooldown_sec
        self._last_cmd_time: float = 0.0
        # ── Duplicate suppression ─────────────────────────────────────────────
        self.last_command = ""
        self.last_command_time = 0
        # ── Internal transcription queue ──────────────────────────────────────
        self._q: queue.Queue[Optional[str]] = queue.Queue(maxsize=_QUEUE_MAX)

        # ── Threading state ───────────────────────────────────────────────────
        self._stop_event = threading.Event()
        self._exit_voice_event = threading.Event()   # set when exit phrase heard
        self._listen_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None

        # ── Lifecycle guard ───────────────────────────────────────────────────
        self._running = False

        log.info(
            "VoiceController ready  |  cooldown=%.1fs  whisper=%s  router=%s",
            self._cooldown_sec,
            type(self._whisper).__name__,
            type(self._router).__name__,
        )
    
    def is_valid_transcript(self, text):
        text = text.lower().strip()

        if not text:
            return False

    # reject tiny garbage
        if len(text.split()) < 2:
            return False

    # common whisper hallucinations
        banned = {
        "thank you",
        "thanks",
        "thanks and",
        "thanks and...",
        "okay",
        "bye",
        "you",
        "open",
        ".",
        "...",
        }
        if text in banned:
            return False
        
        words = text.split()

        if len(words) >= 4:
            unique_ratio = len(set(words)) / len(words)
            if unique_ratio < 0.5:
                return False
        return True
    
    def is_duplicate(self, text):
        now = time.time()
        if (
            text == self.last_command
            and now - self.last_command_time < 3
        ):
            return True

        self.last_command = text
        self.last_command_time = now

        return False
    # ── Public API ────────────────────────────────────────────────────────────
    
    def start(self) -> None:
        """
        Spin up background threads and begin capturing audio.

        Normally you call run() instead, which calls start() internally and
        then blocks.  Call start() directly only if you need non-blocking
        behaviour (e.g. in tests).
        """
        if self._running:
            log.warning("VoiceController.start() called while already running — ignored.")
            return

        self._stop_event.clear()
        self._exit_voice_event.clear()
        self._last_cmd_time = 0.0
        self._running = True

        self._listen_thread = threading.Thread(
            target=self._listen_loop,
            name="vc-listen",
            daemon=True,
        )
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="vc-worker",
            daemon=True,
        )

        self._listen_thread.start()
        self._worker_thread.start()

        log.info("VoiceController started — active voice mode engaged.")

    def stop(self) -> None:
        """
        Signal threads to stop and wait for them to finish.
        Idempotent: safe to call multiple times.
        """
        if not self._running:
            return

        log.info("VoiceController: stopping …")
        self._stop_event.set()

        # Ask WhisperEngine to release the microphone
        try:
            self._whisper.stop()
        except Exception:
            log.debug("WhisperEngine.stop() raised (ignored).", exc_info=True)

        # Unblock any blocking queue.get() with a sentinel
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

        for t in (self._listen_thread, self._worker_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
                if t.is_alive():
                    log.warning("Thread %r did not exit within timeout.", t.name)

        self._running = False
        log.info("VoiceController: stopped.")

    def run(self, stop_flag: Optional[threading.Event] = None) -> None:
        """
        Start voice mode and block until one of:
          • an exit phrase is recognised ("stop voice", "back to face", …)
          • stop_flag is set by the caller (mainController.py)
          • stop() is called from another thread

        Parameters
        ──────────
        stop_flag
            An optional threading.Event owned by mainController.py.
            When set externally (e.g. face-detection regains priority),
            this method returns cleanly without needing an exit phrase.

        Integration example in mainController.py
        ─────────────────────────────────────────
            voice_stop = threading.Event()
            vc = VoiceController()
            vc.run(stop_flag=voice_stop)
            # execution resumes here after voice mode ends
        """
        self.start()

        try:
            while True:
                # Exit phrase detected by worker thread
                if self._exit_voice_event.is_set():
                    log.info("Exit phrase received — leaving voice mode.")
                    break

                # External stop requested by mainController.py
                if stop_flag is not None and stop_flag.is_set():
                    log.info("External stop_flag set — leaving voice mode.")
                    break

                # Internal stop (e.g. unrecoverable error in a thread)
                if self._stop_event.is_set():
                    log.info("Internal stop event set — leaving voice mode.")
                    break

                time.sleep(0.05)

        except KeyboardInterrupt:
            log.info("KeyboardInterrupt in voice mode — stopping.")

        finally:
            self.stop()

    def process_command(self, text: str) -> bool:
        """
        Parse and execute a single command string.

        Intended for direct calls (tests, injected text, GUI overrides).
        Bypasses WhisperEngine but goes through the full
        ParameterParser → IntentRouter pipeline.

        Returns True if the command was executed, False otherwise
        (empty text, cooldown active, unrecognised intent, exit phrase).

        Note: exit phrases detected here set the exit event so run()
        also terminates.
        """
        text = text.strip()
        if not text:
            return False

        # Check for exit phrases first
        if self._is_exit_phrase(text):
            log.info("process_command: exit phrase %r — signalling stop.", text)
            self._exit_voice_event.set()
            return False

        # Cooldown guard
        now = time.monotonic()
        if now - self._last_cmd_time < self._cooldown_sec:
            remaining = self._cooldown_sec - (now - self._last_cmd_time)
            log.debug(
                "process_command: cooldown %.2fs remaining — dropping %r.",
                remaining, text,
            )
            return False

        self._last_cmd_time = now
        return self._dispatch(text)

    def cleanup(self) -> None:
        """
        Release all resources held by this controller.

        Call this when discarding the VoiceController instance, especially
        if it was never started (avoids open handles from WhisperEngine
        model loading).
        """
        log.debug("VoiceController.cleanup() called.")
        self.stop()

        # Give sub-components a chance to free resources
        for component_name, component in (
            ("WhisperEngine", self._whisper),
            ("ParameterParser", self._parser),
            ("VoiceCommandController", self._actions),
            ("IntentRouter", self._router),
        ):
            cleanup_fn = getattr(component, "cleanup", None)
            if callable(cleanup_fn):
                try:
                    cleanup_fn()
                    log.debug("cleanup: %s.cleanup() called.", component_name)
                except Exception:
                    log.debug("cleanup: %s.cleanup() raised (ignored).", component_name,
                              exc_info=True)

        log.info("VoiceController: cleanup complete.")

    # ── Thread: listen ────────────────────────────────────────────────────────

    def _listen_loop(self) -> None:
        """
        Continuously call WhisperEngine.get_text() and push non-empty
        transcripts onto the internal queue.

        WhisperEngine contract
        ──────────────────────
          .start()          → open mic + load model (already called by start())
          .get_text()       → blocks until an utterance completes;
                              returns str (may be empty) or None on silence
          .stop()           → release mic (called by stop())
        """
        log.debug("_listen_loop: started.")

        # WhisperEngine was already started in start(); don't call it again.
        # If your WhisperEngine.start() is idempotent, calling it here is safe.
        try:
            if self._whisper is None:
                 log.error("Groq voice unavailable: API key is not configured.")
                 return
            self._whisper.start()
        except Exception as exc:
            log.critical("_listen_loop: WhisperEngine.start() failed: %s", exc,
                         exc_info=True)
            self._stop_event.set()
            return

        while not self._stop_event.is_set():
            try:
                raw: Optional[str] = (
                    self._whisper.get_text()
                    if self._whisper is not None
                    else None
                )
            except Exception as exc:
                log.error("_listen_loop: WhisperEngine.get_text() error: %s", exc,
                          exc_info=True)
                # Brief pause before retrying to avoid a tight error loop
                time.sleep(0.25)
                continue

            if not raw or not raw.strip():
                # Silence or empty frame — nothing to do
                continue

            transcript = raw.strip()
            log.debug("_listen_loop: transcript: %r", transcript)

            try:
                self._q.put_nowait(transcript)
            except queue.Full:
                log.warning(
                    "_listen_loop: command queue full — dropping transcript: %r",
                    transcript,
                )

        log.debug("_listen_loop: exited.")

    # ── Thread: worker ────────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        """
        Pop transcripts from the queue, filter exit phrases, enforce
        cooldown, then route to the action layer.
        """
        log.debug("_worker_loop: started.")

        while not self._stop_event.is_set():
            try:
                text: Optional[str] = self._q.get(timeout=_QUEUE_TIMEOUT)
            except queue.Empty:
                continue

            # None sentinel → shutdown signal
            if text is None:
                break

            # process_command handles exit phrases, cooldown, dispatch
            self.process_command(text)

            # If an exit phrase was detected, wake the run() loop immediately
            if self._exit_voice_event.is_set():
                break

        log.debug("_worker_loop: exited.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _is_exit_phrase(text: str) -> bool:
        """
        Return True if text (case-insensitive, punctuation-stripped) matches
        one of the recognised exit phrases.
        """
        normalised = text.lower().strip(" .!?,")
        return normalised in _EXIT_PHRASES

    def _dispatch(self, text: str) -> bool:
        """
        Run the ParameterParser → IntentRouter pipeline for one command.

        Returns True on success, False if parsing or routing fails.
        All exceptions are caught; the worker loop must never crash.
        """
        log.info("Executing command: %r", text)
    
    
        # ── Step 1: parse intent + parameters ────────────────────────────────
        try:
            if not self.is_valid_transcript(text):
                return

            if self.is_duplicate(text):
                return
            
            parsed = self._parser.parse(text)
            print("PARSED RESULT:", parsed)
        except Exception as exc:
            log.error("_dispatch: ParameterParser.parse(%r) failed: %s", text, exc,
                      exc_info=True)
            return False

        if parsed is None:
            log.warning("_dispatch: ParameterParser returned None for: %r", text)
            return False

        log.debug("_dispatch: parsed intent → %r", parsed)

    

        # ── Step 2: route to existing VoiceCommandController actions ──────────
        try:
            result = self._router.route(parsed)
        except Exception as exc:
            log.error("_dispatch: IntentRouter.route(%r) failed: %s", parsed, exc,
                      exc_info=True)
            return False

        log.debug("_dispatch: route result → %r", result)
        log.info("Command done: %r", text)
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Integration notes & example
# ─────────────────────────────────────────────────────────────────────────────

"""
─────────────────────────────────────────────────────────────────────────────
INTEGRATION NOTES
─────────────────────────────────────────────────────────────────────────────

1.  HOW mainController.py SHOULD CALL THIS
    ─────────────────────────────────────────

    # Inside mainController.py — the passive face/mode loop:

    from voice.voice_controller import VoiceController

    def on_voice_mode_activated():
        \"\"\"Called by StrictListener when wake gesture/word is detected.\"\"\"
        voice_stop = threading.Event()   # optionally set this to force exit

        vc = VoiceController()
        try:
            vc.run(stop_flag=voice_stop)
        finally:
            vc.cleanup()
        # Execution returns here → StrictListener / face mode resumes

2.  EXIT PHRASES
    ─────────────
    Any of the following return control to mainController.py:
        "stop voice"    "exit voice"    "quit voice"
        "back to face"  "face control"

3.  SUPPORTED COMMAND EXAMPLES (handled by existing VoiceCommandController)
    ────────────────────────────────────────────────────────────────────────
        "open excel"
        "open youtube"
        "search google for AI tools"
        "type hello world"
        "next tab"
        "scroll down"
        "close window"
        … (see voice_commands.py for the full list)

4.  DEPENDENCY INJECTION (unit tests)
    ────────────────────────────────────
    from unittest.mock import MagicMock
    from voice.voice_controller import VoiceController

    mock_whisper = MagicMock()
    mock_whisper.get_text.side_effect = ["open excel", "stop voice"]

    vc = VoiceController(whisper=mock_whisper)
    vc.run()   # exits after "stop voice"

5.  COMPONENT CONTRACTS
    ──────────────────────
    WhisperEngine:
        .start()               → initialise mic + model
        .get_text() → str|None → blocks until utterance; None = silence
        .stop()                → release mic

    ParameterParser:
        .parse(text: str) → Any|None → returns intent object or None

    IntentRouter:
        __init__(controller: VoiceCommandController)
        .route(parsed: Any) → Any → dispatches to VoiceCommandController

    VoiceCommandController:
        (unchanged — all Windows actions already implemented here)

─────────────────────────────────────────────────────────────────────────────
"""

if __name__ == "__main__":
    # Quick smoke-test without mainController.py
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(name)s  │  %(message)s",
        datefmt="%H:%M:%S",
    )

    print(
        "\n"
        "  ╔═══════════════════════════════════════════════════╗\n"
        "  ║   VoiceController — standalone test               ║\n"
        "  ║   Voice mode is ACTIVE.  No wake word needed.     ║\n"
        "  ║   Say 'stop voice' or 'back to face' to exit.     ║\n"
        "  ║   Ctrl-C also exits cleanly.                      ║\n"
        "  ╚═══════════════════════════════════════════════════╝\n"
    )

    vc = VoiceController(cooldown_sec=1.0)
    try:
        vc.run()
    finally:
        vc.cleanup()

    print("\n  Voice mode ended.  mainController.py would resume here.\n")