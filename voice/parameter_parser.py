"""
parameter_parser.py
--------------------
Parameter extraction system for the offline Windows accessibility assistant.

Converts raw transcribed speech into structured intent + parameter dicts
using regex-based rule matching.  Works entirely offline with no ML models.

Supported intents
-----------------
* OPEN_APPLICATION  — "open excel", "excel kholo", "start notepad"
* OPEN_WEBSITE      — "open youtube.com", "go to amazon", "website kholo"
* SEARCH_GOOGLE     — "search for monsoon recipes", "google karo AI tools"
* TYPE_TEXT         — "type my name is Disha", "write hello world"

Design goals
------------
* Rule-based and regex-driven  — deterministic, fast, fully offline.
* Filler-word stripping        — "please can you open excel now" → "excel".
* Hinglish support             — "kholo", "karo", "likho" trigger phrases.
* Imperfect ASR tolerance      — leading/trailing noise, repeated words.
* Thread-safe                  — parse() is stateless; safe for concurrent use.
* Modular                      — each intent has its own RuleSet; easy to extend.

Installation
------------
    No extra packages — uses stdlib ``re`` only.

Usage
-----
    from parameter_parser import ParameterParser

    parser = ParameterParser()

    parser.parse("open excel")
    # → {"intent": "OPEN_APPLICATION", "target": "excel"}

    parser.parse("search google for accessibility software")
    # → {"intent": "SEARCH_GOOGLE", "query": "accessibility software"}

    parser.parse("type my name is disha")
    # → {"intent": "TYPE_TEXT", "text": "my name is disha"}

    parser.parse("open youtube.com")
    # → {"intent": "OPEN_WEBSITE", "url": "youtube.com"}

    parser.parse("gibberish xyz")
    # → {"intent": "UNKNOWN", "raw": "gibberish xyz"}
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("ParameterParser")


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ParseResult = dict   # {"intent": str, ...}


# ---------------------------------------------------------------------------
# Filler word stripping
# ---------------------------------------------------------------------------
HINDI_COMMAND_MAP = {
    "ओपन एक्सल": "open excel",
    "ओपन एक्सेल": "open excel",
    "ओपन यूट्यूब": "open youtube",
    "ओपन गूगल": "open google",
    "नेक्स्ट स्लाइड": "next slide",
    "अगला स्लाइड": "next slide",
    "स्क्रॉल डाउन": "scroll down",
    "स्क्रॉल अप": "scroll up",
}

APP_ALIASES = {

    # Excel
    "excel": "excel",
    "excell": "excel",
    "excellent": "excel",
    "accent": "excel",
    "xl": "excel",

    # Chrome
    "chrome": "chrome",
    "crumb": "chrome",
    "crow": "chrome",

    # VS Code
    "vscode": "vscode",
    "vs code": "vscode",
    "visual studio": "vscode",

    # PowerPoint
    "powerpoint": "powerpoint",
    "ppt": "powerpoint",
    "slides": "powerpoint",

    # Word
    "word": "word",
    "ms word": "word",

    # Paint
    "paint": "paint",
    "painter": "paint",

    # YouTube
    "youtube": "youtube",
    "you tube": "youtube",

    # Google
    "google": "google",

    # Notepad
    "notepad": "notepad",
}

INTENT_ALIASES = {

    # Refresh
    "fresh page": "refresh page",
    "refresh the screen": "refresh page",
    "reload page": "refresh page",
    "fresh pitch": "refresh page",
"be fresh pidge": "refresh page",
"refresh the screen": "refresh page",

"last page": "previous tab",
"previous page": "previous tab",

"next page": "next tab",

"school down": "scroll down",
"slow down": "scroll down",
    # Tabs
    "new text": "new tab",
    "next step": "next tab",

    # Scroll
    "school down": "scroll down",
    "slow down": "scroll down",

    # Browser
    "go front": "go forward",

    # Common hallucinations
    "fresh pitch": "refresh page",

}
# Words/phrases that ASR commonly prepends or appends but carry no meaning.
# Stripped from input before pattern matching.
_FILLER_PATTERN = re.compile(
    r"""
    ^\s*
    (?:
        please\s+(?:can\s+you\s+)?  |  # "please", "please can you"
        can\s+you\s+                |  # "can you"
        could\s+you\s+              |  # "could you"
        hey\s+(?:computer\s+)?      |  # "hey", "hey computer"
        computer\s+                 |  # "computer"
        assistant\s+                |  # "assistant"
        ok(?:ay)?\s+                |  # "ok", "okay"
        um+\s+                      |  # "um", "umm"
        uh+\s+                      |  # "uh", "uhh"
        so\s+                       |  # "so"
        now\s+                      |  # "now" (leading)
        just\s+                     |  # "just"
        quickly\s+                  |  # "quickly"
        yaar\s+                     |  # Hinglish filler
        bhai\s+                     |  # Hinglish filler
        boss\s+                        # Hinglish filler
    )*
    """,
    re.VERBOSE | re.IGNORECASE,
)

_TRAILING_FILLER_PATTERN = re.compile(
    r"""
    \s*
    (?:
        \s+please    |   # trailing "please"
        \s+now       |   # trailing "now"
        \s+yaar      |   # trailing "yaar"
        \s+bhai      |   # trailing "bhai"
        \s+na        |   # trailing "na" (Hinglish)
        \s+re            # trailing "re" (Hinglish)
    )*
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _strip_fillers(text: str) -> str:
    """
    Remove common filler words from both ends of *text*.

    Examples
    --------
    >>> _strip_fillers("please can you open excel now")
    'open excel now'
    >>> _strip_fillers("hey computer search for music yaar")
    'search for music'
    """
    text = _FILLER_PATTERN.sub("", text)
    text = _TRAILING_FILLER_PATTERN.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Known website keywords
# ---------------------------------------------------------------------------

# Maps common spoken aliases to their canonical domain.
# Used by OPEN_WEBSITE to normalise "open youtube" → url="youtube.com".
_WEBSITE_ALIASES: dict[str, str] = {
    "youtube":    "youtube.com",
    "google":     "google.com",
    "amazon":     "amazon.in",
    "flipkart":   "flipkart.com",
    "gmail":      "gmail.com",
    "github":     "github.com",
    "wikipedia":  "wikipedia.org",
    "twitter":    "twitter.com",
    "instagram":  "instagram.com",
    "facebook":   "facebook.com",
    "linkedin":   "linkedin.com",
    "whatsapp":   "web.whatsapp.com",
    "netflix":    "netflix.com",
    "spotify":    "spotify.com",
    "maps":       "maps.google.com",
    "translate":  "translate.google.com",
    "drive":      "drive.google.com",
    "chatgpt":    "chat.openai.com",
    "openai":     "openai.com",
}

# Regex that matches a bare domain typed by the ASR (e.g. "youtube.com").
_DOMAIN_PATTERN = re.compile(
    r"\b[\w-]+\.(com|in|org|net|io|co|edu|gov|ai)\b",
    re.IGNORECASE,
)


def _resolve_website(target: str) -> str:
    """
    Normalise a spoken website target to a domain string.

    Checks explicit alias table first, then bare-domain regex.

    Examples
    --------
    >>> _resolve_website("youtube")
    'youtube.com'
    >>> _resolve_website("github.com")
    'github.com'
    >>> _resolve_website("some unknown site")
    'some unknown site'
    """
    lower = target.lower().strip()
    if lower in _WEBSITE_ALIASES:
        return _WEBSITE_ALIASES[lower]
    # Check if the whole target is already a domain string.
    match = _DOMAIN_PATTERN.search(lower)
    if match:
        return match.group(0)
    # Return as-is; CommandRouter / browser_actions will handle it.
    return target.strip()


# ---------------------------------------------------------------------------
# RuleSet dataclass
# ---------------------------------------------------------------------------

@dataclass
class RuleSet:
    """
    A collection of regex patterns for one intent.

    Attributes
    ----------
    intent:
        Intent name this ruleset belongs to.
    patterns:
        List of compiled regex patterns tried in order.
        The *first* match wins.
    extractor:
        Callable ``(match) → ParseResult`` that turns the regex match object
        into the final structured dict.
    """
    intent: str
    patterns: list[re.Pattern]
    extractor: Callable[[re.Match], ParseResult]


# ---------------------------------------------------------------------------
# Intent rule definitions
# ---------------------------------------------------------------------------

def _make_keyboard_command_rules() -> list[RuleSet]:
    """
    Keyboard shortcut commands.
    """

    simple_rules = [

        ("PRESS_ENTER", [
            r"press\s+enter",
            r"hit\s+enter",
            r"enter",
        ]),

        ("PRESS_BACKSPACE", [
            r"press\s+backspace",
            r"backspace",
            r"delete\s+character",
        ]),

        ("SELECT_ALL", [
            r"select\s+all",
        ]),

        ("COPY", [
            r"copy",
        ]),

        ("PASTE", [
            r"paste",
        ]),

        ("UNDO", [
            r"undo",
        ]),

        ("REDO", [
            r"redo",
        ]),
    ]

    rules = []

    for intent_name, pattern_strings in simple_rules:

        patterns = [
            re.compile(p, re.IGNORECASE)
            for p in pattern_strings
        ]

        def make_extractor(intent):
            def extractor(_: re.Match) -> ParseResult:
                return {"intent": intent}
            return extractor

        rules.append(
            RuleSet(
                intent=intent_name,
                patterns=patterns,
                extractor=make_extractor(intent_name),
            )
        )

    return rules

def _make_window_control_rules() -> list[RuleSet]:

    simple_rules = [

        ("CLOSE_WINDOW", [
            r"close\s+window",
            r"close\s+current\s+window",
            r"close\s+app",
        ]),

        ("MINIMIZE_WINDOW", [
            r"minimize\s+window",
            r"minimize",
        ]),

        ("MAXIMIZE_WINDOW", [
            r"maximize\s+window",
            r"maximize",
        ]),

        ("SWITCH_APP", [
            r"switch\s+app",
            r"alt\s+tab",
            r"next\s+app",
        ]),
    ]

    rules = []

    for intent_name, pattern_strings in simple_rules:

        patterns = [
            re.compile(p, re.IGNORECASE)
            for p in pattern_strings
        ]

        def make_extractor(intent):
            def extractor(_: re.Match) -> ParseResult:
                return {"intent": intent}
            return extractor

        rules.append(
            RuleSet(
                intent=intent_name,
                patterns=patterns,
                extractor=make_extractor(intent_name),
            )
        )

    return rules

def _make_system_control_rules() -> list[RuleSet]:

    simple_rules = [

        ("OPEN_DOWNLOADS", [
            r"open\s+downloads",
        ]),

        ("OPEN_DESKTOP", [
            r"open\s+desktop",
        ]),

        ("OPEN_DOCUMENTS", [
            r"open\s+documents",
        ]),

        ("OPEN_FILE_EXPLORER", [
            r"open\s+file\s+explorer",
            r"open\s+explorer",
        ]),

        ("LOCK_SCREEN", [
            r"lock\s+screen",
            r"lock\s+computer",
        ]),

        ("VOLUME_UP", [
            r"volume\s+up",
        ]),

        ("VOLUME_DOWN", [
            r"volume\s+down",
        ]),

        ("MUTE_VOLUME", [
            r"mute",
            r"mute\s+volume",
        ]),

        ("TAKE_SCREENSHOT", [
            r"take\s+screenshot",
            r"screenshot",
        ]),
    ]

    rules = []

    for intent_name, pattern_strings in simple_rules:

        patterns = [
            re.compile(p, re.IGNORECASE)
            for p in pattern_strings
        ]

        def make_extractor(intent):
            def extractor(_: re.Match) -> ParseResult:
                return {"intent": intent}
            return extractor

        rules.append(
            RuleSet(
                intent=intent_name,
                patterns=patterns,
                extractor=make_extractor(intent_name),
            )
        )

    return rules

def _make_presentation_rules() -> list[RuleSet]:

    simple_rules = [

        ("START_PRESENTATION", [
            r"start\s+presentation",
            r"start\s+slideshow",
        ]),

        ("NEXT_SLIDE", [
            r"next\s+slide",
        ]),

        ("PREVIOUS_SLIDE", [
            r"previous\s+slide",
            r"last\s+slide",
        ]),

        ("BLACK_SCREEN", [
            r"black\s+screen",
        ]),

        ("EXIT_PRESENTATION", [
            r"exit\s+presentation",
            r"close\s+presentation",
        ]),
    ]

    rules = []

    for intent_name, pattern_strings in simple_rules:

        patterns = [
            re.compile(p, re.IGNORECASE)
            for p in pattern_strings
        ]

        def make_extractor(intent):
            def extractor(_: re.Match) -> ParseResult:
                return {"intent": intent}
            return extractor

        rules.append(
            RuleSet(
                intent=intent_name,
                patterns=patterns,
                extractor=make_extractor(intent_name),
            )
        )

    return rules


def _make_open_application_rules() -> RuleSet:
    """
    OPEN_APPLICATION — open / launch a named desktop application.

    Trigger words (English + Hinglish)
    -----------------------------------
    open, launch, start, run, execute, kholo, chalu karo, start karo

    Examples
    --------
    "open excel"             → target="excel"
    "launch microsoft paint" → target="microsoft paint"
    "excel kholo"            → target="excel"
    "notepad chalu karo"     → target="notepad"
    """
    patterns = [
        # English: "open <app>", "launch <app>", "start <app>" …
        re.compile(
            r"(?:open|launch|start|run|execute|load)\s+(?:the\s+|app\s+|application\s+)?(.+)",
            re.IGNORECASE,
        ),
        # Hinglish: "<app> kholo", "<app> chalu karo", "<app> start karo"
        re.compile(
            r"(.+?)\s+(?:kholo|kholna|chalu\s+karo|start\s+karo|chalao)",
            re.IGNORECASE,
        ),
    ]

    def extractor(m: re.Match) -> ParseResult:
        target = m.group(1).strip(" .,!?")
        target = APP_ALIASES.get(
            target.lower(),
            target.lower()
            )
        # Remove trailing noise like "for me", "please", "app"
        target = re.sub(r"\s+(for\s+me|please|app|application)$", "", target).strip()
        return {"intent": "OPEN_APPLICATION", "target": target}

    return RuleSet(intent="OPEN_APPLICATION", patterns=patterns, extractor=extractor)


def _make_open_website_rules() -> RuleSet:
    """
    OPEN_WEBSITE — open a website by name or domain.

    Trigger words (English + Hinglish)
    -----------------------------------
    open, go to, visit, navigate to, show, website kholo, site kholo

    Examples
    --------
    "open youtube"             → url="youtube.com"
    "go to amazon.in"          → url="amazon.in"
    "visit github"             → url="github.com"
    "youtube website kholo"    → url="youtube.com"
    """
    patterns = [
        # English: "open youtube", "go to github.com", "visit netflix"
        re.compile(
            r"(?:open|go\s+to|visit|navigate\s+to|show|browse)\s+(?:the\s+)?(?:website\s+)?(.+)",
            re.IGNORECASE,
        ),
        # Hinglish: "youtube kholo", "website kholo youtube"
        re.compile(
            r"(.+?)\s+(?:website\s+)?(?:kholo|kholna|dikhao)",
            re.IGNORECASE,
        ),
    ]

    def extractor(m: re.Match) -> ParseResult:
        raw = m.group(1).strip().lower()
        # Remove qualifiers that aren't part of the website name.
        raw = re.sub(r"\s+(website|site|page|browser)$", "", raw).strip()
        url = _resolve_website(raw)
        return {"intent": "OPEN_WEBSITE", "url": url}

    return RuleSet(intent="OPEN_WEBSITE", patterns=patterns, extractor=extractor)


def _make_search_google_rules() -> RuleSet:
    """
    SEARCH_GOOGLE — perform a Google search with a query string.

    Trigger words (English + Hinglish)
    -----------------------------------
    search, search for, google, look up, find, google karo, dhundo

    Examples
    --------
    "search for accessibility software"  → query="accessibility software"
    "google monsoon recipes"             → query="monsoon recipes"
    "look up python tutorials"           → query="python tutorials"
    "AI tools google karo"               → query="AI tools"
    "dhundo weather today"               → query="weather today"
    """
    patterns = [
        # English: "search for <query>", "search google for <query>"
        re.compile(
            r"search\s+(?:google\s+)?(?:for\s+|about\s+)?(.+)",
            re.IGNORECASE,
        ),
        # English: "google <query>", "look up <query>", "find <query>"
        re.compile(
            r"(?:google|look\s+up|find|lookup)\s+(.+)",
            re.IGNORECASE,
        ),
        # Hinglish: "<query> google karo", "<query> search karo"
        re.compile(
            r"(.+?)\s+(?:google\s+karo|search\s+karo|dhundo|khojo)",
            re.IGNORECASE,
        ),
        # Hinglish: "dhundo <query>", "khojo <query>"
        re.compile(
            r"(?:dhundo|khojo)\s+(.+)",
            re.IGNORECASE,
        ),
    ]

    def extractor(m: re.Match) -> ParseResult:
        query = m.group(1).strip(" .,!?")
        # Strip residual filler from the captured query.
        query = re.sub(r"\s+(please|now|yaar|bhai)$", "", query, flags=re.IGNORECASE).strip()
        return {"intent": "SEARCH_GOOGLE", "query": query}

    return RuleSet(intent="SEARCH_GOOGLE", patterns=patterns, extractor=extractor)

def _make_search_youtube_rules() -> RuleSet:
        patterns = [
            re.compile(
                r"search\s+youtube\s+for\s+(.+)",
                re.IGNORECASE,
            ),

            re.compile(
                r"youtube\s+search\s+(.+)",
                re.IGNORECASE,
            ),
            re.compile(
                r"youtube\s+(.+)",
                re.IGNORECASE,
            ),
        ]
        def extractor(m: re.Match) -> ParseResult:
            query = m.group(1).strip(" .,!?")
            return {
                "intent": "SEARCH_YOUTUBE",
                "query": query,
            }

        return RuleSet(
            intent="SEARCH_YOUTUBE",
            patterns=patterns,
            extractor=extractor,
        )


def _make_type_text_rules() -> RuleSet:
    """
    TYPE_TEXT — type a string into the active window.
    """

    patterns = [

        re.compile(
            r"(?:type|write|enter|input|dictate)\s+(.+)",
            re.IGNORECASE,
        ),

        re.compile(
            r"(?:likho|type\s+karo|likh\s+do)\s+(.+)",
            re.IGNORECASE,
        ),
    ]

    def extractor(m: re.Match) -> ParseResult:

        text = m.group(1).strip(" .,!?")

        return {
            "intent": "TYPE_TEXT",
            "text": text,
        }

    return RuleSet(
        intent="TYPE_TEXT",
        patterns=patterns,
        extractor=extractor,
    )


def _make_browser_navigation_rules() -> list[RuleSet]:
    """
    Browser navigation and tab controls.
    """

    simple_rules = [

        ("NEW_TAB", [
            r"new\s+tab",
            r"open\s+new\s+tab",
        ]),

        ("CLOSE_TAB", [
            r"close\s+tab",
            r"remove\s+tab",
        ]),

        ("NEXT_TAB", [
            r"next\s+tab",
            r"switch\s+tab",
        ]),

        ("PREVIOUS_TAB", [
            r"previous\s+tab",
            r"last\s+tab",
            r"back\s+tab",
        ]),

        ("SCROLL_DOWN", [
            r"scroll\s+down",
            r"down\s+scroll",
        ]),

        ("SCROLL_UP", [
            r"scroll\s+up",
            r"up\s+scroll",
        ]),

        ("REFRESH_PAGE", [
            r"refresh\s+page",
            r"reload\s+page",
            r"refresh",
        ]),

        ("GO_BACK", [
            r"go\s+back",
            r"browser\s+back",
        ]),

        ("GO_FORWARD", [
            r"go\s+forward",
            r"browser\s+forward",
        ]),
    ]

    rules = []

    for intent_name, pattern_strings in simple_rules:

        patterns = [
            re.compile(p, re.IGNORECASE)
            for p in pattern_strings
        ]

        def make_extractor(intent):
            def extractor(_: re.Match) -> ParseResult:
                return {"intent": intent}
            return extractor

        rules.append(
            RuleSet(
                intent=intent_name,
                patterns=patterns,
                extractor=make_extractor(intent_name),
            )
        )

    return rules

# ---------------------------------------------------------------------------
# Website-vs-application disambiguation
# ---------------------------------------------------------------------------
#
# WHY THIS AMBIGUITY EXISTS
# -------------------------
# Both OPEN_APPLICATION and OPEN_WEBSITE share the trigger verb "open".
# The OPEN_WEBSITE rule matches "open <anything>" and its extractor calls
# _resolve_website(), which accepts "excel" or "vscode" as input and returns
# them unchanged — making the result look like a website intent even though
# they are clearly desktop apps.
#
# The disambiguation layer (_disambiguate + _is_likely_website) runs AFTER
# pattern matching and decides whether to keep or re-classify the result.
# This keeps rule extractors clean (no cross-intent awareness inside them)
# and makes the entire policy easy to tune from one place.
#
# WHY KNOWN_APPS IS CHECKED FIRST
# --------------------------------
# Without an explicit app blocklist, every name that isn't in _WEBSITE_ALIASES
# and doesn't match _DOMAIN_PATTERN falls through and is treated as a website
# (because OPEN_WEBSITE sits higher in the rule list).  KNOWN_APPS is an
# authoritative "definitely a desktop app" guard that short-circuits all
# website-detection heuristics for well-known executables.  The set lookup
# is O(1) and must execute before any regex or alias-table check.

# Canonical set of well-known desktop application names (lowercase).
# If the user's extracted target matches any of these exactly, it is NEVER
# reclassified as a website — regardless of what the alias table or domain
# pattern might suggest.
KNOWN_APPS: frozenset[str] = frozenset({
    "excel",
    "word",
    "powerpoint",
    "paint",
    "vscode",
    "vs code",
    "chrome",
    "google chrome",
    "notepad",
    "calculator",
    "calc",
    "spotify",
    "whatsapp",
    "edge",
    "microsoft edge",
    "explorer",
    "file explorer",
    "cmd",
    "command prompt",
    "terminal",
    "discord",
    "telegram",
    "zoom",
    # extras that share names with websites but are clearly apps when spoken
    "outlook",
    "teams",
    "slack",
    "vlc",
    "firefox",
    "opera",
})

# Matches targets that contain a bare domain extension or a known web alias.
# e.g. "youtube.com", "github.io", "netflix"
_WEB_HINT_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in _WEBSITE_ALIASES) + r"|"
    r"[\w-]+\.(?:com|in|org|net|io|co|edu|gov|ai))\b",
    re.IGNORECASE,
)


def _is_likely_website(target: str) -> bool:
    """
    Return ``True`` if *target* looks like a website rather than a desktop app.

    Decision order (stops at the first definitive answer)
    -----------------------------------------------------
    1. **KNOWN_APPS guard (O(1))** — if the target is a known desktop app,
       return ``False`` immediately.  This is the primary fix for the
       "open excel → OPEN_WEBSITE" and "open vscode → OPEN_WEBSITE" bugs.
       Known apps take absolute precedence over all website heuristics.

    2. **Domain + alias pattern** — targets containing ``.com`` / ``.in``
       etc., or matching a known web service ("youtube", "netflix"), are
       classified as websites.

    Parameters
    ----------
    target:
        The extracted target string from an OPEN_APPLICATION result,
        already lowercased.

    Returns
    -------
    bool
    """
    lower = target.lower().strip()

    # ── Guard 1: known desktop app → never a website ──────────────────────
    if lower in KNOWN_APPS:
        logger.debug("_is_likely_website(%r): in KNOWN_APPS → False", target)
        return False

    # ── Guard 2: domain pattern or website alias → likely a website ────────
    result = bool(_WEB_HINT_PATTERN.search(lower))
    logger.debug("_is_likely_website(%r): pattern check → %s", target, result)
    return result


# ---------------------------------------------------------------------------
# ParameterParser
# ---------------------------------------------------------------------------

class ParameterParser:
    """
    Rule-based parameter extractor for voice assistant intents.

    Converts raw transcribed text into structured ``ParseResult`` dicts.

    Thread-safety
    -------------
    ``parse()`` is stateless after initialisation and safe to call from
    multiple threads concurrently without locking.

    Parameters
    ----------
    custom_rules:
        Optional list of additional ``RuleSet`` objects to prepend to the
        default rule list.  Allows callers to inject domain-specific patterns
        without subclassing.
    """

    def __init__(self, custom_rules: Optional[list[RuleSet]] = None) -> None:
        self._lock = threading.Lock()   # guards rule list for hot-reload only

        # Build the ordered rule list.
        # Order matters: more specific rules (SEARCH_GOOGLE) must come before
        # broader ones (OPEN_APPLICATION) to avoid premature matches.
        default_rules: list[RuleSet] = [
            _make_search_youtube_rules(),
             _make_search_google_rules(),
             _make_type_text_rules(),
             _make_open_application_rules(),
             *_make_browser_navigation_rules(),
             *_make_keyboard_command_rules(),
             *_make_window_control_rules(),
             *_make_system_control_rules(),
             *_make_presentation_rules(),
             _make_open_website_rules(), # "open excel"  (catch-all last)
        ]

        self._rules: list[RuleSet] = (custom_rules or []) + default_rules

        logger.info(
            "ParameterParser ready — %d rule sets loaded.",
            len(self._rules),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, text: str) -> ParseResult:
        """
        Extract intent and parameters from raw transcribed speech.

        Processing pipeline
        -------------------
        1. Lowercase + strip whitespace.
        2. Remove leading/trailing filler words.
        3. Try each RuleSet's patterns in registration order.
        4. On first match: call the RuleSet's extractor and return the result.
        5. Post-process: re-classify OPEN_APPLICATION as OPEN_WEBSITE when
           the target looks like a web address.
        6. If nothing matched: return ``{"intent": "UNKNOWN", "raw": text}``.

        Parameters
        ----------
        text:
            Raw ASR output string.

        Returns
        -------
        dict
            One of:

            ``{"intent": "OPEN_APPLICATION", "target": str}``
            ``{"intent": "OPEN_WEBSITE",     "url":    str}``
            ``{"intent": "SEARCH_GOOGLE",    "query":  str}``
            ``{"intent": "TYPE_TEXT",        "text":   str}``
            ``{"intent": "UNKNOWN",          "raw":    str}``

        Examples
        --------
        >>> parser.parse("open excel")
        {"intent": "OPEN_APPLICATION", "target": "excel"}

        >>> parser.parse("search google for accessibility software")
        {"intent": "SEARCH_GOOGLE", "query": "accessibility software"}

        >>> parser.parse("type my name is disha")
        {"intent": "TYPE_TEXT", "text": "my name is disha"}

        >>> parser.parse("open youtube")
        {"intent": "OPEN_WEBSITE", "url": "youtube.com"}
        """
        if not text or not text.strip():
            logger.debug("parse() received empty input.")
            return {"intent": "UNKNOWN", "raw": ""}

        # Step 1 — normalise
        normalised = text.lower().strip(" .,!?")
        normalised = re.sub(r"\s+", " ", normalised)   # collapse whitespace
        HINDI_COMMAND_MAP = {
            "ओपन एक्सल": "open excel",
    "ओपन एक्सेल": "open excel",
    "ओपन यूट्यूब": "open youtube",
    "ओपन गूगल": "open google",
    "स्क्रॉल डाउन": "scroll down",
    "स्क्रॉल अप": "scroll up",
    "नेक्स्ट स्लाइड": "next slide",
        }
        

        normalised = HINDI_COMMAND_MAP.get(text, text)
        normalised = INTENT_ALIASES.get(normalised, normalised)
        # Step 2 — strip fillers
        cleaned = _strip_fillers(normalised)
        logger.debug("Input: %r  →  cleaned: %r", text, cleaned)

        # Step 3 & 4 — try each ruleset
        with self._lock:
            rules_snapshot = list(self._rules)

        for ruleset in rules_snapshot:
            for pattern in ruleset.patterns:
                match = pattern.match(cleaned)
                if match:
                    result = ruleset.extractor(match)
                    logger.debug("Matched rule %r: %s", ruleset.intent, result)

                    # Step 5 — disambiguate OPEN_APPLICATION vs OPEN_WEBSITE
                    result = self._disambiguate(result)

                    return result

        # Step 6 — nothing matched
        logger.debug("No rule matched for: %r", cleaned)
        return {"intent": "UNKNOWN", "raw": text.strip()}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _disambiguate(result: ParseResult) -> ParseResult:
        """
        Re-classify OPEN_APPLICATION as OPEN_WEBSITE when the extracted
        target looks like a website name or domain.

        Prevents "open youtube" from being returned as
        ``{"intent": "OPEN_APPLICATION", "target": "youtube"}``
        instead of the correct ``{"intent": "OPEN_WEBSITE", "url": "youtube.com"}``.

        Parameters
        ----------
        result:
            ParseResult as returned by an extractor.

        Returns
        -------
        ParseResult
            Possibly re-classified result.
        """
        if result.get("intent") == "OPEN_APPLICATION":
            target = result.get("target", "")
            if _is_likely_website(target):
                url = _resolve_website(target)
                logger.debug(
                    "Disambiguated OPEN_APPLICATION(%r) → OPEN_WEBSITE(%r)",
                    target, url,
                )
                return {"intent": "OPEN_WEBSITE", "url": url}
        return result

    # ------------------------------------------------------------------
    # Runtime extension
    # ------------------------------------------------------------------

    def add_rule(self, ruleset: RuleSet, prepend: bool = True) -> None:
        """
        Add a custom RuleSet at runtime (thread-safe).

        Parameters
        ----------
        ruleset:
            The new RuleSet to add.
        prepend:
            If True (default), insert at the beginning so custom rules take
            priority over built-in ones.
        """
        with self._lock:
            if prepend:
                self._rules.insert(0, ruleset)
            else:
                self._rules.append(ruleset)
        logger.info(
            "Added custom rule %r (%s).",
            ruleset.intent,
            "prepended" if prepend else "appended",
        )

    def __repr__(self) -> str:
        intents = [r.intent for r in self._rules]
        return f"ParameterParser(rules={intents})"


# ---------------------------------------------------------------------------
# Example / smoke-test  (run file directly to test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = ParameterParser()
    print(f"\n{parser}\n")

    test_cases: list[tuple[str, str, str]] = [
        # (input_text,                                  expected_intent,       key_to_check)
        ("open excel",                                  "OPEN_APPLICATION",    "target"),
        ("launch microsoft paint",                      "OPEN_APPLICATION",    "target"),
        ("start notepad please",                        "OPEN_APPLICATION",    "target"),
        ("excel kholo",                                 "OPEN_APPLICATION",    "target"),
        ("please can you open vlc now",                 "OPEN_APPLICATION",    "target"),

        ("open youtube",                                "OPEN_WEBSITE",        "url"),
        ("go to amazon.in",                             "OPEN_WEBSITE",        "url"),
        ("visit github.com",                            "OPEN_WEBSITE",        "url"),
        ("youtube website kholo",                       "OPEN_WEBSITE",        "url"),
        ("open netflix",                                "OPEN_WEBSITE",        "url"),

        ("search for accessibility software",           "SEARCH_GOOGLE",       "query"),
        ("search google for monsoon recipes",           "SEARCH_GOOGLE",       "query"),
        ("google python tutorials",                     "SEARCH_GOOGLE",       "query"),
        ("look up best keyboard shortcuts",             "SEARCH_GOOGLE",       "query"),
        ("AI tools google karo",                        "SEARCH_GOOGLE",       "query"),
        ("dhundo weather today",                        "SEARCH_GOOGLE",       "query"),

        ("type my name is disha",                       "TYPE_TEXT",           "text"),
        ("write hello world",                           "TYPE_TEXT",           "text"),
        ("enter this is a test",                        "TYPE_TEXT",           "text"),
        ("likho mera naam Disha hai",                   "TYPE_TEXT",           "text"),
        ("hey computer please type good morning yaar",  "TYPE_TEXT",           "text"),

        ("gibberish xyz 123",                           "UNKNOWN",             "raw"),
        ("",                                            "UNKNOWN",             "raw"),

        # ── Disambiguation regression tests (KNOWN_APPS guard) ──────────
        # The bug: without KNOWN_APPS these returned OPEN_WEBSITE instead.
        ("open excel",                                  "OPEN_APPLICATION",    "target"),
        ("open vscode",                                 "OPEN_APPLICATION",    "target"),
        # These must still resolve to OPEN_WEBSITE after the fix.
        ("open youtube",                                "OPEN_WEBSITE",        "url"),
        ("open github.com",                             "OPEN_WEBSITE",        "url"),
    ]

    header = f"{'Input':<45} {'Expected':<20} {'Got':<20} {'Value':<30} OK"
    print(header)
    print("-" * len(header))

    passed = 0
    for text, expected_intent, key in test_cases:
        result = parser.parse(text)
        got    = result.get("intent", "?")
        value  = result.get(key, "")
        ok     = "✓" if got == expected_intent else "✗"
        passed += int(got == expected_intent)
        print(f"{text!r:<45} {expected_intent:<20} {got:<20} {str(value):<30} {ok}")

    print(f"\n{passed}/{len(test_cases)} tests passed.")