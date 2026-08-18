
import os
import queue
import threading
import tempfile
import logging
import time

import numpy as np
import sounddevice as sd
import soundfile as sf
from dotenv import load_dotenv
from groq import Groq
from settings import get_groq_api_key
load_dotenv()
log = logging.getLogger("WhisperEngine")
logging.basicConfig(level=logging.INFO)


class WhisperEngine:
    """
    Groq Whisper realtime transcription engine.
    Replaces local Faster-Whisper backend.
    """
    def get_text(self):
        """
        Compatibility method for old VoiceController architecture.
        Returns latest transcript if available.
        """
        try:
            return self.transcript_queue.get(timeout=0.1)
        except queue.Empty:
            return None
        
    def __init__(
        self,
        sample_rate=16000,
        chunk_duration=1.5,
    ):
        self.sample_rate = sample_rate
        self.chunk_duration = chunk_duration

        api_key = get_groq_api_key() or os.getenv("GROQ_API_KEY")

        if not api_key:
             raise RuntimeError(
                  "Groq API key is not configured. "
                  "Please add your Groq API key in MotionQ Settings."
                )

        self.client = Groq(api_key=api_key)

        self.audio_queue = queue.Queue()
        self.transcript_queue = queue.Queue()
        self.stop_event = threading.Event()

        self.stream = None
        self.worker_thread = None

        log.info("Groq WhisperEngine initialized.")

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            log.warning(status)

        self.audio_queue.put(indata.copy())
    

    def start(self):
        self.stop_event.clear()

        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            callback=self.audio_callback,
            dtype="float32",
        )

        self.stream.start()

        self.worker_thread = threading.Thread(
            target=self.transcription_loop,
            daemon=True,
        )

        self.worker_thread.start()

        log.info("Microphone stream started.")

    def stop(self):
        self.stop_event.set()

        if self.stream:
            self.stream.stop()
            self.stream.close()

        log.info("WhisperEngine stopped.")

    def transcription_loop(self):
        while not self.stop_event.is_set():

            frames = []

            start = time.time()

            while time.time() - start < self.chunk_duration:
                try:
                    audio = self.audio_queue.get(timeout=1)
                    frames.append(audio)

                except queue.Empty:
                    continue

            if not frames:
                continue

            audio_data = np.concatenate(frames, axis=0)

            rms = np.sqrt(np.mean(audio_data**2))

            # Ignore silence
            if rms < 0.03:
                continue

            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".wav",
                    delete=False
                ) as tmp:

                    sf.write(
                        tmp.name,
                        audio_data,
                        self.sample_rate
                    )

                    with open(tmp.name, "rb") as audio_file:

                        result = self.client.audio.transcriptions.create(
                            file=audio_file,
                            model="whisper-large-v3",
                            response_format="verbose_json",
                            language="en",
                            temperature=0,
                            prompt="Short Windows voice commands only.",
                        )

                    text = result.text.strip()

                    if text:
                        print(f"\n🎤 {text}")
                        self.transcript_queue.put(text)
                        if hasattr(self, "on_transcript"):
                            self.on_transcript(text)

                os.remove(tmp.name)

            except Exception as e:
                log.error(f"Groq transcription error: {e}")

