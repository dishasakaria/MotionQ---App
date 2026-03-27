import threading
import time
import cv2
from eyefeature import EyeTrackingMouse
from smile import run_smile_control
from head import run_head_control

def listen_for_keyboard():
    """
    Listen for keyboard commands: '1' for eye, '2' for smile, '3' for head
    Returns: mode index (0=eye, 1=smile, 2=head) or None if no valid command
    """
    print("\n⌨️  Keyboard Controls:")
    print("  Press '1' - Eye Tracking")
    print("  Press '2' - Smile Detection")
    print("  Press '3' - Head Movement")
    print("  Press 'q' - Quit\n")
    
    while True:
        command = input("\n🎮 Enter command (1/2/3/q): ").strip().lower()
        
        if command == '1':
            print("✅ Eye Tracking selected!")
            yield 0
        elif command == '2':
            print("✅ Smile Detection selected!")
            yield 1
        elif command == '3':
            print("✅ Head Movement selected!")
            yield 2
        elif command == 'q':
            print("👋 Quitting...")
            return
        else:
            print("❌ Invalid command. Use 1, 2, 3, or q")
            yield None

def run_feature(mode, cap, stop_flag):
    """Run the selected feature mode"""
    modes = [
        "👁️  Eye Tracking (Cursor + Double Blink)",
        "😊 Smile Detection (Tab Switch)",
        "🔄 Head Movement (Scroll)"
    ]
    print(f"\n{'='*50}")
    print(f"🚀 ACTIVATED: {modes[mode]}")
    print(f"{'='*50}\n")
    
    if mode == 0:
        eye = EyeTrackingMouse()
        eye.run_modular(cap, stop_flag)
    elif mode == 1:
        run_smile_control(cap, stop_flag)
    elif mode == 2:
        run_head_control(cap, stop_flag)
    
    print(f"\n{'='*50}")
    print(f"⏹️  STOPPED: {modes[mode]}")
    print(f"{'='*50}\n")

if __name__ == "__main__":
    print("="*60)
    print("🎯 MULTI-FEATURE FACE CONTROL SYSTEM")
    print("    (KEYBOARD CONTROL VERSION)")
    print("="*60)
    print("\n📋 Available Features:")
    print("  1 → Eye tracking for cursor + blink to click")
    print("  2 → Smile to switch tabs")
    print("  3 → Head movement for scrolling")
    print("\n🎮 Controls:")
    print("  • Type number and press Enter to activate feature")
    print("  • Press 'Q' in OpenCV window to stop current feature")
    print("  • Type 'q' and press Enter to exit program")
    print("="*60)
    
    # Initialize webcam ONCE at the start
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ ERROR: Cannot open webcam!")
        exit()
    
    print("\n✅ Webcam initialized successfully!")
    
    current_mode = None
    stop_flag = None
    thread = None
    
    try:
        for new_mode in listen_for_keyboard():
            if new_mode is None:
                # No valid command, keep listening
                continue
            
            # Check if we need to switch modes
            if current_mode == new_mode:
                print(f"ℹ️  Already running this feature. Press Q in window to stop it first.")
                continue
            
            # Stop current feature if running
            if thread is not None and thread.is_alive():
                print(f"\n⏸️  Stopping current feature...")
                stop_flag.set()
                thread.join(timeout=3)
                if thread.is_alive():
                    print("⚠️ Feature did not stop cleanly, forcing switch...")
                time.sleep(0.5)
            
            # Start new feature
            current_mode = new_mode
            stop_flag = threading.Event()
            thread = threading.Thread(target=run_feature, args=(current_mode, cap, stop_flag))
            thread.daemon = True
            thread.start()
            
            time.sleep(0.3)
            
    except KeyboardInterrupt:
        print("\n\n⏹️  Shutting down system...")
        if stop_flag:
            stop_flag.set()
        if thread:
            thread.join(timeout=2)
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("\n✅ Webcam released. Goodbye!\n")
