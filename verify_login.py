"""Verify login endpoints against a *running* server using real seeded data.

Unlike smoke_test.py (which uses fastapi.testclient in-process), this script
hits the live HTTP server, so it confirms uvicorn + routing + CORS are wired
up correctly end-to-end.

------------------------------------------------------------------------------
HOW TO USE
------------------------------------------------------------------------------

  Terminal 1   (rebuild DB only if you don't have one yet):
      python reseeder.py

  Terminal 1   start the server:
      python run.py
      # wait until you see "Uvicorn running on http://127.0.0.1:8000"

  Terminal 2   run this verifier:
      python verify_login.py

  Optional — also test a teacher login by passing creds explicitly:
      python verify_login.py --teacher-username admin --teacher-password ...

  Optional — point at a different host:
      python verify_login.py --base-url http://127.0.0.1:8000

------------------------------------------------------------------------------
WHAT IT CHECKS, AND HOW TO READ THE OUTPUT
------------------------------------------------------------------------------

  Step 1  GET /health
    [OK]   server is up and responding
    [FAIL] server isn't running on the URL given. Start `python run.py`
           in another terminal, or pass --base-url.

  Step 2  Pick a real student from the DB
    [OK]   prints NIS + class. Login password is the last 6 chars of NISN.
    [FAIL] DB is empty or unreachable. Run `python reseeder.py` first.

  Step 3  POST /auth/student/login (happy path)
    [OK]   200 + access_token. Login is wired correctly.
    [FAIL] 401 — bcrypt didn't verify. Either the seeded password rule
           changed, or you re-seeded with a different SEED_BCRYPT_ROUNDS.
           Re-run `python reseeder.py` and try again.
    [FAIL] 500 — schema mismatch or missing column. Drop the DB and reseed.

  Step 4  POST /auth/student/login (wrong password)
    [OK]   401 with a generic "invalid credentials" detail.
    [FAIL] If you got 200, the auth check is broken — DO NOT deploy.
    [FAIL] If the message leaks "no such user" vs "wrong password",
           that's a username-enumeration bug — fix before deploy.

  Step 5  GET /confirm/my-subjects (with token)
    [OK]   200 with the student's subject list.
    [FAIL] 401 — JWT secret rotated between login and this call (server
           restart with a new random secret will do it).
    [FAIL] 403 — student role isn't allowed; check require_role wiring.

  Step 6  Teacher login (only if --teacher-username given)
    [OK]   200 + access_token.
    [FAIL] 401 — the teacher you named doesn't exist or the password
           is wrong. Note: database/teacher.json is now empty by default;
           teachers come from the ADMIN_USERNAME/ADMIN_PASSWORD env vars
           consumed by main.py, or are created in code (e.g. smoke_test
           creates `walikelas_xb` / `testpass123`).
"""
from __future__ import annotations

import argparse
import sys

import httpx

from database import SessionLocal
from models import Class, Student


def _mark(ok: bool) -> str:
    return "OK  " if ok else "FAIL"


def _line(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{_mark(ok)}] {label}{(' — ' + detail) if detail else ''}")


def step(n: int, title: str) -> None:
    print(f"\n--- Step {n}: {title} ---")


def check_health(client: httpx.Client) -> bool:
    step(1, "GET /health")
    try:
        r = client.get("/health", timeout=2.0)
    except httpx.ConnectError:
        _line("server reachable", False,
              "connection refused. Is `python run.py` running?")
        return False
    ok = r.status_code == 200 and r.json().get("status") == "ok"
    _line(f"status={r.status_code}, body={r.text[:60]}", ok)
    return ok


def pick_student() -> tuple[str, str, str, str] | None:
    step(2, "Pick a real seeded student from the DB")
    db = SessionLocal()
    try:
        s = (db.query(Student)
             .join(Student.class_)
             .filter(Class.name.like("X - %"), Student.flagged == False)
             .first())
        if s is None:
            _line("found a student", False,
                  "DB has no non-flagged students. Run `python reseeder.py`.")
            return None
        if not s.nisn or len(s.nisn) < 6:
            _line("student has usable NISN", False,
                  f"student {s.username} has NISN={s.nisn!r}; cannot derive password")
            return None
        username = s.username
        password = s.nisn[-6:]
        _line(f"picked {s.name} (kelas={s.class_.name}, nis={s.nis})", True)
        print(f"        username={username}  password=<last 6 of NISN>")
        return username, password, str(s.id), s.name
    finally:
        db.close()


def login_student(client: httpx.Client, username: str, password: str) -> str | None:
    step(3, "POST /auth/student/login (happy path)")
    r = client.post("/auth/student/login",
                    json={"username": username, "password": password})
    ok = r.status_code == 200 and "access_token" in r.json()
    _line(f"status={r.status_code}", ok,
          "" if ok else f"body={r.text[:200]}")
    if not ok:
        return None
    return r.json()["access_token"]


def login_student_wrong(client: httpx.Client, username: str) -> None:
    step(4, "POST /auth/student/login (wrong password)")
    r = client.post("/auth/student/login",
                    json={"username": username, "password": "definitely_wrong"})
    ok_status = r.status_code == 401
    _line(f"status={r.status_code}", ok_status,
          "expected 401" if not ok_status else "")
    if ok_status:
        detail = (r.json().get("detail") or "").lower()
        generic = "invalid credentials" in detail
        _line("error message is generic (no user-enumeration leak)", generic,
              "" if generic else f"detail={detail!r}")


def my_subjects(client: httpx.Client, token: str) -> None:
    step(5, "GET /confirm/my-subjects (with token)")
    r = client.get("/confirm/my-subjects",
                   headers={"Authorization": f"Bearer {token}"})
    ok = r.status_code == 200
    _line(f"status={r.status_code}", ok,
          "" if ok else f"body={r.text[:200]}")
    if ok:
        data = r.json()
        print(f"        student_name = {data.get('student_name')}")
        print(f"        class_name   = {data.get('class_name')}")
        print(f"        subjects     = {len(data.get('subjects', []))} entries")


def login_teacher(client: httpx.Client, username: str, password: str) -> None:
    step(6, "POST /auth/teacher/login")
    r = client.post("/auth/teacher/login",
                    json={"username": username, "password": password})
    ok = r.status_code == 200 and "access_token" in r.json()
    _line(f"status={r.status_code}", ok,
          "" if ok else f"body={r.text[:200]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--teacher-username")
    ap.add_argument("--teacher-password")
    args = ap.parse_args()

    print(f"Verifying against {args.base_url}")
    with httpx.Client(base_url=args.base_url) as client:
        if not check_health(client):
            return 1

        picked = pick_student()
        if picked is None:
            return 1
        username, password, _student_id, _name = picked

        token = login_student(client, username, password)
        if token is None:
            return 1

        login_student_wrong(client, username)
        my_subjects(client, token)

        if args.teacher_username and args.teacher_password:
            login_teacher(client, args.teacher_username, args.teacher_password)
        else:
            step(6, "Teacher login (skipped)")
            print("        pass --teacher-username and --teacher-password "
                  "to include this check.")

    print("\nDone. Any [FAIL] above tells you exactly what to fix.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
