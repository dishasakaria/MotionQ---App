"""
intent_router.py
----------------
Adapter layer that bridges the parsed-intent system (parameter_parser.py)
to the existing VoiceCommandController action system (voice_commands.py).

Architecture
~~~~~~~~~~~~
    ParsedResult  ──►  IntentRouter.route()  ──►  _intent_to_command()
                                                         │
                                                         ▼
                                               VoiceCommandController.execute(command_text)

IMPORTANT — Adapter pattern explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
VoiceCommandController is a *command-text-driven* system. Its public API is:

    controller.execute(command_text: str) -> Any

It does NOT expose modular methods such as open_application(), open_website(),
search_google(), etc. Calling those would raise AttributeError at runtime.

IntentRouter therefore acts as an *adapter*: it converts structured intents
(OPEN_APPLICATION, SEARCH_GOOGLE, …) back into the normalised command-text
strings that VoiceCommandController.execute() already understands, then
delegates the actual execution to that single entry point.

This keeps the router fully decoupled from any future changes to the
controller's internal command parsing logic.

Design principles
~~~~~~~~~~~~~~~~~
- Zero re-implementation: every action delegates to VoiceCommandController.execute().
- Single responsibility: this module only handles intent → command-text conversion
  and routing logic.
- Fail loudly on unknown intents; fail gracefully on missing parameters.
- Easy to extend: add a new intent by inserting one entry in _build_dispatch_table().
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent catalogue
# ---------------------------------------------------------------------------

@unique
class Intent(str, Enum):
    """All intents that IntentRouter understands.

    Values match the canonical intent strings produced by parameter_parser.py
    so that ``Intent(parsed_result.intent)`` always succeeds for known intents.
    """
    PRESS_ENTER = "PRESS_ENTER"

    PRESS_BACKSPACE = "PRESS_BACKSPACE"
    OPEN_DOWNLOADS = "OPEN_DOWNLOADS"
    OPEN_DESKTOP = "OPEN_DESKTOP"
    OPEN_DOCUMENTS = "OPEN_DOCUMENTS"
    OPEN_FILE_EXPLORER = "OPEN_FILE_EXPLORER"

    LOCK_SCREEN = "LOCK_SCREEN"

    VOLUME_UP = "VOLUME_UP"
    VOLUME_DOWN = "VOLUME_DOWN"
    MUTE_VOLUME = "MUTE_VOLUME"
    START_PRESENTATION = "START_PRESENTATION"
    NEXT_SLIDE = "NEXT_SLIDE"
    PREVIOUS_SLIDE = "PREVIOUS_SLIDE"
    BLACK_SCREEN = "BLACK_SCREEN"
    EXIT_PRESENTATION = "EXIT_PRESENTATION"

    TAKE_SCREENSHOT = "TAKE_SCREENSHOT"
    SELECT_ALL = "SELECT_ALL"
    OPEN_APPLICATION = "OPEN_APPLICATION"
    OPEN_WEBSITE     = "OPEN_WEBSITE"
    SEARCH_GOOGLE    = "SEARCH_GOOGLE"
    SEARCH_YOUTUBE = "SEARCH_YOUTUBE"
    TYPE_TEXT        = "TYPE_TEXT"
    SCROLL_UP        = "SCROLL_UP"
    SCROLL_DOWN      = "SCROLL_DOWN"
    CLICK            = "CLICK"
    DOUBLE_CLICK     = "DOUBLE_CLICK"
    NEXT_TAB         = "NEXT_TAB"
    PREVIOUS_TAB     = "PREVIOUS_TAB"
    CLOSE_TAB        = "CLOSE_TAB"
    NEW_TAB          = "NEW_TAB"
    REFRESH_PAGE = "REFRESH_PAGE"
    GO_BACK = "GO_BACK"
    GO_FORWARD = "GO_FORWARD"
    COPY             = "COPY"
    PASTE            = "PASTE"
    UNDO             = "UNDO"
    CLOSE_WINDOW = "CLOSE_WINDOW"
    MINIMIZE_WINDOW = "MINIMIZE_WINDOW"
    MAXIMIZE_WINDOW = "MAXIMIZE_WINDOW"
    SWITCH_APP = "SWITCH_APP"
    REDO             = "REDO"
    


# ---------------------------------------------------------------------------
# Routing result
# ---------------------------------------------------------------------------

@dataclass
class RouteResult:
    """Outcome of a single ``IntentRouter.route()`` call."""

    success: bool
    intent: str
    message: str = ""
    error: Optional[Exception] = None
    # The command-text string that was passed to controller.execute(), if any.
    command_text: Optional[str] = field(default=None, repr=True)
    # Raw return value from VoiceCommandController.execute(), if any.
    controller_result: Any = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class IntentRouter:
    """Routes a *ParsedResult* to *VoiceCommandController.execute()*.

    Because VoiceCommandController is command-text-driven (its only public
    entry point is ``execute(command_text: str)``), this router converts each
    structured intent + parameters back into the normalised command string
    that the controller already knows how to handle.

    Parameters
    ----------
    controller:
        A fully-initialised ``VoiceCommandController`` instance.  The router
        holds a reference but never owns or closes it.

    Thread safety
    -------------
    A ``threading.Lock`` serialises all calls to ``controller.execute()`` so
    that concurrent ``route()`` calls never interleave their commands.

    Example
    -------
    ::

        from voice_commands import VoiceCommandController
        from parameter_parser import parse
        from voice.intent_router import IntentRouter

        controller = VoiceCommandController()
        router = IntentRouter(controller)

        result = router.route(parse("open chrome"))
        if not result.success:
            print(f"Routing failed: {result.error}")
        else:
            print(f"Sent command: '{result.command_text}'")
            self.voice_controller.execute(result.command_text)
    """

    def __init__(self, controller: Any) -> None:
        if controller is None:
            raise ValueError("controller must not be None")
        self._ctrl = controller
        # Serialise all execute() calls for thread safety.
        self._lock = threading.Lock()
        # Build the dispatch table once at construction time.
        # Each entry maps an Intent → a callable(params) → command_text str.
        self._dispatch: Dict[Intent, Callable[[dict], str]] = (
            self._build_dispatch_table()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, parsed_result: Any) -> RouteResult:
        """Dispatch *parsed_result* via ``controller.execute(command_text)``.

        Parameters
        ----------
        parsed_result:
            Object (or dict) returned by ``parameter_parser.parse()``.
            Must expose ``.intent`` (str) and ``.parameters`` (dict).

        Returns
        -------
        RouteResult
            Always returns a ``RouteResult``; never raises.
        """
        # --- 1. Unpack the parsed result --------------------------------
        try:
            intent_str, params = self._unpack(parsed_result)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.error("Failed to unpack parsed_result: %s", exc)
            return RouteResult(
                success=False,
                intent=str(parsed_result),
                message="Invalid parsed_result format.",
                error=exc,
            )

        # --- 2. Validate the intent -------------------------------------
        try:
            intent = Intent(intent_str)
        except ValueError:
            msg = f"Unknown intent: '{intent_str}'"
            logger.warning(msg)
            return RouteResult(
                success=False,
                intent=intent_str,
                message=msg,
                error=ValueError(msg),
            )

        # --- 3. Retrieve the command-text builder -----------------------
        builder = self._dispatch.get(intent)
        if builder is None:
            # Guard against an Intent member with no registered builder.
            msg = f"No command builder registered for intent '{intent}'"
            logger.error(msg)
            return RouteResult(
                success=False,
                intent=intent_str,
                message=msg,
                error=NotImplementedError(msg),
            )

        # --- 4. Build the normalised command-text string ----------------
        try:
            command_text: str = builder(params)
        except (KeyError, TypeError) as exc:
            msg = f"Failed to build command text for intent '{intent_str}': {exc}"
            logger.error(msg)
            return RouteResult(
                success=False,
                intent=intent_str,
                message=msg,
                error=exc,
            )

        if not command_text or not isinstance(command_text, str):
            msg = f"Builder for '{intent_str}' returned an empty/invalid command."
            logger.error(msg)
            return RouteResult(
                success=False,
                intent=intent_str,
                message=msg,
                error=ValueError(msg),
            )

        # --- 5. Delegate to VoiceCommandController.execute() ------------
        #
        # This is the ONLY call site that touches the controller.
        # The adapter pattern ends here: everything above was translation,
        # everything below is the controller's responsibility.
        #
        try:
            logger.debug(
                "Routing intent=%s → execute(%r)", intent, command_text
            )
            with self._lock:  # thread-safe serialisation
                ctrl_result = self._ctrl.execute(command_text)

            return RouteResult(
                success=True,
                intent=intent_str,
                command_text=command_text,
                message=(
                    f"Intent '{intent_str}' executed successfully "
                    f"via command '{command_text}'."
                ),
                controller_result=ctrl_result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "VoiceCommandController.execute(%r) raised an exception.", command_text
            )
            return RouteResult(
                success=False,
                intent=intent_str,
                command_text=command_text,
                message=f"execute('{command_text}') failed: {exc}",
                error=exc,
            )

    # ------------------------------------------------------------------
    # Dispatch table  (intent → command-text builder)
    # ------------------------------------------------------------------

    def _build_dispatch_table(self) -> Dict[Intent, Callable[[dict], str]]:
        """Return a mapping of every Intent to a command-text builder.

        Each builder is a callable that:
        - accepts a single *params* dict
        - returns a normalised command-text string for VoiceCommandController.execute()
        - contains NO business logic — only string construction

        Normalised command-text format mirrors what VoiceCommandController
        already parses internally, e.g. "open excel", "search AI tools".
        This keeps IntentRouter as a pure adapter with zero duplicated logic.
        """
        return {
            # ----------------------------------------------------------
            # Application / browser launchers
            #
            # OPEN_APPLICATION(target="excel")  → "open excel"
            # OPEN_WEBSITE(url="youtube")        → "open youtube"
            # ----------------------------------------------------------
            Intent.OPEN_APPLICATION: lambda p: (
                f"open {p['target'].strip().lower()}"
            ),
            
Intent.CLOSE_WINDOW:
    lambda _p: "close window",
Intent.START_PRESENTATION:
    lambda _p: "start presentation",

Intent.NEXT_SLIDE:
    lambda _p: "next slide",

Intent.PREVIOUS_SLIDE:
    lambda _p: "previous slide",

Intent.BLACK_SCREEN:
    lambda _p: "black screen",

Intent.EXIT_PRESENTATION:
    lambda _p: "exit presentation",

Intent.MINIMIZE_WINDOW:
    lambda _p: "minimize window",

Intent.MAXIMIZE_WINDOW:
    lambda _p: "maximize window",

Intent.SWITCH_APP:
    lambda _p: "switch app",
Intent.OPEN_DOWNLOADS:
    lambda _p: "open downloads",

Intent.OPEN_DESKTOP:
    lambda _p: "open desktop",

Intent.OPEN_DOCUMENTS:
    lambda _p: "open documents",

Intent.OPEN_FILE_EXPLORER:
    lambda _p: "open file explorer",

Intent.LOCK_SCREEN:
    lambda _p: "lock screen",

Intent.VOLUME_UP:
    lambda _p: "volume up",

Intent.VOLUME_DOWN:
    lambda _p: "volume down",

Intent.MUTE_VOLUME:
    lambda _p: "mute volume",

Intent.TAKE_SCREENSHOT:
    lambda _p: "take screenshot",



            Intent.OPEN_WEBSITE: lambda p: (
                f"open {p['url'].strip().lower()}"
            ),
            Intent.PRESS_ENTER: lambda _p: "press enter",

            Intent.PRESS_BACKSPACE: lambda _p: "press backspace",

            Intent.SELECT_ALL: lambda _p: "select all",


            # SEARCH_GOOGLE(query="AI tools")  → "search AI tools"
            Intent.SEARCH_GOOGLE: lambda p: (
                f"search {p['query'].strip()}"
            ),

            # ----------------------------------------------------------
            # Text input
            #
            # TYPE_TEXT(text="hello world")  → "type hello world"
            # ----------------------------------------------------------
            Intent.TYPE_TEXT: lambda p: (
                f"type {p['text']}"
            ),

            # ----------------------------------------------------------
            # Scroll actions
            #
            # SCROLL_UP(amount=3)    → "scroll up 3"
            # SCROLL_DOWN(amount=3)  → "scroll down 3"
            # ----------------------------------------------------------
            Intent.SCROLL_UP: lambda p: (
                f"scroll up {p.get('amount', 3)}"
            ),

            Intent.SCROLL_DOWN: lambda p: (
                f"scroll down {p.get('amount', 3)}"
            ),
            Intent.SEARCH_YOUTUBE:
    lambda p: f"search youtube for {p['query']}",
            # ----------------------------------------------------------
            # Mouse actions
            #
            # CLICK(x=100, y=200)         → "click 100 200"
            # CLICK()                     → "click"          (cursor pos)
            # DOUBLE_CLICK(x=100, y=200)  → "double click 100 200"
            # ----------------------------------------------------------
            Intent.CLICK: lambda p: (
                f"click {p['x']} {p['y']}"
                if p.get("x") is not None and p.get("y") is not None
                else "click"
            ),

            Intent.DOUBLE_CLICK: lambda p: (
                f"double click {p['x']} {p['y']}"
                if p.get("x") is not None and p.get("y") is not None
                else "double click"
            ),

            # ----------------------------------------------------------
            # Tab management
            #
            # All map to their plain English equivalents.
            # ----------------------------------------------------------
            Intent.NEXT_TAB:     lambda _p: "next tab",
            Intent.PREVIOUS_TAB: lambda _p: "previous tab",
            Intent.CLOSE_TAB:    lambda _p: "close tab",
            Intent.NEW_TAB:      lambda _p: "new tab",
            Intent.REFRESH_PAGE: lambda _p: "refresh page",
            Intent.GO_BACK: lambda _p: "go back",
            Intent.GO_FORWARD: lambda _p: "go forward",
            # ----------------------------------------------------------
            # Clipboard / edit shortcuts
            # ----------------------------------------------------------
            Intent.COPY:  lambda _p: "copy",
            Intent.PASTE: lambda _p: "paste",
            Intent.UNDO:  lambda _p: "undo",
            Intent.REDO:  lambda _p: "redo",
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unpack(parsed_result: Any) -> tuple[str, dict]:
        """Extract ``(intent_str, parameters)`` from a parsed_result.

        Supports both attribute-based objects (dataclass / namedtuple)
        and plain dicts, so the router stays decoupled from the exact
        type returned by ``parameter_parser.py``.
        """
        if isinstance(parsed_result, dict):
            intent_str = parsed_result["intent"]
            params = parsed_result
        else:
            intent_str = parsed_result.intent
            params = getattr(parsed_result, "parameters", None) or {}

        if not isinstance(intent_str, str) or not intent_str:
            raise ValueError(
                f"intent must be a non-empty string, got {intent_str!r}"
            )
        if not isinstance(params, dict):
            raise TypeError(
                f"parameters must be a dict, got {type(params).__name__}"
            )

        return intent_str.upper(), params