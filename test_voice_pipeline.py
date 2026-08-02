from voice.voice_controller import VoiceController
import time

print("\n🎤 Starting Voice Pipeline Test...")
print("====================================")
print("Try saying commands like:")
print("  open excel")
print("  open youtube")
print("  search google for AI tools")
print("  type hello world")
print("  scroll down")
print("  new tab")
print("====================================\n")

vc = VoiceController()

try:
    vc.start()

    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\n🛑 Stopping voice controller...")
    vc.stop()

except Exception as e:
    print(f"\n❌ Error: {e}")

finally:
    try:
        vc.cleanup()
    except:
        pass