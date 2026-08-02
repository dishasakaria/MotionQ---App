"""
command_router.py
-----------------
Modular command router for the offline Windows accessibility assistant.

Receives a parsed intent result (from IntentParser) and dispatches it to the
appropriate action module.  Designed for easy future expansion: adding a new
intent requires only one entry in the ``_ROUTE_TABLE``.

Architecture
------------
::

    WhisperEngine  →  IntentParser  →  CommandRouter  →  actions/*
         │                  │                │
    raw audio          {"intent":...,    route()
                        "confidence":...,
                        "matched_phrase":...}

Supported intents
-----------------
* OPEN_GOOGLE   → actions/browser_actions.py  :: open_google()
* OPEN_EXCEL    → actions/system_actions.py   :: open_excel()
* OPEN_PAINT    → actions/system_actions.py   :: open_paint()
* NEXT_SLIDE    → actions/window_actions.py   :: next_slide()
* TYPE_TEXT     → actions/typing_actions.py   :: type_text(text)

Installation
------------
    pip install pyautogui   # required by window_actions & typing_actions

Usage
-----
    from command_router import CommandRouter

    router = CommandRouter()

    result = {
        "intent": "OPEN_EXCEL",
        "confidence": 0.84,
        "matched_phrase": "open excel",
    }
    success = router.route(result)

    # Pass original speech for intents that need content (TYPE_TEXT):
    success = router.route(
        {"intent": "TYPE_TEXT", "confidence": 0.91, "matched_phrase": "type"},
        original_text="type hello world",
    )
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Action modules
# ---------------------------------------------------------------------------
from actions.browser_actions import open_google, open_youtube, search_google
from actions.system_actions import open_excel, open_paint, open_notepad
from actions.typing_actions import type_text, press_enter, backspace
from actions.window_actions import next_slide, previous_slide, alt_tab, close_window

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("CommandRouter")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

#: Shape produced by IntentParser.parse() / passed into route().
IntentResult = dict  # {"intent": str|None, "confidence": float, "matched_phrase": str}

#: A zero-argument callable that executes the action and returns bool.
ActionCallable = Callable[[], bool]


# ---------------------------------------------------------------------------
# Confidence gate
# ---------------------------------------------------------------------------

#: Intents below this threshold are silently ignored even if IntentParser
#: accepted them.  Acts as a second safety net.
#: Scale: 0.0 – 1.0  (IntentParser normalises rapidfuzz's 0–100 to 0–1).
DEFAULT_MIN_CONFIDENCE: float = 0.70


# ---------------------------------------------------------------------------
# TYPE_TEXT keyword extractor
# ---------------------------------------------------------------------------

# Words that trigger the TYPE_TEXT intent but should NOT themselves be typed.
_TYPE_TRIGGER_WORDS = re.compile(
    r"^\s*(type|write|enter\s+text)\s*",
    re.IGNORECASE,
)


def _extract_type_payload(original_text: Optional[str]) -> str:
    """
    Strip the trigger keyword from the original transcription so only the
    payload is typed into the target window.

    Examples
    --------
    >>> _extract_type_payload("type hello world")
    'hello world'
    >>> _extract_type_payload("write this is a test")
    'this is a test'
    >>> _extract_type_payload(None)
    ''
    """
    if not original_text:
        return ""
    return _TYPE_TRIGGER_WORDS.sub("", original_text).strip()


# ---------------------------------------------------------------------------
# CommandRouter
# ---------------------------------------------------------------------------

class CommandRouter:
    """
    Routes parsed intent results to their corresponding action functions.

    Design principles
    -----------------
    * **Single entry point** — callers always use ``route()``.
    * **Table-driven dispatch** — adding a new intent means one dict entry;
      no ``if/elif`` chains.
    * **Thread-safe** — the route table is built once at init; ``route()``
      only reads it and is safe to call from multiple threads concurrently.
    * **Failure isolation** — each action is wrapped in a try/except so one
      failing module never crashes the router.

    Parameters
    ----------
    min_confidence:
        Minimum confidence score [0.0–1.0] required to execute an action.
        Intents below this value are logged and silently dropped.
    """

    def __init__(self, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(
                f"min_confidence must be in [0.0, 1.0]; got {min_confidence}."
            )

        self._min_confidence = min_confidence
        self._lock = threading.Lock()  # guards dynamic table mutations only

        # ------------------------------------------------------------------
        # Route table
        # ------------------------------------------------------------------
        # Maps intent name → factory that returns a zero-arg ActionCallable.
        # Using a factory (lambda) lets us capture ``original_text`` lazily
        # at dispatch time for intents that need it (e.g. TYPE_TEXT).
        #
        # To add a new intent:
        #   1. Import the action function above.
        #   2. Add one entry here: "NEW_INTENT": lambda _: your_action_fn
        #
        # The factory receives ``original_text`` as its sole argument.
        # ------------------------------------------------------------------
        self._route_table: dict[str, Callable[[Optional[str]], ActionCallable]] = {
            # Browser
            "OPEN_GOOGLE":   lambda _ot: open_google,
            "OPEN_YOUTUBE":  lambda _ot: open_youtube,
            "SEARCH_GOOGLE": lambda ot:  (lambda: search_google(_extract_type_payload(ot))),

            # System apps
            "OPEN_EXCEL":    lambda _ot: open_excel,
            "OPEN_PAINT":    lambda _ot: open_paint,
            "OPEN_NOTEPAD":  lambda _ot: open_notepad,

            # Window / presentation control
            "NEXT_SLIDE":     lambda _ot: next_slide,
            "PREVIOUS_SLIDE": lambda _ot: previous_slide,
            "ALT_TAB":        lambda _ot: alt_tab,
            "CLOSE_WINDOW":   lambda _ot: close_window,

            # Typing
            "TYPE_TEXT":    lambda ot: (lambda: type_text(_extract_type_payload(ot))),
            "PRESS_ENTER":  lambda _ot: press_enter,
            "BACKSPACE":    lambda _ot: backspace,
        }

        logger.info(
            "CommandRouter ready — %d intents registered, min_confidence=%.2f",
            len(self._route_table),
            self._min_confidence,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(
        self,
        result: IntentResult,
        original_text: Optional[str] = None,
    ) -> bool:
        """
        Dispatch an intent result to its action handler.

        Parameters
        ----------
        result:
            Dict produced by ``IntentParser.parse()``:
            ``{"intent": str|None, "confidence": float, "matched_phrase": str}``.
        original_text:
            The raw transcribed speech string.  Required for intents that need
            content from the utterance (e.g. TYPE_TEXT).  Optional for others.

        Returns
        -------
        bool
            ``True``  — action was dispatched and reported success.
            ``False`` — intent was ``None``, below threshold, unknown,
                        or the action itself failed.
        """
        intent     = result.get("intent")
        confidence = result.get("confidence", 0.0)
        phrase     = result.get("matched_phrase", "")

        # --- Guard: no intent detected --------------------------------------
        if intent is None:
            logger.debug("route() called with intent=None — nothing to do.")
            return False

        # --- Guard: confidence too low -------------------------------------
        if confidence < self._min_confidence:
            logger.warning(
                "Intent %r rejected — confidence %.2f < threshold %.2f. "
                "(matched_phrase=%r)",
                intent,
                confidence,
                self._min_confidence,
                phrase,
            )
            return False

        # --- Resolve handler -----------------------------------------------
        with self._lock:
            factory = self._route_table.get(intent)

        if factory is None:
            logger.error(
                "Unknown intent %r — no handler registered.  "
                "Add an entry to CommandRouter._route_table.",
                intent,
            )
            return False

        # --- Dispatch -------------------------------------------------------
        logger.info(
            "Dispatching intent=%r  confidence=%.2f  matched=%r  text=%r",
            intent,
            confidence,
            phrase,
            original_text,
        )

        action: ActionCallable = factory(original_text)

        try:
            success: bool = action()
        except Exception as exc:  # noqa: BLE001
            # Isolate the action from crashing the router.
            logger.error(
                "Unhandled exception in action for intent %r: %s",
                intent,
                exc,
                exc_info=True,
            )
            return False

        if success:
            logger.info("✓ Action for intent %r completed successfully.", intent)
        else:
            logger.warning("✗ Action for intent %r reported failure.", intent)

        return success

    # ------------------------------------------------------------------
    # Runtime configuration
    # ------------------------------------------------------------------

    def register(
        self,
        intent: str,
        factory: Callable[[Optional[str]], ActionCallable],
    ) -> None:
        """
        Register a new intent handler at runtime (thread-safe).

        This lets external modules extend the router without modifying this
        file — useful for plugin-style architectures.

        Parameters
        ----------
        intent:
            Intent name string, e.g. ``"OPEN_NOTEPAD"``.
        factory:
            Callable ``(original_text) → () → bool``.
            Must return a zero-argument callable that executes the action.

        Example
        -------
        ::

            from actions.system_actions import open_notepad

            router.register(
                "OPEN_NOTEPAD",
                lambda _ot: open_notepad,
            )
        """
        with self._lock:
            self._route_table[intent] = factory
        logger.info("Registered new handler for intent %r.", intent)

    def unregister(self, intent: str) -> None:
        """
        Remove an intent handler at runtime (thread-safe).

        Parameters
        ----------
        intent:
            Intent name to remove.  No-op if not registered.
        """
        with self._lock:
            removed = self._route_table.pop(intent, None)
        if removed:
            logger.info("Unregistered handler for intent %r.", intent)
        else:
            logger.debug("unregister(%r): intent was not registered.", intent)

    def set_min_confidence(self, value: float) -> None:
        """
        Update the minimum confidence threshold at runtime.

        Parameters
        ----------
        value:
            New threshold in [0.0, 1.0].
        """
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"Threshold must be in [0.0, 1.0]; got {value}.")
        self._min_confidence = value
        logger.info("min_confidence updated to %.2f", value)

    @property
    def registered_intents(self) -> list[str]:
        """Return a sorted list of currently registered intent names."""
        with self._lock:
            return sorted(self._route_table.keys())

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"CommandRouter("
            f"intents={self.registered_intents}, "
            f"min_confidence={self._min_confidence})"
        )


# ---------------------------------------------------------------------------
# Example / smoke-test  (run file directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    router = CommandRouter(min_confidence=0.70)
    print(f"\n{router}\n")

    # Simulated IntentParser outputs.
    test_cases: list[tuple[IntentResult, Optional[str]]] = [
        # (result_dict,                                          original_text)
        ({"intent": "OPEN_GOOGLE", "confidence": 0.91, "matched_phrase": "open google"},  None),
        ({"intent": "OPEN_EXCEL",  "confidence": 0.84, "matched_phrase": "open excel"},   None),
        ({"intent": "OPEN_PAINT",  "confidence": 0.88, "matched_phrase": "open paint"},   None),
        ({"intent": "NEXT_SLIDE",  "confidence": 0.79, "matched_phrase": "next slide"},   None),
        ({"intent": "TYPE_TEXT",   "confidence": 0.92, "matched_phrase": "type"},         "type hello world"),
        # Below threshold — should be silently dropped.
        ({"intent": "OPEN_GOOGLE", "confidence": 0.55, "matched_phrase": "open google"},  None),
        # None intent — should be skipped.
        ({"intent": None,          "confidence": 0.0,  "matched_phrase": ""},             None),
        # Unknown intent — should log an error.
        ({"intent": "OPEN_NOTEPAD","confidence": 0.95, "matched_phrase": "open notepad"}, None),
    ]

    print("-" * 60)
    for result, original in test_cases:
        print(f"\n→ Routing: intent={result['intent']!r}  conf={result['confidence']}")
        success = router.route(result, original_text=original)
        print(f"  Result: {'success ✓' if success else 'skipped / failed ✗'}")

    # --- Dynamic registration example --------------------------------------
    print("\n" + "-" * 60)
    print("Registering OPEN_NOTEPAD dynamically …")

    def _open_notepad() -> bool:
        import subprocess
        try:
            subprocess.Popen("notepad.exe", shell=True)
            return True
        except OSError:
            return False

    router.register("OPEN_NOTEPAD", lambda _ot: _open_notepad)
    print(f"Intents now: {router.registered_intents}")

    result = {"intent": "OPEN_NOTEPAD", "confidence": 0.95, "matched_phrase": "open notepad"}
    print(f"\n→ Routing: intent=OPEN_NOTEPAD  conf=0.95")
    success = router.route(result)
    print(f"  Result: {'success ✓' if success else 'failed ✗'}")