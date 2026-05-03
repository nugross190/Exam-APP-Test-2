# Exam-APP-Ligar

Sistem aplikasi ujian lokal — FastAPI + SQLAlchemy backend for SMAN 5 Garut
exam administration.

## Quick start (5 minutes)

### 1. Install

```powershell
git clone <this-repo>
cd Test_2_main
py -m venv .venv
.\.venv\Scripts\Activate.ps1     # PowerShell. On Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Drop in the source data

These files are **not in git** (student PII / large fixtures). Place them in
the repo root:

| File | Purpose |
|---|---|
| `daftar_peserta_kelas_X_updated.xlsx`  | Grade X student roster |
| `daftar_peserta_kelas_XI_updated.xlsx` | Grade XI student roster |
| `schedule_parsed.csv`                  | Long/tidy exam schedule (committed; regenerate as below if needed) |

If you only have the original schedule grid (`schedule_sample.xlsx`), convert
it once:

```powershell
py database/schedule_parser.py schedule_sample.xlsx schedule_parsed.csv
```

### 3. Seed the database

`reseeder.py` deletes any existing `hadir_exam.db` and runs the full seed:

```powershell
py reseeder.py
```

Or step through manually:

```powershell
py seed.py --teachers --create-tables
py seed.py --xi daftar_peserta_kelas_XI_updated.xlsx `
           --x  daftar_peserta_kelas_X_updated.xlsx `
           --schedule schedule_parsed.csv
```

### 4. Run the server

The admin teacher is bootstrapped from env vars on first start. Set them in
the same shell *before* starting the server:

```powershell
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "somepass"
py run.py
```

Server is now at `http://127.0.0.1:8000`. The static client is mounted at `/`.

### 5. Verify it works

In a second shell (server still running in the first):

```powershell
py verify_login.py --teacher-username admin --teacher-password somepass
```

All six steps should be `[OK]`. Each `[FAIL]` prints what to fix — see the
docstring at the top of `verify_login.py` for the full diagnostic table.

For the in-process test suite (no running server required):

```powershell
py smoke_test.py
```

## Environment variables

| Var | Default | Used in | Notes |
|---|---|---|---|
| `DATABASE_URL`         | `sqlite:///./hadir_exam.db` | `database.py`     | Postgres URI also accepted (`postgres://` is normalized to `postgresql://`) |
| `ADMIN_USERNAME`       | *(unset → no admin bootstrap)* | `main.py`         | Bootstraps an admin teacher row on app startup |
| `ADMIN_PASSWORD`       | *(required if `ADMIN_USERNAME` set)* | `main.py`   | Hashed with bcrypt before insert |
| `ADMIN_FULL_NAME`      | falls back to `ADMIN_USERNAME` | `main.py`        | |
| `ADMIN_ROLE`           | `admin` | `main.py`         | |
| `JWT_SECRET`           | `dev-secret-change-me-in-prod` | `routers/auth.py` | **Change in production.** A new random value invalidates all existing tokens |
| `SEED_BCRYPT_ROUNDS`   | `10` | `seed.py`         | Set to `4` for fast local seeding; `10–12` for prod |
| `CORS_ORIGINS`         | `*` | `main.py`         | Comma-separated list. Tighten before production |
| `UPLOAD_DIR`           | `uploads` | `main.py`         | Where question images live (Week 2+) |

PowerShell sets env vars with `$env:NAME = "value"`; bash uses
`export NAME=value`; cmd.exe uses `set NAME=value`. They live only for that
shell session.

## Repo layout

```
main.py                    FastAPI app, CORS, static mount, admin bootstrap
database.py                SQLAlchemy engine + get_db dependency
models.py                  15 ORM models (§1 of the spec)
seed.py                    CLI: seed teachers, students, subjects, exams
reseeder.py                Convenience wrapper: delete DB + run seeds
run.py                     Convenience wrapper: start uvicorn with reload
smoke_test.py              In-process end-to-end test (TestClient)
verify_login.py            HTTP test against a running server
parsers/excel.py           Roster + schedule parsers (openpyxl + csv)
database/schedule_parser.py  One-off xlsx grid → tidy CSV converter (pandas)
database/teacher.json      Teacher seed source (empty by default — see security)
routers/
  auth.py                  JWT login + role dependencies
  confirm.py               Week 1: student data confirmation flow
  exam.py                  Week 2: exam engine (start, answer, submit, score)
  violation.py             Week 2: anti-cheat (tab switch, panic button)
  teacher.py               Week 2: question CRUD, image upload
  admin.py                 Week 2: confirm exam, monitor live, import CSV
static/                    Minimal HTML clients (index.html, teacher.html)
REVIEW_CHECKLIST.md        Week 1 self-review questions
exam_app_build_spec.txt    Original product spec
```

## Security notes

- `database/teacher.json` is intentionally `[]`. The previous version shipped
  plaintext defaults (`admin/admin123`); use `ADMIN_USERNAME` / `ADMIN_PASSWORD`
  env vars instead.
- `JWT_SECRET` defaults to a placeholder. Set a long random value in prod
  (e.g. `python -c "import secrets; print(secrets.token_urlsafe(64))"`).
- Student rosters and the SQLite DB are gitignored — never commit them.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: httpx` | venv not active or deps not installed | `pip install -r requirements.txt` |
| `verify_login` step 1 fails  | server not running | start `py run.py` in another shell |
| `verify_login` step 2 fails  | DB empty | `py reseeder.py` |
| Step 3 returns 401           | wrong NIS/NISN, or DB seeded with different bcrypt cost | reseed |
| Step 5 shows `subjects = 0`  | student is in a class with no schedule (e.g. X-A, XI-A) | the verifier now filters these out — re-pull and retry |
| Step 6 returns 401           | teacher doesn't exist | set `ADMIN_USERNAME`/`ADMIN_PASSWORD` *before* starting the server |
| `export: command not found`  | PowerShell, not bash | use `$env:NAME = "value"` instead |
