import cv2
import mediapipe as mp
import pyautogui
import numpy as np
from collections import deque
import time

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.001

class EyeTrackingMouse:
    def __init__(self, smoothing_frames=7, sensitivity=3.0):
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        
        self.screen_w, self.screen_h = pyautogui.size()
        self.smoothing_frames = smoothing_frames
        self.x_coords = deque(maxlen=smoothing_frames)
        self.y_coords = deque(maxlen=smoothing_frames)
        self.sensitivity = sensitivity
        
        self.blink_threshold = 0.018

        # Double Blink State Machine
        self.blink_state = "IDLE"
        self.blink_start_time = 0
        self.blink_end_time = 0
        self.double_blink_window = 0.5
        self.min_blink_duration = 0.04
        self.max_blink_duration = 0.4
        self.click_cooldown = 0
        
        self.last_message = ""
        self.last_message_color = (255, 255, 255)
        self.last_message_frames = 0
        
        # Eye landmarks
        self.LEFT_EYE_TOP = 159
        self.LEFT_EYE_BOTTOM = 145
        self.RIGHT_EYE_TOP = 386
        self.RIGHT_EYE_BOTTOM = 374
        self.LEFT_IRIS = [474, 475, 476, 477]
        self.RIGHT_IRIS = [469, 470, 471, 472]
        
        self.is_calibrated = False
        self.iris_range_x = None
        self.iris_range_y = None
        
    def get_iris_position(self, landmarks, frame_w, frame_h):
        left_iris_x = np.mean([landmarks[i].x for i in self.LEFT_IRIS])
        left_iris_y = np.mean([landmarks[i].y for i in self.LEFT_IRIS])
        right_iris_x = np.mean([landmarks[i].x for i in self.RIGHT_IRIS])
        right_iris_y = np.mean([landmarks[i].y for i in self.RIGHT_IRIS])
        
        avg_x = (left_iris_x + right_iris_x) / 2
        avg_y = (left_iris_y + right_iris_y) / 2
        
        return avg_x, avg_y
    
    def detect_blink(self, landmarks, frame_h):
        left_eye_height = abs(landmarks[self.LEFT_EYE_TOP].y - landmarks[self.LEFT_EYE_BOTTOM].y) * frame_h
        right_eye_height = abs(landmarks[self.RIGHT_EYE_TOP].y - landmarks[self.RIGHT_EYE_BOTTOM].y) * frame_h
        avg_eye_height = (left_eye_height + right_eye_height) / 2
        return avg_eye_height < self.blink_threshold * frame_h
    
    def smooth_coordinates(self, x, y):
        self.x_coords.append(x)
        self.y_coords.append(y)
        return np.mean(self.x_coords), np.mean(self.y_coords)
    
    def map_to_screen(self, iris_x, iris_y, frame_w, frame_h):
        center_x, center_y = 0.5, 0.5
        offset_x = (iris_x - center_x) * self.sensitivity
        offset_y = (iris_y - center_y) * self.sensitivity
        
        if abs(offset_x) > 0.1:
            offset_x = np.sign(offset_x) * (abs(offset_x) ** 1.2)
        if abs(offset_y) > 0.1:
            offset_y = np.sign(offset_y) * (abs(offset_y) ** 1.2)
        
        screen_x = self.screen_w / 2 + offset_x * self.screen_w
        screen_y = self.screen_h / 2 + offset_y * self.screen_h
        
        screen_x = max(0, min(self.screen_w - 1, screen_x))
        screen_y = max(0, min(self.screen_h - 1, screen_y))
        
        return screen_x, screen_y

    def process_frame(self, frame):
        """
        Process a single frame for eye tracking + blink detection.
        Moves the mouse and handles double-blink click.
        Draws debug info on the frame and returns it.
        Call this from any loop that already has a frame.
        """
        frame_h, frame_w, _ = frame.shape
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb_frame)

        smooth_x = smooth_y = None

        if results.multi_face_landmarks:
            landmarks = results.multi_face_landmarks[0].landmark

            iris_x, iris_y = self.get_iris_position(landmarks, frame_w, frame_h)
            screen_x, screen_y = self.map_to_screen(iris_x, iris_y, frame_w, frame_h)
            smooth_x, smooth_y = self.smooth_coordinates(screen_x, screen_y)

            is_blinking = self.detect_blink(landmarks, frame_h)

            if not is_blinking:
                pyautogui.moveTo(smooth_x, smooth_y, duration=0.005)

            current_time = time.time()

            if self.click_cooldown > 0:
                self.click_cooldown -= 1

            # Double blink state machine
            if self.blink_state == "IDLE":
                if is_blinking:
                    self.blink_state = "BLINK_1"
                    self.blink_start_time = current_time

            elif self.blink_state == "BLINK_1":
                if not is_blinking:
                    duration = current_time - self.blink_start_time
                    if self.min_blink_duration < duration < self.max_blink_duration:
                        self.blink_state = "WAIT_FOR_2"
                        self.blink_end_time = current_time
                    else:
                        self.blink_state = "IDLE"

            elif self.blink_state == "WAIT_FOR_2":
                if is_blinking:
                    self.blink_state = "BLINK_2"
                    self.blink_start_time = current_time
                elif (current_time - self.blink_end_time) > self.double_blink_window:
                    self.blink_state = "IDLE"

            elif self.blink_state == "BLINK_2":
                if not is_blinking:
                    duration = current_time - self.blink_start_time
                    if self.min_blink_duration < duration < self.max_blink_duration:
                        if self.click_cooldown == 0:
                            try:
                                pyautogui.click()
                                self.last_message = "✅ DOUBLE BLINK CLICK!"
                                self.last_message_color = (0, 255, 0)
                                self.last_message_frames = 30
                                print("✅ Double Blink -> CLICK!")
                                self.click_cooldown = 30
                            except Exception as e:
                                print(f"Click error: {e}")
                    self.blink_state = "IDLE"

            # Draw debug overlay
            status_color = (100, 100, 100)
            if self.blink_state == "WAIT_FOR_2":
                status_color = (0, 255, 255)
            elif is_blinking:
                status_color = (0, 0, 255)

            cv2.circle(frame, (30, 30), 10, status_color, -1)
            cv2.putText(frame, f"Eye: {self.blink_state}", (50, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, status_color, 1)

            if self.last_message_frames > 0:
                cv2.putText(frame, self.last_message, (frame_w // 2 - 150, 100),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, self.last_message_color, 3)
                self.last_message_frames -= 1
        else:
            cv2.putText(frame, "No face (eye)", (10, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

        return frame

    def run_modular(self, cap, stop_flag):
        """Standalone eye tracking loop (eye-only mode)."""
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        print("👁️ Eye Tracking Active!")
        print("Move eyes to control cursor, DOUBLE BLINK to click")
        print("Press Q to quit\n")
        
        cv2.namedWindow('Eye Tracking - Q to Quit', cv2.WINDOW_NORMAL)
        cv2.moveWindow('Eye Tracking - Q to Quit', 100, 100)
        cv2.resizeWindow('Eye Tracking - Q to Quit', 640, 480)
        
        while cap.isOpened() and not stop_flag.is_set():
            success, frame = cap.read()
            if not success:
                break
            
            frame = cv2.flip(frame, 1)
            frame = self.process_frame(frame)  # ← uses shared method
            
            cv2.imshow('Eye Tracking - Q to Quit', frame)
            
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('c'):
                self.x_coords.clear()
                self.y_coords.clear()
                print("Calibration reset")
        
        cv2.destroyAllWindows()