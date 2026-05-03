import subprocess
import sys

def start_server():
    print("[*] Starting HADIR Exam App with auto-reload...")
    print("[*] Press Ctrl+C to stop.")
    
    try:
        # sys.executable translates to exactly 'python' or 'py' depending on your environment
        # This safely executes: python -m uvicorn main:app --reload
        subprocess.run(
            [sys.executable, "-m", "uvicorn", "main:app", "--reload", "--host", "127.0.0.1", "--port", "8000"],
            check=True
        )
    except KeyboardInterrupt:
        print("\n[*] Server stopped safely.")
    except Exception as e:
        print(f"\n[!] Server crashed or failed to start: {e}")

if __name__ == "__main__":
    start_server()