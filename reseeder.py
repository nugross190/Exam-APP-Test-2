import os
import subprocess
import sys

def reset_database():
    db_file = "hadir_exam.db"
    
    # 1. Delete the existing database to ensure a clean slate
    if os.path.exists(db_file):
        print(f"[*] Deleting existing database: {db_file}...")
        try:
            os.remove(db_file)
        except Exception as e:
            print(f"[!] Error deleting database: {e}")
            sys.exit(1)
    else:
        print(f"[*] No existing {db_file} found. Starting fresh.")

    # 2. Run the teacher seed and table creation
    print("\n[*] Step 1: Creating tables and seeding Teachers...")
    try:
        subprocess.run(["python", "seed.py", "--teachers", "--create-tables"], check=True)
    except subprocess.CalledProcessError:
        print("[!] Failed to seed teachers.")
        sys.exit(1)

    # 3. Run the main data parsing and seeding
    print("\n[*] Step 2: Parsing Excel files and seeding Students, Subjects, and Exams...")
    try:
        subprocess.run([
            "python", "seed.py",
            "--xi", "daftar_peserta_kelas_XI_updated.xlsx",
            "--x", "daftar_peserta_kelas_X_updated.xlsx",
            "--schedule", "schedule_parsed.csv"
        ], check=True)
    except subprocess.CalledProcessError:
        print("[!] Failed to seed Excel data.")
        sys.exit(1)

    print("\n[SUCCESS] Database has been completely rebuilt and seeded!")

if __name__ == "__main__":
    reset_database()