import cv2
import pyautogui
import time
import mediapipe as mp
import math

def run_smile_control(cap, stop_flag, calibration_manager, eye_tracker=None):
    """
    Smile detection using lip corner distance (MediaPipe).
    Pass an EyeTrackingMouse instance as eye_tracker to keep
    eye tracking active while smile mode is running.
    """
    
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05
    
    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )
    
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    last_smile_time = 0
    smile_cooldown = 2.0
    
    calibration = calibration_manager.get_smile_calibration()
    if not calibration:
        print("⚠️ No calibration found! Using defaults.")
        neutral_distance = 50.0
        smile_threshold = 10.0
    else:
        neutral_distance = calibration.get('neutral_intensity', 50.0)
        smile_threshold = calibration.get('smile_threshold', 10.0)
    
    trigger_distance = neutral_distance + smile_threshold
    
    eye_status = "👁️ + 😊 MODE" if eye_tracker else "😊 SMILE MODE"
    
    print("\n" + "="*50)
    print(f"😊 SMILE DETECTION ACTIVE  {'(Eye tracking also ON)' if eye_tracker else ''}")
    print("="*50)
    print(f"✅ Neutral distance: {neutral_distance:.1f}")
    print(f"✅ Trigger when distance > {trigger_distance:.1f}")
    print("✅ Press Q to quit")
    print("="*50 + "\n")
    
    cv2.namedWindow('Smile Control - Q to Quit', cv2.WINDOW_NORMAL)
    cv2.moveWindow('Smile Control - Q to Quit', 100, 100)
    cv2.resizeWindow('Smile Control - Q to Quit', 640, 480)
    
    LEFT_LIP_CORNER = 61
    RIGHT_LIP_CORNER = 291
    
    while not stop_flag.is_set():
        ret, frame = cap.read()
        if not ret:
            break
        
        frame = cv2.flip(frame, 1)

        # ── Eye tracking runs on same frame ──────────────────────────
        if eye_tracker is not None:
            frame = eye_tracker.process_frame(frame)
        # ─────────────────────────────────────────────────────────────

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb_frame)
        
        h, w, _ = frame.shape
        status = "No face detected"
        color = (0, 0, 255)
        current_distance = 0.0
        
        if results.multi_face_landmarks:
            landmarks = results.multi_face_landmarks[0].landmark
            
            left_corner = landmarks[LEFT_LIP_CORNER]
            right_corner = landmarks[RIGHT_LIP_CORNER]
            
            x1 = int(left_corner.x * w)
            y1 = int(left_corner.y * h)
            x2 = int(right_corner.x * w)
            y2 = int(right_corner.y * h)
            
            current_distance = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
            
            cv2.circle(frame, (x1, y1), 5, (0, 255, 255), -1)
            cv2.circle(frame, (x2, y2), 5, (0, 255, 255), -1)
            cv2.line(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
            
            cv2.putText(frame, f"Distance: {current_distance:.1f}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            cv2.putText(frame, f"Need > {trigger_distance:.1f}", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            
            progress = min(100, (current_distance / trigger_distance) * 100)
            bar_width = int((progress / 100) * 300)
            cv2.rectangle(frame, (10, 80), (310, 100), (100, 100, 100), 2)
            cv2.rectangle(frame, (10, 80), (10 + bar_width, 100), (0, 255, 0), -1)
            cv2.putText(frame, f"{progress:.0f}%", (320, 95),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            
            if current_distance > trigger_distance:
                if time.time() - last_smile_time > smile_cooldown:
                    print(f"😊 SMILE DETECTED! Switching tab (distance={current_distance:.1f})")
                    
                    try:
                        cv2.setWindowProperty('Smile Control - Q to Quit',
                                              cv2.WND_PROP_VISIBLE, 0)
                        time.sleep(0.15)
                        pyautogui.hotkey('ctrl', 'tab')
                        last_smile_time = time.time()
                        status = "✅ TAB SWITCHED!"
                        color = (0, 255, 0)
                        print("✅ Tab switched successfully!\n")
                        time.sleep(0.1)
                        cv2.setWindowProperty('Smile Control - Q to Quit',
                                              cv2.WND_PROP_VISIBLE, 1)
                    except Exception as e:
                        print(f"❌ Error: {e}")
                        status = "❌ Error!"
                        color = (0, 0, 255)
                        try:
                            cv2.setWindowProperty('Smile Control - Q to Quit',
                                                  cv2.WND_PROP_VISIBLE, 1)
                        except:
                            pass
                else:
                    cooldown_rem = smile_cooldown - (time.time() - last_smile_time)
                    status = f"Cooldown {cooldown_rem:.1f}s"
                    color = (255, 255, 0)
            
            elif current_distance > (neutral_distance + smile_threshold * 0.5):
                status = "Smile BIGGER!"
                color = (0, 165, 255)
            else:
                status = "Neutral - SMILE!"
                color = (150, 150, 150)
        
        # Show active mode in top-right corner
        cv2.putText(frame, eye_status, (w - 220, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 255, 128) if eye_tracker else (200, 200, 200), 2)

        cv2.putText(frame, status, (10, 140), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 3)
        cv2.imshow('Smile Control - Q to Quit', frame)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    
    cv2.destroyAllWindows()
    print("\n✅ Smile detection stopped\n")