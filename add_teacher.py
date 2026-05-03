"""Add or verify teachers in the HADIR Exam App database.

Three modes:

  Bulk import from CSV (long format — one row per teacher-subject):
      py add_teacher.py --from teachers.csv

  Single teacher (no file, ad hoc):
      py add_teacher.py --username sari --full-name "Bu Sari" \\
          --role teacher --subjects "Matematika,Fisika"
      # password generated and printed; pass --password to set explicitly

  Verify only (no writes, just counts):
      py add_teacher.py --verify

CSV format expected by --from:
  nip,full_name,role,username,subject_name
  197307271998021001,"Effen Heryana, M.Pd",teacher,effen_heryana,Kepala Sekolah
  198001011990012345,"Sari Indrawati S.Pd",teacher,sari_indrawati,Matematika
  198001011990012345,"Sari Indrawati S.Pd",teacher,sari_indrawati,Fisika

Rules:
  - `nip` is the dedup key. Rows with the same nip create ONE teacher
    and link ALL their listed subjects.
  - Inconsistencies across rows for the same nip (different name, role,
    or username) cause a hard error — fix the CSV.
  - `username` must be unique across all teachers.
  - `subject_name` must already exist in the DB (created by seed.py).
    Rows with unknown subjects are reported but don't block import.
  - Passwords are generated randomly per teacher and printed ONCE at the
    end. Nothing writes them to disk; copy now or you'll have to reset.
  - If a teacher with the same username already exists, the row is
    skipped (no password reset, no reassignment).
"""
from __future__ import annotations

import argparse
import csv
import os
import secrets
import sys
from collections import defaultdict
from pathlib import Path

import bcrypt

from database import SessionLocal
from models import Subject, Teacher

_BCRYPT_ROUNDS = int(os.environ.get("SEED_BCRYPT_ROUNDS", "10"))
_REQUIRED_COLS = {"nip", "full_name", "role", "username", "subject_name"}


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()


def _gen_password() -> str:
    # ~11 url-safe chars; printable, no shell-quoting hazards
    return secrets.token_urlsafe(8)


# ---------------------------------------------------------------------------
# CSV parsing & validation
# ---------------------------------------------------------------------------

def parse_csv(path: Path) -> tuple[dict, list[tuple[str, str]]]:
    """Return (teachers_by_nip, rows_subject_links).

    teachers_by_nip: { nip: {"full_name", "role", "username"} }
    rows_subject_links: [(nip, subject_name), ...] in original order
    Raises ValueError on header or consistency problems.
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or set(reader.fieldnames) - _REQUIRED_COLS != set() \
                or _REQUIRED_COLS - set(reader.fieldnames) != set():
            raise ValueError(
                f"CSV columns {reader.fieldnames!r} != required {sorted(_REQUIRED_COLS)}"
            )

        teachers: dict[str, dict] = {}
        links: list[tuple[str, str]] = []
        for i, row in enumerate(reader, start=2):  # header is line 1
            nip = (row["nip"] or "").strip()
            entry = {
                "full_name": (row["full_name"] or "").strip(),
                "role":      (row["role"] or "").strip().lower(),
                "username":  (row["username"] or "").strip(),
            }
            if not all(entry.values()):
                raise ValueError(f"line {i}: missing one of full_name/role/username")
            # Auto-fill nip when blank or 'N/A' so honorary/non-PNS staff
            # don't need a fake number invented in the CSV. Username is
            # already unique, so __nonip_<username> stays unique too.
            if not nip or nip.lower() in {"n/a", "na"}:
                nip = f"__nonip_{entry['username']}"
            existing = teachers.get(nip)
            if existing is None:
                teachers[nip] = entry
            elif existing != entry:
                raise ValueError(
                    f"line {i}: nip={nip} disagrees with earlier row\n"
                    f"  earlier: {existing}\n  here:    {entry}"
                )
            subject = (row["subject_name"] or "").strip()
            if subject:
                links.append((nip, subject))
    return teachers, links


# ---------------------------------------------------------------------------
# Bulk import
# ---------------------------------------------------------------------------

def import_csv(csv_path: Path) -> int:
    teachers, links = parse_csv(csv_path)
    print(f"Parsed {len(teachers)} teachers, {len(links)} teacher-subject rows.")

    # Cross-row username collision check (different nip, same username)
    username_to_nip: dict[str, str] = {}
    for nip, t in teachers.items():
        prior = username_to_nip.get(t["username"])
        if prior is not None and prior != nip:
            raise ValueError(
                f"username collision: {t['username']!r} used by both nip={prior} and nip={nip}"
            )
        username_to_nip[t["username"]] = nip

    db = SessionLocal()
    created_creds: list[tuple[str, str]] = []  # (username, plaintext_password)
    skipped_existing: list[str] = []
    nip_to_teacher_id: dict[str, str] = {}
    try:
        for nip, t in teachers.items():
            existing = db.query(Teacher).filter_by(username=t["username"]).first()
            if existing is not None:
                skipped_existing.append(t["username"])
                nip_to_teacher_id[nip] = existing.id
                continue
            pw = _gen_password()
            row = Teacher(
                username=t["username"],
                password_hash=_hash(pw),
                full_name=t["full_name"],
                role=t["role"],
            )
            db.add(row)
            db.flush()
            nip_to_teacher_id[nip] = row.id
            created_creds.append((t["username"], pw))

        # Link subjects
        linked = 0
        missing_subjects: dict[str, int] = defaultdict(int)
        for nip, subject_name in links:
            subj = db.query(Subject).filter_by(name=subject_name).first()
            if subj is None:
                missing_subjects[subject_name] += 1
                continue
            subj.teacher_id = nip_to_teacher_id[nip]
            linked += 1

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"\nCreated {len(created_creds)} teachers, "
          f"skipped {len(skipped_existing)} (username already existed).")
    print(f"Linked {linked} subject assignments.")
    if missing_subjects:
        print(f"\nWARNING: {len(missing_subjects)} subject names not found in DB "
              f"(check spelling against seeded Subject.name):")
        for name, n in sorted(missing_subjects.items()):
            print(f"  - {name!r} ({n} row{'s' if n > 1 else ''})")

    if created_creds:
        print("\n" + "="*60)
        print("ONE-TIME PASSWORDS — save now. Not stored anywhere.")
        print("="*60)
        width = max(len(u) for u, _ in created_creds)
        print(f"  {'username'.ljust(width)}  password")
        print(f"  {'-'*width}  {'-'*12}")
        for u, p in created_creds:
            print(f"  {u.ljust(width)}  {p}")
    return 0


# ---------------------------------------------------------------------------
# Single teacher
# ---------------------------------------------------------------------------

def add_one(username: str, full_name: str, role: str,
            subjects_csv: str, password: str | None) -> int:
    db = SessionLocal()
    try:
        if db.query(Teacher).filter_by(username=username).first() is not None:
            print(f"username {username!r} already exists; nothing to do.")
            return 0
        pw = password or _gen_password()
        t = Teacher(username=username, password_hash=_hash(pw),
                    full_name=full_name, role=role)
        db.add(t)
        db.flush()

        linked = 0
        missing = []
        for name in [s.strip() for s in subjects_csv.split(",") if s.strip()]:
            subj = db.query(Subject).filter_by(name=name).first()
            if subj is None:
                missing.append(name)
                continue
            subj.teacher_id = t.id
            linked += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Created {username} ({role}); linked {linked} subjects.")
    for m in missing:
        print(f"  WARNING: subject {m!r} not found, skipped")
    if password is None:
        print(f"\nGenerated password (save now): {pw}")
    return 0


# ---------------------------------------------------------------------------
# Verify (read-only)
# ---------------------------------------------------------------------------

def verify() -> int:
    db = SessionLocal()
    try:
        teachers = db.query(Teacher).all()
        subjects = db.query(Subject).all()
        with_owner = [s for s in subjects if s.teacher_id is not None]
        without_owner = [s for s in subjects if s.teacher_id is None]

        print(f"Teachers in DB: {len(teachers)}")
        by_role: dict[str, int] = defaultdict(int)
        for t in teachers:
            by_role[t.role] += 1
        for role, n in sorted(by_role.items()):
            print(f"  role={role}: {n}")

        print(f"\nSubjects in DB: {len(subjects)}")
        print(f"  with assigned teacher: {len(with_owner)}")
        print(f"  unassigned:            {len(without_owner)}")

        if without_owner:
            print("\nUnassigned subjects:")
            for s in sorted(without_owner, key=lambda x: x.name):
                print(f"  - {s.name}")

        # Per-teacher subject count (top 10 most-loaded)
        teacher_load: dict[str, int] = defaultdict(int)
        teacher_name: dict[str, str] = {}
        for s in with_owner:
            teacher_load[s.teacher_id] += 1
        for t in teachers:
            teacher_name[t.id] = t.username
        if teacher_load:
            print("\nTop teachers by subject count:")
            top = sorted(teacher_load.items(), key=lambda kv: -kv[1])[:10]
            for tid, n in top:
                print(f"  {teacher_name.get(tid, tid):30s}  {n}")
    finally:
        db.close()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--from", dest="csv_path", help="bulk import from CSV")
    g.add_argument("--username", help="single-teacher mode: username")
    g.add_argument("--verify", action="store_true",
                   help="print teacher/subject counts and exit (no writes)")

    ap.add_argument("--full-name", help="single-teacher mode")
    ap.add_argument("--role", default="teacher",
                    help="single-teacher mode (default: teacher)")
    ap.add_argument("--subjects", default="",
                    help="single-teacher mode: comma-separated subject names")
    ap.add_argument("--password", help="single-teacher mode: explicit password")

    args = ap.parse_args(argv[1:])

    if args.verify:
        return verify()
    if args.csv_path:
        return import_csv(Path(args.csv_path))
    # single-teacher mode
    if not args.full_name:
        ap.error("--full-name is required when using --username")
    return add_one(args.username, args.full_name, args.role,
                   args.subjects, args.password)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
