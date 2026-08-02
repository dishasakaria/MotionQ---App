"""
intent_parser.py
----------------
Production-quality fuzzy intent recognition system for an offline
accessibility assistant.

Takes raw (potentially imperfect) transcribed speech text and maps it to a
known intent using fuzzy string matching via rapidfuzz.

Designed to handle:
  * Indian English accent quirks  (e.g. "accent" → "excel")
  * ASR transcription errors      (e.g. "gugle" → "google")
  * Partial / clipped speech      (e.g. "open goo" → "open google")
  * Hinglish commands             (e.g. "excel kholo" → OPEN_EXCEL)
  * Extra filler words            (e.g. "please open google now" → OPEN_GOOGLE)

Installation
------------
    pip install rapidfuzz

Usage
-----
    from intent_parser import IntentParser

    parser = IntentParser()
    result = parser.parse("open accent")
    # → {"intent": "OPEN_EXCEL", "confidence": 0.84, "matched_phrase": "open excel"}

    result = parser.parse("hmm maybe launch chrome")
    # → {"intent": "OPEN_GOOGLE", "confidence": 0.91, "matched_phrase": "open chrome"}

    result = parser.parse("xyzzy gibberish")
    # → {"intent": None, "confidence": 0.0, "matched_phrase": ""}
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional

from rapidfuzz import fuzz, process

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("IntentParser")


# ---------------------------------------------------------------------------
# Default confidence threshold
# ---------------------------------------------------------------------------

#: Matches below this score are discarded as unrecognised input.
#: Scale: 0–100 (rapidfuzz uses 0–100, not 0–1).
#: 70 is a good starting point: lenient enough for accent variation,
#: strict enough to avoid false positives on unrelated speech.
DEFAULT_CONFIDENCE_THRESHOLD: float = 70.0


# ---------------------------------------------------------------------------
# Scorer selection
# ---------------------------------------------------------------------------

#: ``token_set_ratio`` is the best scorer here because:
#:   * It is order-insensitive  → "excel open" matches "open excel".
#:   * It ignores extra words   → "please open google now" still matches.
#:   * It handles subsets well  → partial / clipped speech scores higher.
#: Use ``fuzz.WRatio`` as a fallback when you need positional sensitivity.
_SCORER = fuzz.token_set_ratio


# ---------------------------------------------------------------------------
# ParseResult type alias (plain dict for easy JSON serialisation)
# ---------------------------------------------------------------------------

ParseResult = dict  # {"intent": str|None, "confidence": float, "matched_phrase": str}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """
    Normalise raw text before matching.

    Steps
    -----
    1. Lowercase.
    2. Strip leading/trailing whitespace.
    3. Collapse multiple spaces.
    4. Remove punctuation that ASR systems sometimes insert (commas, periods).

    Keeps Hinglish characters and numerals intact.
    """
    text = text.lower().strip()
    # Remove punctuation artefacts from ASR (., , ! ? …)
    text = re.sub(r"[^\w\s]", " ", text)
    # Collapse whitespace runs.
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _build_flat_index(
    commands: dict[str, list[str]],
) -> tuple[list[str], list[str]]:
    """
    Flatten the COMMANDS registry into two parallel lists:
      * ``phrases``  – every known trigger phrase (normalised).
      * ``intents``  – the intent name that each phrase belongs to.

    Flat lists are what ``rapidfuzz.process.extractOne`` expects.

    Parameters
    ----------
    commands:
        The COMMANDS dict from command_registry.py.

    Returns
    -------
    (phrases, intents)
        Two equal-length lists.
    """
    phrases: list[str] = []
    intents: list[str] = []

    for intent_name, phrase_list in commands.items():
        for phrase in phrase_list:
            phrases.append(_normalise(phrase))
            intents.append(intent_name)

    logger.debug(
        "Built flat index: %d phrases across %d intents.",
        len(phrases),
        len(commands),
    )
    return phrases, intents


# ---------------------------------------------------------------------------
# IntentParser
# ---------------------------------------------------------------------------

class IntentParser:
    """
    Fuzzy intent recogniser backed by rapidfuzz.

    Thread-safety
    -------------
    The parser is stateless after initialisation — ``parse()`` only reads
    pre-built internal data structures and uses no shared mutable state, so
    it is safe to call from multiple threads concurrently without locking.
    A ``threading.Lock`` guards lazy re-initialisation only.

    Parameters
    ----------
    commands:
        Optional custom command registry.  Defaults to the COMMANDS dict
        imported from ``command_registry``.
    confidence_threshold:
        Minimum rapidfuzz score (0–100) required to accept a match.
        Matches below this value return ``intent=None``.
    scorer:
        rapidfuzz scorer callable.  Defaults to ``fuzz.token_set_ratio``
        which is best for variable-length, order-insensitive speech input.
    """

    def __init__(
        self,
        commands: Optional[dict[str, list[str]]] = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        scorer=_SCORER,
    ) -> None:
        # Import here so the file can be used even if command_registry is
        # placed in a different package — just pass ``commands`` directly.
        if commands is None:
            try:
                from .command_registry import COMMANDS  # type: ignore[import]
                commands = COMMANDS
            except ImportError as exc:
                raise ImportError(
                    "command_registry.py not found.  Either place it next to "
                    "intent_parser.py or pass a commands dict explicitly."
                ) from exc

        self._confidence_threshold = confidence_threshold
        self._scorer = scorer
        self._lock = threading.Lock()  # guards lazy rebuild only

        # Build the flat lookup index once at construction time.
        self._phrases, self._intents = _build_flat_index(commands)

        logger.info(
            "IntentParser ready — %d phrases, threshold=%.1f, scorer=%s",
            len(self._phrases),
            self._confidence_threshold,
            scorer.__name__,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, text: str) -> ParseResult:
        """
        Map a raw transcribed speech string to the best matching intent.

        Parameters
        ----------
        text:
            Raw ASR output, e.g. ``"open accent"`` or ``"excel kholo please"``.

        Returns
        -------
        dict
            ``{"intent": str|None, "confidence": float, "matched_phrase": str}``

            * ``intent``        – matched intent name, or ``None`` if no match
                                  exceeded the confidence threshold.
            * ``confidence``    – normalised score in [0.0, 1.0].
            * ``matched_phrase``– the registry phrase that was matched, or ``""``
                                  if no match was found.

        Examples
        --------
        >>> parser.parse("open accent")
        {"intent": "OPEN_EXCEL", "confidence": 0.84, "matched_phrase": "open excel"}

        >>> parser.parse("gibberish xyz")
        {"intent": None, "confidence": 0.0, "matched_phrase": ""}
        """
        if not text or not text.strip():
            logger.debug("parse() received empty input.")
            return self._no_match()

        normalised = _normalise(text)
        logger.debug("Normalised input: %r → %r", text, normalised)

        # --- Primary match: full normalised input vs all known phrases -------
        result = self._fuzzy_match(normalised)

        # --- Fallback: try matching on individual words / bigrams if the full
        #     string scores poorly.  This helps when the user prepends filler
        #     words like "please", "can you", "hey computer", etc.
        if result["confidence"] < (self._confidence_threshold / 100):
            sub_result = self._subphrase_match(normalised)
            if sub_result["confidence"] > result["confidence"]:
                logger.debug(
                    "Subphrase match improved score: %.2f → %.2f",
                    result["confidence"],
                    sub_result["confidence"],
                )
                result = sub_result

        return result

    # ------------------------------------------------------------------
    # Internal matching helpers
    # ------------------------------------------------------------------

    def _fuzzy_match(self, normalised_input: str) -> ParseResult:
        """
        Run ``rapidfuzz.process.extractOne`` against the full phrase index.

        Parameters
        ----------
        normalised_input:
            Pre-normalised query string.

        Returns
        -------
        ParseResult
        """
        match = process.extractOne(
            normalised_input,
            self._phrases,
            scorer=self._scorer,
            score_cutoff=0,   # Return even low scores; we threshold ourselves.
        )

        if match is None:
            return self._no_match()

        matched_phrase, raw_score, matched_index = match
        # rapidfuzz returns scores in [0, 100]; normalise to [0.0, 1.0].
        confidence = round(raw_score / 100.0, 4)

        logger.debug(
            "Best match: %r → phrase=%r, intent=%s, score=%.4f",
            normalised_input,
            matched_phrase,
            self._intents[matched_index],
            confidence,
        )

        # Apply threshold gate.
        if raw_score < self._confidence_threshold:
            logger.debug(
                "Score %.1f below threshold %.1f — returning no-match.",
                raw_score,
                self._confidence_threshold,
            )
            return self._no_match()

        return {
            "intent": self._intents[matched_index],
            "confidence": confidence,
            "matched_phrase": matched_phrase,
        }

    def _subphrase_match(self, normalised_input: str) -> ParseResult:
        """
        Attempt matching on extracted n-gram windows (unigrams + bigrams)
        of the input.

        Useful when the user says "computer please open google now" and the
        full-string score is dragged down by filler words.  Sliding a 1–2 word
        window over the input often surfaces a strong sub-match.

        Parameters
        ----------
        normalised_input:
            Pre-normalised full query.

        Returns
        -------
        ParseResult with the best sub-window score (may still be a no-match).
        """
        tokens = normalised_input.split()
        best: ParseResult = self._no_match()

        # Generate unigrams (n=1) and bigrams (n=2) windows.
        for n in (1, 2, 3):
            for i in range(len(tokens) - n + 1):
                window = " ".join(tokens[i : i + n])
                candidate = self._fuzzy_match(window)
                if candidate["confidence"] > best["confidence"]:
                    best = candidate

        return best

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _no_match() -> ParseResult:
        """Return the canonical no-match result."""
        return {"intent": None, "confidence": 0.0, "matched_phrase": ""}

    def set_threshold(self, threshold: float) -> None:
        """
        Update the confidence threshold at runtime.

        Parameters
        ----------
        threshold:
            New threshold in [0, 100].  E.g. 75 means 75 % similarity.
        """
        if not 0.0 <= threshold <= 100.0:
            raise ValueError(f"Threshold must be in [0, 100]; got {threshold}.")
        with self._lock:
            self._confidence_threshold = threshold
        logger.info("Confidence threshold updated to %.1f", threshold)

    def reload_commands(self, commands: dict[str, list[str]]) -> None:
        """
        Hot-reload the command registry without restarting the parser.

        Thread-safe: acquires the internal lock while rebuilding the index.

        Parameters
        ----------
        commands:
            New COMMANDS dict.
        """
        with self._lock:
            self._phrases, self._intents = _build_flat_index(commands)
        logger.info("Command registry reloaded — %d phrases.", len(self._phrases))

    def __repr__(self) -> str:
        return (
            f"IntentParser("
            f"phrases={len(self._phrases)}, "
            f"threshold={self._confidence_threshold}, "
            f"scorer={self._scorer.__name__!r})"
        )


# ---------------------------------------------------------------------------
# Example / smoke-test  (run file directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Inline registry so this file can be run standalone for quick testing.
    COMMANDS = {
        "OPEN_GOOGLE": [
            "open google", "google kholo", "launch google", "open chrome",
            "start browser", "browser kholo", "internet kholo",
            "open gugle", "go to google",
        ],
        "OPEN_EXCEL": [
            "open excel", "launch excel", "excel kholo",
            "start excel", "open accent", "open xl",
        ],
        "OPEN_PAINT": [
            "open paint", "launch paint", "paint kholo",
            "open painter", "start paint",
        ],
        "NEXT_SLIDE": [
            "next slide", "new slide", "slide next",
            "go next", "forward slide",
        ],
        "TYPE_TEXT": [
            "type", "write", "enter text",
        ],
    }

    parser = IntentParser(commands=COMMANDS, confidence_threshold=70.0)
    print(f"\n{parser}\n")

    test_cases = [
        # (input,                        expected_intent)
        ("open accent",                  "OPEN_EXCEL"),      # classic Indian ASR quirk
        ("open excel",                   "OPEN_EXCEL"),      # exact
        ("launch google please",         "OPEN_GOOGLE"),     # filler word
        ("google kholo",                 "OPEN_GOOGLE"),     # Hinglish
        ("open gugle",                   "OPEN_GOOGLE"),     # phonetic misspelling
        ("browser kholo yaar",           "OPEN_GOOGLE"),     # Hinglish + filler
        ("next slid",                    "NEXT_SLIDE"),      # clipped word
        ("go to next slide",             "NEXT_SLIDE"),      # paraphrase
        ("open pant",                    "OPEN_PAINT"),      # ASR drop of 'i'
        ("start xl",                     "OPEN_EXCEL"),      # abbreviation
        ("type this for me",             "TYPE_TEXT"),       # partial
        ("computer please open internet","OPEN_GOOGLE"),     # filler prefix
        ("xyzzy gibberish nonsense",     None),              # should fail
        ("",                             None),              # empty input
    ]

    header = f"{'Input':<35} {'Expected':<15} {'Got':<15} {'Conf':>6}  {'Match':<25} {'OK':>3}"
    print(header)
    print("-" * len(header))

    passed = 0
    for text, expected in test_cases:
        result = parser.parse(text)
        got     = result["intent"]
        conf    = result["confidence"]
        phrase  = result["matched_phrase"]
        ok      = "✓" if got == expected else "✗"
        passed += int(got == expected)
        print(
            f"{text!r:<35} {str(expected):<15} {str(got):<15} "
            f"{conf:>6.2f}  {phrase:<25} {ok:>3}"
        )

    print(f"\n{passed}/{len(test_cases)} tests passed.")