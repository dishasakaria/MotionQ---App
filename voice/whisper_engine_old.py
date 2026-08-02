"""
whisper_engine.py
-----------------
Production-quality, real-time, offline speech recognition module for Windows.

Uses faster-whisper with the tiny.en model for low-latency microphone transcription.
Designed and tuned for Indian English accents.

Installation
------------
    pip install faster-whisper sounddevice numpy

Optional (recommended for better Windows audio performance):
    pip install sounddevice[portaudio]

Usage
-----
    engine = WhisperEngine()
    engine.start()

    try:
        while True:
            text = engine.get_text()
            if text:
                print(f"Transcribed: {text}")
            time.sleep(0.05)
    finally:
        engine.stop()

Requirements
------------
    Python   : 3.11+
    Platform : Windows (also works on Linux/macOS)
    Model    : faster-whisper tiny.en (auto-downloaded on first run, ~75 MB)
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("WhisperEngine")


# ---------------------------------------------------------------------------
# Constants / tuning knobs
# ---------------------------------------------------------------------------

#: Whisper expects 16 kHz mono audio.
SAMPLE_RATE: int = 16_000

#: Number of audio channels (mono).
CHANNELS: int = 1

#: Duration (seconds) of each audio chunk fed to Whisper.
#: Shorter → lower latency but more CPU; longer → higher accuracy.
#: 2 s is a good trade-off for conversational speech with Indian accents.
CHUNK_SECONDS: float = 2.0

#: Number of samples per chunk.
CHUNK_SAMPLES: int = int(SAMPLE_RATE * CHUNK_SECONDS)

#: Minimum RMS energy below which a chunk is considered silence and skipped.
#: This prevents Whisper from hallucinating text on background noise.
#: Range 0–1 (float32 audio).  Tune lower (0.002) for quiet environments.
SILENCE_THRESHOLD: float = 0.005

#: How many consecutive silent chunks to accumulate before flushing a
#: partial buffer (avoids holding stale audio in the rolling window).
MAX_SILENT_CHUNKS: int = 3

#: Rolling context: number of *previous* chunks prepended to each new chunk
#: so that words split across chunk boundaries are transcribed correctly.
CONTEXT_CHUNKS: int = 1

#: Maximum items in the audio queue before old chunks are dropped.
#: Prevents unbounded memory growth if transcription falls behind.
AUDIO_QUEUE_MAXSIZE: int = 20

#: Maximum items in the text output queue.
TEXT_QUEUE_MAXSIZE: int = 200

# ---------------------------------------------------------------------------
# Whisper decode options tuned for Indian English
# ---------------------------------------------------------------------------
WHISPER_OPTIONS: dict = {
    # Language is fixed to English; avoids misdetection.
    "language": "en",
    # beam_size=1 → greedy decoding, lowest latency.
    "beam_size": 1,
    # best_of=1 avoids multiple sampling passes.
    "best_of": 1,
    # temperature=0 → deterministic, no random sampling.
    "temperature": 0.0,
    # Suppress common hallucinations on silence/noise.
    "no_speech_threshold": 0.6,
    # compression_ratio_threshold: reject repetitive/looping outputs.
    "compression_ratio_threshold": 2.4,
    # condition_on_previous_text helps continuity across chunks.
    "condition_on_previous_text": True,
    # Suppress blank/filler tokens (".") that pad silent chunks.
    "suppress_blank": True,
    # word_timestamps=False for speed.
    "word_timestamps": False,
    # Prompt primes the model for Indian English vocabulary & rhythm.
    "initial_prompt": (
        "The speaker has an Indian English accent. "
        "Transcribe clearly, preserving natural speech patterns."
    ),
}


# ---------------------------------------------------------------------------
# WhisperEngine
# ---------------------------------------------------------------------------

class WhisperEngine:
    """
    Real-time, offline speech-to-text engine backed by faster-whisper.

    Thread model
    ------------
    * Main thread        – creates the engine, calls start()/stop()/get_text().
    * _audio_callback()  – called by sounddevice's PortAudio thread; enqueues
                           raw PCM blocks into ``_audio_queue``.
    * _transcription_loop() – a dedicated daemon thread; drains ``_audio_queue``,
                               assembles chunks, calls Whisper, pushes results
                               into ``_text_queue``.

    All public methods are thread-safe.
    """

    def __init__(
        self,
        model_size: str = "tiny.en",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        """
        Parameters
        ----------
        model_size:
            Whisper model variant.  ``"tiny.en"`` is fastest and English-only.
        device:
            ``"cpu"`` or ``"cuda"``.  ``"cpu"`` is used by default so that the
            module works on any Windows machine without a GPU.
        compute_type:
            ``"int8"`` gives the best speed on CPU with negligible accuracy loss.
            Use ``"float16"`` if running on CUDA.
        """
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type

        # Queues ----------------------------------------------------------------
        # Raw audio blocks from the microphone callback.
        self._audio_queue: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=AUDIO_QUEUE_MAXSIZE
        )
        # Transcribed text segments ready for the caller.
        self._text_queue: queue.Queue[str] = queue.Queue(maxsize=TEXT_QUEUE_MAXSIZE)

        # Threading state -------------------------------------------------------
        self._running: threading.Event = threading.Event()
        self._transcription_thread: Optional[threading.Thread] = None

        # PortAudio stream ------------------------------------------------------
        self._stream: Optional[sd.InputStream] = None

        # Whisper model (loaded lazily in start()) ------------------------------
        self._model: Optional[WhisperModel] = None

        # Internal state for rolling context ------------------------------------
        self._context_buffer: list[np.ndarray] = []
        self._silent_chunk_count: int = 0

        logger.info(
            "WhisperEngine initialised (model=%s, device=%s, compute=%s)",
            model_size,
            device,
            compute_type,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Load the Whisper model, open the microphone stream, and begin
        real-time transcription in a background thread.

        Raises
        ------
        RuntimeError
            If the engine is already running.
        sd.PortAudioError
            If no microphone is available or the device cannot be opened.
        """
        if self._running.is_set():
            raise RuntimeError("WhisperEngine is already running.")

        # Load model -----------------------------------------------------------
        logger.info("Loading Whisper model '%s' …", self._model_size)
        self._model = WhisperModel(
            self._model_size,
            device=self._device,
            compute_type=self._compute_type,
        )
        logger.info("Model loaded.")

        # Signal threads to start ----------------------------------------------
        self._running.set()

        # Start transcription thread -------------------------------------------
        self._transcription_thread = threading.Thread(
            target=self._transcription_loop,
            name="whisper-transcription",
            daemon=True,
        )
        self._transcription_thread.start()

        # Open microphone stream -----------------------------------------------
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=1024,          # Small blocksize → lower callback latency.
                callback=self._audio_callback,
            )
            self._stream.start()
            logger.info(
                "Microphone stream opened (device: %s, rate: %d Hz).",
                sd.query_devices(kind="input")["name"],
                SAMPLE_RATE,
            )
        except sd.PortAudioError as exc:
            self._running.clear()
            logger.error("Failed to open microphone: %s", exc)
            raise

    def stop(self) -> None:
        """
        Gracefully stop transcription and release all resources.

        Safe to call multiple times.
        """
        if not self._running.is_set():
            return

        logger.info("Stopping WhisperEngine …")
        self._running.clear()

        # Close audio stream first so the callback stops enqueuing.
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing audio stream: %s", exc)
            finally:
                self._stream = None

        # Unblock the transcription thread if it's waiting on the queue.
        try:
            self._audio_queue.put_nowait(None)  # sentinel value
        except queue.Full:
            pass

        if self._transcription_thread is not None:
            self._transcription_thread.join(timeout=10)
            self._transcription_thread = None

        logger.info("WhisperEngine stopped.")

    def get_text(self) -> Optional[str]:
        """
        Return the oldest unread transcription segment, or ``None`` if the
        queue is empty.

        Non-blocking.  Intended to be polled from the main thread.

        Returns
        -------
        str | None
            A transcribed text segment, or ``None`` if nothing is available yet.
        """
        try:
            return self._text_queue.get_nowait()
        except queue.Empty:
            return None

    # ------------------------------------------------------------------
    # Internal – audio ingestion
    # ------------------------------------------------------------------

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,          # noqa: ARG002
        time_info: object,    # noqa: ARG002
        status: sd.CallbackFlags,
    ) -> None:
        """
        Called by PortAudio on every audio block.  Runs in a dedicated
        high-priority OS thread — keep it fast, no blocking I/O.

        Parameters
        ----------
        indata:
            Shape ``(frames, channels)`` float32 PCM data.
        status:
            PortAudio status flags (overflow, underflow, etc.).
        """
        if status:
            # Log but don't crash; overflows are common on loaded systems.
            logger.debug("Audio callback status: %s", status)

        if not self._running.is_set():
            return

        # Flatten to mono float32 vector.
        audio_block: np.ndarray = indata[:, 0].copy()

        try:
            self._audio_queue.put_nowait(audio_block)
        except queue.Full:
            # Drop the oldest item to make room for the new one.
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(audio_block)
                logger.debug("Audio queue full — dropped oldest block.")
            except queue.Empty:
                pass

    # ------------------------------------------------------------------
    # Internal – transcription loop
    # ------------------------------------------------------------------

    def _transcription_loop(self) -> None:
        """
        Drain the audio queue, assemble fixed-size chunks, and call Whisper.

        Runs on a dedicated daemon thread.  Exits when ``_running`` is cleared
        and a ``None`` sentinel is received (or the queue drains).
        """
        logger.info("Transcription thread started.")
        accumulator: list[np.ndarray] = []
        accumulated_samples: int = 0

        while self._running.is_set() or not self._audio_queue.empty():
            # Collect blocks until we have enough for one chunk ----------------
            try:
                block = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                # If we have a partial accumulation and no new audio for a while,
                # force-flush the remaining audio so the user gets timely output.
                if accumulated_samples > SAMPLE_RATE // 2:
                    audio_chunk = np.concatenate(accumulator)
                    self._process_chunk(audio_chunk)
                    accumulator = []
                    accumulated_samples = 0
                continue

            # None is the stop sentinel.
            if block is None:
                break

            accumulator.append(block)
            accumulated_samples += len(block)

            # Once we have a full chunk, transcribe it.
            if accumulated_samples >= CHUNK_SAMPLES:
                audio_chunk = np.concatenate(accumulator)[:CHUNK_SAMPLES]
                # Keep leftover samples for the next iteration.
                leftover = np.concatenate(accumulator)[CHUNK_SAMPLES:]
                accumulator = [leftover] if len(leftover) > 0 else []
                accumulated_samples = len(leftover)

                self._process_chunk(audio_chunk)

        # Flush any remaining audio when stopping.
        if accumulator:
            final_chunk = np.concatenate(accumulator)
            if len(final_chunk) > SAMPLE_RATE // 4:  # at least 250 ms
                self._process_chunk(final_chunk)

        logger.info("Transcription thread exited.")

    def _process_chunk(self, audio_chunk: np.ndarray) -> None:
        """
        Run Whisper on a single audio chunk.

        Skips silent chunks to avoid hallucinations.
        Prepends rolling context so cross-boundary words are recognised.

        Parameters
        ----------
        audio_chunk:
            1-D float32 array at 16 kHz.
        """
        # --- Silence detection ------------------------------------------------
        rms = float(np.sqrt(np.mean(audio_chunk ** 2)))

        if rms < SILENCE_THRESHOLD:
            self._silent_chunk_count += 1
            if self._silent_chunk_count >= MAX_SILENT_CHUNKS:
                # Long silence → clear the context buffer so stale audio
                # doesn't pollute the next utterance.
                self._context_buffer = []
                logger.debug("Silence detected; context buffer cleared.")
            return

        self._silent_chunk_count = 0

        # --- Build input with rolling context ---------------------------------
        if self._context_buffer:
            audio_with_context = np.concatenate(
                self._context_buffer[-CONTEXT_CHUNKS:] + [audio_chunk]
            )
        else:
            audio_with_context = audio_chunk

        # Update rolling context.
        self._context_buffer.append(audio_chunk)
        if len(self._context_buffer) > CONTEXT_CHUNKS + 1:
            self._context_buffer.pop(0)

        # --- Transcribe -------------------------------------------------------
        try:
            segments, info = self._model.transcribe(
                audio_with_context,
                **WHISPER_OPTIONS,
            )
            logger.debug(
                "Detected language: %s (prob=%.2f)", info.language, info.language_probability
            )

            for segment in segments:
                text = segment.text.strip()
                if text:
                    logger.debug("[%.2fs → %.2fs] %s", segment.start, segment.end, text)
                    self._push_text(text)

        except Exception as exc:  # noqa: BLE001
            logger.error("Transcription error: %s", exc, exc_info=True)

    def _push_text(self, text: str) -> None:
        """
        Push a transcribed segment into the output queue.

        If the queue is full, the oldest entry is discarded to make room.
        """
        try:
            self._text_queue.put_nowait(text)
        except queue.Full:
            try:
                self._text_queue.get_nowait()
                self._text_queue.put_nowait(text)
                logger.debug("Text queue full — dropped oldest segment.")
            except queue.Empty:
                pass

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "WhisperEngine":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        state = "running" if self._running.is_set() else "stopped"
        return (
            f"WhisperEngine(model={self._model_size!r}, "
            f"device={self._device!r}, state={state!r})"
        )


# ---------------------------------------------------------------------------
# Example usage (run this file directly to test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("  WhisperEngine — Real-time Offline Speech Recognition")
    print("  Model : tiny.en  |  Device : CPU  |  Accent : Indian EN")
    print("=" * 60)
    print("Speak into your microphone.  Press Ctrl+C to stop.\n")

    # Use the context manager for clean resource handling.
    with WhisperEngine() as engine:
        print("🎙  Listening …\n")
        try:
            while True:
                text = engine.get_text()
                if text:
                    # Print inline so output streams naturally.
                    print(f"  › {text}")
                    sys.stdout.flush()
                else:
                    # Avoid busy-looping; 50 ms poll interval is sufficient.
                    time.sleep(0.05)
        except KeyboardInterrupt:
            print("\n\nStopping …")

    print("Done.")