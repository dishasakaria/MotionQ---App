"""
mainController.py — Face Control System main entry point.

Modes (activate by voice):
  'eye'      (0) → Eye tracking + double blink to click
  'smile'    (1) → Eye tracking + smile to switch tabs
  'head'     (2) → Head tilt to scroll
  'voice'    (3) → Full voice command controller
  'keyboard' (4) → Floating on-screen keyboard + eye tracking

Controls: Q = stop current mode | Ctrl+C = exit
"""

import threading
import time
import cv2
import json
import pyaudio

from eyefeature          import EyeTrackingMouse
from smile               import run_smile_control
from head                import run_head_control
from calibration_manager import CalibrationManager
from voice_commands      import VoiceCommandController
from virtual_keyboard    import run_keyboard_mode   # ← NEW

voice_mode_active = threading.Event()

# ── Speech recognition imports ────────────────────────────────────────────────

VOSK_AVAILABLE = False
try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except ImportError:
    print("⚠️  Vosk not installed")

SPEECH_REC_AVAILABLE = False
try:
    import speech_recognition as sr
    SPEECH_REC_AVAILABLE = True
except ImportError:
    print("⚠️  SpeechRecognition not installed")

# ── Mode configuration ────────────────────────────────────────────────────────

VALID_COMMANDS = {
    'eye':      0,
    'smile':    1,
    'head':     2,
    'voice':    3,
    'keyboard': 4,   # ← NEW
}

MODE_NAMES = {
    0: "👁️  Eye Tracking",
    1: "👁️😊 Eye + Smile",
    2: "🔄 Head Scroll",
    3: "🎙️ Voice Commands",
    4: "⌨️  Virtual Keyboard",   # ← NEW
}


# ══════════════════════════════════════════════════════════════════════════════
# Voice recognition listeners (for switching modes)
# ══════════════════════════════════════════════════════════════════════════════

def listen_with_vosk():
    print("\n⏳ Loading Vosk model (OFFLINE)...")
    try:
        model = Model("model")
    except Exception as e:
        print(f"❌ Vosk model error: {e}")
        return None

    rec = KaldiRecognizer(model, 16000)
    rec.SetWords(True)

    # Grammar includes all mode keywords
    grammar = '["eye", "smile", "head", "voice", "keyboard", "[unk]"]'
    rec.SetGrammar(grammar)

    p = pyaudio.PyAudio()
    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=8000,
        )
        stream.start_stream()
    except Exception as e:
        print(f"❌ Microphone error: {e}")
        return None

    print("✅ Vosk Voice System Ready (OFFLINE)")
    print("🔊 Listening... (Say commands CLEARLY)\n")

    last_command_time = 0
    cooldown = 2.5

    while True:
        try:
            if voice_mode_active.is_set():
                stream.read(8000, exception_on_overflow=False)  # drain
                time.sleep(0.1)
                continue

            data = stream.read(8000, exception_on_overflow=False)
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                text   = result.get('text', '').lower().strip()

                if not text or time.time() - last_command_time < cooldown:
                    continue

                detected = None
                if text in VALID_COMMANDS:
                    detected = text
                else:
                    for word in text.split():
                        if word in VALID_COMMANDS:
                            detected = word
                            break

                if detected:
                    mode_idx = VALID_COMMANDS[detected]
                    print(f"✅ COMMAND: {detected.upper()}\n")
                    last_command_time = time.time()
                    yield mode_idx

        except Exception:
            continue


def listen_with_google():
    print("\n✅ Using Google Web Speech API (ONLINE)")
    print("🔊 Listening... (Say commands CLEARLY)\n")

    recognizer = sr.Recognizer()
    recognizer.energy_threshold       = 5000
    recognizer.dynamic_energy_threshold = False
    recognizer.pause_threshold        = 0.8
    recognizer.phrase_threshold       = 0.3
    recognizer.non_speaking_duration  = 0.5

    microphone = sr.Microphone()

    print("🎤 Calibrating for ambient noise (stay QUIET for 3 seconds)...")
    with microphone as source:
        recognizer.adjust_for_ambient_noise(source, duration=3)
    print(f"✅ Calibration done! (Threshold: {recognizer.energy_threshold})\n")

    last_command_time = 0
    cooldown = 2.5

    while True:
        try:
            if voice_mode_active.is_set():
                time.sleep(0.5)
                continue

            with microphone as source:
                print("🎤 Ready for mode command...")
                audio = recognizer.listen(source, timeout=10, phrase_time_limit=3)

            try:
                text = recognizer.recognize_google(
                    audio, language='en-US', show_all=False
                ).lower().strip()

                if time.time() - last_command_time < cooldown:
                    remaining = cooldown - (time.time() - last_command_time)
                    print(f"⏳ Cooldown ({remaining:.1f}s remaining)")
                    continue

                print(f"🗣️  Heard: '{text}'")

                detected = None
                if text in VALID_COMMANDS:
                    detected = text
                else:
                    for word in text.split():
                        if word in VALID_COMMANDS:
                            detected = word
                            break

                if detected:
                    mode_idx = VALID_COMMANDS[detected]
                    print(f"✅ COMMAND: {detected.upper()}\n")
                    last_command_time = time.time()
                    yield mode_idx
                else:
                    print("❓ Not a mode command — ignored\n")

            except sr.UnknownValueError:
                continue
            except sr.RequestError as e:
                print(f"❌ Google API error: {e}")
                time.sleep(2)
                continue

        except sr.WaitTimeoutError:
            continue
        except Exception:
            continue


def listen_for_commands():
    print("\n🎤 Initializing Voice Recognition System...")
    print("⚠️  IMPORTANT: Wait 2.5 seconds between commands")

    if VOSK_AVAILABLE:
        print("📡 Attempting OFFLINE recognition (Vosk)...")
        vosk_gen = listen_with_vosk()
        if vosk_gen is not None:
            yield from vosk_gen
            return
        print("❌ Vosk failed to initialise")

    if SPEECH_REC_AVAILABLE:
        print("📡 Falling back to ONLINE recognition (Google)...")
        time.sleep(1)
        yield from listen_with_google()
        return

    print("\n" + "=" * 60)
    print("❌ NO VOICE RECOGNITION AVAILABLE")
    print("=" * 60)
    while True:
        yield None
        time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════════
# Feature runner
# ══════════════════════════════════════════════════════════════════════════════

def run_feature(mode: int, cap, stop_flag: threading.Event,
                calibration_manager, eye_tracker):
    """
    Run the selected feature mode.
    eye_tracker is always passed so smile/keyboard mode can use it simultaneously.
    """
    print(f"\n{'='*50}")
    print(f"🚀 ACTIVATED: {MODE_NAMES[mode]}")
    print(f"{'='*50}\n")

    if mode == 0:
        # Eye tracking only
        eye_tracker.run_modular(cap, stop_flag)

    elif mode == 1:
        # Smile detection + eye tracking simultaneously
        run_smile_control(cap, stop_flag, calibration_manager, eye_tracker=eye_tracker)

    elif mode == 2:
        # Head tilt scrolling
        run_head_control(cap, stop_flag, calibration_manager)

    elif mode == 3:
        # Full voice command controller
        voice_mode_active.set()
        vc = VoiceCommandController(cap=cap)
        result = vc.run(stop_flag)
        voice_mode_active.clear()

        # If the user asked to open the keyboard from within voice mode,
        # switch to keyboard mode immediately
        if result == 'KEYBOARD':
            print("\n🔀 Voice → Keyboard mode\n")
            stop_flag.clear()
            run_keyboard_mode(cap, stop_flag, eye_tracker=eye_tracker)

    elif mode == 4:
        # Virtual keyboard + eye tracking (for hands-free key clicking)
        run_keyboard_mode(cap, stop_flag, eye_tracker=eye_tracker)

    print(f"\n⏹️  STOPPED: {MODE_NAMES[mode]}\n")
    print("🔊 Listening for mode commands...\n")


# ══════════════════════════════════════════════════════════════════════════════
# Calibration helpers (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

def auto_calibrate_smile(cap, calibration_manager):
    """Calibrate smile using lip corner distance."""
    print("\n📸 Smile Calibration Starting...")
    print("⚠️  Keep NEUTRAL face (no smile) for 3 seconds!")

    import mediapipe as mp
    import math

    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    LEFT_LIP_CORNER  = 61
    RIGHT_LIP_CORNER = 291

    cv2.namedWindow("Calibration", cv2.WINDOW_NORMAL)
    cv2.moveWindow("Calibration", 100, 100)
    cv2.resizeWindow("Calibration", 640, 480)

    # ── Neutral phase ──────────────────────────────────────────────────────
    neutral_samples = []
    start = time.time()
    while time.time() - start < 3:
        ret, frame = cap.read()
        if not ret:
            continue
        frame     = cv2.flip(frame, 1)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results   = face_mesh.process(rgb_frame)
        if results.multi_face_landmarks:
            h, w, _ = frame.shape
            lm = results.multi_face_landmarks[0].landmark
            lc = lm[LEFT_LIP_CORNER];  rc = lm[RIGHT_LIP_CORNER]
            x1, y1 = int(lc.x * w), int(lc.y * h)
            x2, y2 = int(rc.x * w), int(rc.y * h)
            dist = math.sqrt((x2-x1)**2 + (y2-y1)**2)
            neutral_samples.append(dist)
            cv2.circle(frame, (x1, y1), 5, (0, 255, 255), -1)
            cv2.circle(frame, (x2, y2), 5, (0, 255, 255), -1)
            cv2.line(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.putText(frame, f"Distance: {dist:.1f}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        remaining = int(3 - (time.time() - start))
        cv2.putText(frame, f"NEUTRAL FACE: {remaining}s", (80, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 3)
        cv2.imshow("Calibration", frame)
        cv2.waitKey(1)

    neutral_distance = (sum(neutral_samples) / len(neutral_samples)
                        if neutral_samples else 50.0)
    print(f"✅ Neutral distance: {neutral_distance:.1f}")

    # ── Smile phase ────────────────────────────────────────────────────────
    print("Now SMILE BIG for 3 seconds!")
    time.sleep(0.5)
    smile_samples = []
    start = time.time()
    while time.time() - start < 3:
        ret, frame = cap.read()
        if not ret:
            continue
        frame     = cv2.flip(frame, 1)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results   = face_mesh.process(rgb_frame)
        if results.multi_face_landmarks:
            h, w, _ = frame.shape
            lm = results.multi_face_landmarks[0].landmark
            lc = lm[LEFT_LIP_CORNER];  rc = lm[RIGHT_LIP_CORNER]
            x1, y1 = int(lc.x * w), int(lc.y * h)
            x2, y2 = int(rc.x * w), int(rc.y * h)
            dist = math.sqrt((x2-x1)**2 + (y2-y1)**2)
            smile_samples.append(dist)
            cv2.circle(frame, (x1, y1), 5, (0, 255, 0), -1)
            cv2.circle(frame, (x2, y2), 5, (0, 255, 0), -1)
            cv2.line(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame, f"Distance: {dist:.1f}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        remaining = int(3 - (time.time() - start))
        cv2.putText(frame, f"SMILE BIG: {remaining}s", (180, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
        cv2.imshow("Calibration", frame)
        cv2.waitKey(1)

    smile_distance = (sum(smile_samples) / len(smile_samples)
                      if smile_samples else neutral_distance * 1.2)
    cv2.destroyAllWindows()

    diff = smile_distance - neutral_distance
    smile_threshold = max(diff * 0.6, 8)
    calibration_manager.set_smile_calibration(neutral_distance, smile_threshold)
    print(f"✅ Smile distance : {smile_distance:.1f}")
    print(f"✅ Difference     : {diff:.1f}")
    print(f"✅ Threshold      : {smile_threshold:.1f}")
    print(f"✅ Triggers when  > {neutral_distance + smile_threshold:.1f}\n")


def auto_calibrate_head(cap, calibration_manager):
    print("\n📸 Head Calibration Starting...")
    print("Hold head STEADY for 3 seconds!")

    import mediapipe as mp
    mp_face_mesh = mp.solutions.face_mesh
    face_mesh    = mp_face_mesh.FaceMesh(refine_landmarks=True)

    cv2.namedWindow("Calibration", cv2.WINDOW_NORMAL)
    cv2.moveWindow("Calibration", 100, 100)
    cv2.resizeWindow("Calibration", 640, 480)

    neutral_samples = []
    start = time.time()
    while time.time() - start < 3:
        ret, frame = cap.read()
        if not ret:
            continue
        frame     = cv2.flip(frame, 1)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results   = face_mesh.process(rgb_frame)
        if results.multi_face_landmarks:
            h, w, _ = frame.shape
            nose = results.multi_face_landmarks[0].landmark[1]
            nose_y = int(nose.y * h)
            neutral_samples.append(nose_y)
            cv2.circle(frame, (int(nose.x * w), nose_y), 6, (0, 255, 0), -1)
        remaining = int(3 - (time.time() - start))
        cv2.putText(frame, f"HOLD STEADY: {remaining}s", (150, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
        cv2.imshow("Calibration", frame)
        cv2.waitKey(1)

    neutral_y = (sum(neutral_samples) / len(neutral_samples)
                 if neutral_samples else 240)
    cv2.destroyAllWindows()
    calibration_manager.set_head_calibration(neutral_y)
    print(f"✅ Head neutral: {neutral_y:.1f}\n")


def initial_calibration(cap, calibration_manager):
    print("\n" + "=" * 60)
    print("🎯 AUTOMATIC CALIBRATION")
    print("=" * 60)

    if not calibration_manager.is_calibrated('smile'):
        auto_calibrate_smile(cap, calibration_manager)
    else:
        cal = calibration_manager.get_smile_calibration()
        print(f"✅ Smile already calibrated:")
        print(f"   Neutral  : {cal['neutral_intensity']:.1f}")
        print(f"   Threshold: {cal['smile_threshold']:.1f}\n")

    if not calibration_manager.is_calibrated('head'):
        auto_calibrate_head(cap, calibration_manager)
    else:
        print("✅ Head already calibrated\n")

    print("=" * 60)
    print("✅ CALIBRATION COMPLETE!")
    print("=" * 60)
    print("\n🎤 Say: 'EYE', 'SMILE', 'HEAD', 'VOICE', or 'KEYBOARD'\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("🎯 FACE CONTROL SYSTEM")
    print("=" * 60)
    print("\n📋 Features:")
    print("   • 'EYE'      → Eye tracking + double blink to click")
    print("   • 'SMILE'    → Smile to switch tabs  +  eye tracking")
    print("   • 'HEAD'     → Head tilt to scroll")
    print("   • 'VOICE'    → Full voice command controller")
    print("   • 'KEYBOARD' → Floating on-screen keyboard + eye tracking")
    print("\nControls: Q = Stop current mode | Ctrl+C = Exit")
    print("=" * 60)

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ Cannot open webcam!")
        exit()

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    print("\n✅ Webcam ready!")

    calibration_manager = CalibrationManager()
    initial_calibration(cap, calibration_manager)

    # One shared eye tracker instance (reused across modes)
    eye_tracker = EyeTrackingMouse()

    current_mode = None
    stop_flag    = None
    thread       = None

    try:
        for new_mode in listen_for_commands():
            if new_mode is None:
                continue

            if current_mode == new_mode:
                print("ℹ️  Already running this mode. Press Q to stop first.")
                continue

            # Stop whatever is running
            if thread is not None and thread.is_alive():
                print("\n⏸️  Stopping current mode...")
                stop_flag.set()
                thread.join(timeout=3)
                time.sleep(0.5)

            # Start new mode
            current_mode = new_mode
            stop_flag    = threading.Event()
            thread = threading.Thread(
                target=run_feature,
                args=(current_mode, cap, stop_flag,
                      calibration_manager, eye_tracker),
            )
            thread.daemon = True
            thread.start()
            time.sleep(0.3)

    except KeyboardInterrupt:
        print("\n\n⏹️  Shutting down...")
        if stop_flag:
            stop_flag.set()
        if thread:
            thread.join(timeout=2)

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("\n✅ Goodbye!\n")