"""End-to-end smoke test for Week 2 routers.

Covers exam, teacher, violation, and admin endpoints in-process via
TestClient. Companion to smoke_test.py (which covers Week 1 auth/confirm).

Run with: python smoke_test_week2.py

Setup strategy:
  - Creates a sentinel Subject ("__SMOKE_W2_SUBJ__") owned by a test
    teacher. Re-runs delete and recreate it, so state is reproducible.
  - Creates one test Exam with scheduled_at in the past and time_end at
    23:59 so the start/answer/submit flow has an open window.
  - Seeds 3 questions of mixed types (pg, tf, complex_mc) to exercise
    the scoring branches.
"""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta

import bcrypt
from fastapi.testclient import TestClient

from database import SessionLocal
from main import app
from models import (
    AnswerChoice, Choice, Class, ClassSubject, Exam, ExamResult,
    ExamSession, ExpelledFlag, Question, SessionViolation, Student,
    StudentAnswer, Subject, Teacher,
)


client = TestClient(app)

SUBJ_NAME = "__SMOKE_W2_SUBJ__"
TEACHER_USERNAME = "smoke_w2_teacher"
TEACHER_PASSWORD = "testpass123"
ADMIN_USERNAME = "smoke_w2_admin"
ADMIN_PASSWORD = "testpass123"


def banner(s: str) -> None:
    print(f"\n{'='*70}\n  {s}\n{'='*70}")


def assert_eq(label, got, want) -> None:
    ok = got == want
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {label}: got={got!r} want={want!r}")
    if not ok:
        sys.exit(1)


def assert_true(label, cond) -> None:
    mark = "OK  " if cond else "FAIL"
    print(f"  [{mark}] {label}")
    if not cond:
        sys.exit(1)


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()


# ---------------------------------------------------------------------------
# Setup: tear down prior test rows, then create teachers/subject/exam/questions
# ---------------------------------------------------------------------------

banner("Setup: reset sentinel data + create test teachers, subject, exam")
db = SessionLocal()
try:
    # Wipe prior test artifacts (cascade through Exam -> Question -> Choice,
    # plus sessions/results/violations).
    prior_subj = db.query(Subject).filter_by(name=SUBJ_NAME).first()
    if prior_subj is not None:
        for ex in list(prior_subj.exams):
            for sess in db.query(ExamSession).filter_by(exam_id=ex.id).all():
                db.query(AnswerChoice).filter(
                    AnswerChoice.student_answer_id.in_(
                        [a.id for a in db.query(StudentAnswer)
                         .filter_by(session_id=sess.id).all()]
                    )
                ).delete(synchronize_session=False)
                db.query(StudentAnswer).filter_by(session_id=sess.id).delete()
                db.query(SessionViolation).filter_by(session_id=sess.id).delete()
                db.query(ExpelledFlag).filter_by(session_id=sess.id).delete()
                db.query(ExamResult).filter_by(session_id=sess.id).delete()
                db.delete(sess)
            db.delete(ex)
        db.delete(prior_subj)
    db.commit()

    # Test teacher (owns the sentinel subject)
    t = db.query(Teacher).filter_by(username=TEACHER_USERNAME).first()
    if t is None:
        t = Teacher(username=TEACHER_USERNAME, password_hash=_hash(TEACHER_PASSWORD),
                    full_name="Smoke W2 Teacher", role="teacher")
        db.add(t)

    # Admin teacher (for /admin endpoints)
    a = db.query(Teacher).filter_by(username=ADMIN_USERNAME).first()
    if a is None:
        a = Teacher(username=ADMIN_USERNAME, password_hash=_hash(ADMIN_PASSWORD),
                    full_name="Smoke W2 Admin", role="admin")
        db.add(a)

    # Second teacher to verify ownership isolation
    other = db.query(Teacher).filter_by(username="smoke_w2_other_teacher").first()
    if other is None:
        other = Teacher(username="smoke_w2_other_teacher",
                        password_hash=_hash("testpass123"),
                        full_name="Other Teacher", role="teacher")
        db.add(other)
    db.flush()

    subj = Subject(name=SUBJ_NAME, teacher_id=t.id)
    db.add(subj)
    db.flush()

    # Pick a real student so /exam/start has a believable user
    student = (db.query(Student)
               .filter(Student.flagged == False)
               .first())
    assert_true("found a real student to use", student is not None)
    if not student.nisn or len(student.nisn) < 6:
        print("  [FAIL] picked student has no usable NISN; reseed and retry")
        sys.exit(1)
    student_username = student.username
    student_password = student.nisn[-6:]
    student_id = student.id
    student_class_id = student.class_id

    # Exam: scheduled in the recent past, ends at 23:59 today so it's open.
    now = datetime.utcnow()
    exam = Exam(
        subject_id=subj.id,
        title="Smoke W2 Exam",
        duration_minutes=90,
        scheduled_at=now - timedelta(minutes=5),
        time_end=time(23, 59),
        status="scheduled",
        admin_confirmed=False,
    )
    db.add(exam)
    db.flush()

    # Three questions: pg (single correct of 4), tf (true/false),
    # complex_mc (2 correct of 4).
    q_pg = Question(exam_id=exam.id, question_type="pg", body="2 + 2 = ?",
                    item_points=10.0, choices_count=4)
    q_tf = Question(exam_id=exam.id, question_type="tf",
                    body="The earth is flat.", item_points=5.0, choices_count=2)
    q_cmc = Question(exam_id=exam.id, question_type="complex_mc",
                     body="Pick the prime numbers.", item_points=10.0,
                     choices_count=4)
    for q in (q_pg, q_tf, q_cmc):
        db.add(q)
    db.flush()

    # PG choices: only "4" is correct (weight=1.0)
    pg_choices = [
        Choice(question_id=q_pg.id, body="3", is_correct=False, weight=0.0),
        Choice(question_id=q_pg.id, body="4", is_correct=True,  weight=1.0),
        Choice(question_id=q_pg.id, body="5", is_correct=False, weight=0.0),
        Choice(question_id=q_pg.id, body="22", is_correct=False, weight=0.0),
    ]
    # TF: "False" is correct
    tf_choices = [
        Choice(question_id=q_tf.id, body="True",  is_correct=False, weight=0.0),
        Choice(question_id=q_tf.id, body="False", is_correct=True,  weight=1.0),
    ]
    # complex_mc: 2 and 3 correct, weight 0.5 each
    cmc_choices = [
        Choice(question_id=q_cmc.id, body="2", is_correct=True,  weight=0.5),
        Choice(question_id=q_cmc.id, body="3", is_correct=True,  weight=0.5),
        Choice(question_id=q_cmc.id, body="4", is_correct=False, weight=0.0),
        Choice(question_id=q_cmc.id, body="9", is_correct=False, weight=0.0),
    ]
    for c in pg_choices + tf_choices + cmc_choices:
        db.add(c)
    db.commit()

    exam_id = exam.id
    pg_correct_id = next(c.id for c in pg_choices if c.is_correct)
    tf_correct_id = next(c.id for c in tf_choices if c.is_correct)
    cmc_correct_ids = [c.id for c in cmc_choices if c.is_correct]
    q_pg_id, q_tf_id, q_cmc_id = q_pg.id, q_tf.id, q_cmc.id

    print(f"  Test teacher: {TEACHER_USERNAME}, admin: {ADMIN_USERNAME}")
    print(f"  Test student: {student.name} (nis={student.nis})")
    print(f"  Test exam_id: {exam_id} ({3} questions)")
finally:
    db.close()


# ---------------------------------------------------------------------------
# Login as everyone we'll need
# ---------------------------------------------------------------------------

banner("Login: teacher, admin, student")

r = client.post("/auth/teacher/login",
                json={"username": TEACHER_USERNAME, "password": TEACHER_PASSWORD})
assert_eq("teacher login", r.status_code, 200)
T = {"Authorization": f"Bearer {r.json()['access_token']}"}

r = client.post("/auth/teacher/login",
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
assert_eq("admin login", r.status_code, 200)
A = {"Authorization": f"Bearer {r.json()['access_token']}"}

r = client.post("/auth/teacher/login",
                json={"username": "smoke_w2_other_teacher", "password": "testpass123"})
assert_eq("other-teacher login", r.status_code, 200)
T_OTHER = {"Authorization": f"Bearer {r.json()['access_token']}"}

r = client.post("/auth/student/login",
                json={"username": student_username, "password": student_password})
assert_eq("student login", r.status_code, 200)
S = {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------------------------------------------------------------------
# §7  Teacher router
# ---------------------------------------------------------------------------

banner("§7.1  GET /teacher/exams")
r = client.get("/teacher/exams", headers=T)
assert_eq("status", r.status_code, 200)
ours = [e for e in r.json() if e["id"] == exam_id]
assert_true("our test exam appears for the owning teacher", len(ours) == 1)
assert_eq("question_count", ours[0]["question_count"], 3)

banner("§7.1  GET /teacher/exams — other teacher does NOT see it")
r = client.get("/teacher/exams", headers=T_OTHER)
assert_eq("status", r.status_code, 200)
assert_true("other teacher cannot see our exam",
            all(e["id"] != exam_id for e in r.json()))

banner("§7.2  POST /teacher/exam/{id}/question — valid payload")
r = client.post(f"/teacher/exam/{exam_id}/question", headers=T, json={
    "question_type": "pg", "body": "5 + 5 = ?", "item_points": 2.0,
    "choices_count": 4,
    "choices": [
        {"body": "10", "is_correct": True},
        {"body": "11", "is_correct": False},
        {"body": "9",  "is_correct": False},
        {"body": "12", "is_correct": False},
    ],
})
assert_eq("status", r.status_code, 201)
new_q_id = r.json()["question_id"]
assert_true("question_id returned", isinstance(new_q_id, str) and len(new_q_id) > 8)

banner("§7.2  POST question — bad payload (no correct choice) -> 400")
r = client.post(f"/teacher/exam/{exam_id}/question", headers=T, json={
    "question_type": "pg", "body": "x?", "item_points": 1.0, "choices_count": 4,
    "choices": [{"body": str(i), "is_correct": False} for i in range(4)],
})
assert_eq("status", r.status_code, 400)

banner("§7.2  POST question — other teacher's attempt -> 404 (ownership leak guard)")
r = client.post(f"/teacher/exam/{exam_id}/question", headers=T_OTHER, json={
    "question_type": "pg", "body": "?", "item_points": 1.0, "choices_count": 4,
    "choices": [
        {"body": "a", "is_correct": True},
        {"body": "b", "is_correct": False},
        {"body": "c", "is_correct": False},
        {"body": "d", "is_correct": False},
    ],
})
assert_eq("status", r.status_code, 404)

banner("§7.3  PUT /teacher/question/{id}")
r = client.put(f"/teacher/question/{new_q_id}", headers=T, json={
    "question_type": "pg", "body": "5 + 5 equals?", "item_points": 3.0,
    "choices_count": 4,
    "choices": [
        {"body": "10", "is_correct": True},
        {"body": "20", "is_correct": False},
        {"body": "5",  "is_correct": False},
        {"body": "55", "is_correct": False},
    ],
})
assert_eq("status", r.status_code, 200)
assert_eq("item_points updated", r.json()["item_points"], 3.0)

banner("GET /teacher/exam/{id}/questions — count after add")
r = client.get(f"/teacher/exam/{exam_id}/questions", headers=T)
assert_eq("status", r.status_code, 200)
assert_eq("question count after add", len(r.json()), 4)


# ---------------------------------------------------------------------------
# §8  Admin router
# ---------------------------------------------------------------------------

banner("§8  GET /admin/exam/{id}/monitor — student token rejected")
r = client.get(f"/admin/exam/{exam_id}/monitor", headers=S)
assert_eq("status", r.status_code, 403)

banner("§8  GET /admin/exam/{id}/monitor — teacher (non-admin) token rejected")
r = client.get(f"/admin/exam/{exam_id}/monitor", headers=T)
assert_eq("status", r.status_code, 403)

banner("§5  POST /exam/start — fails before admin_confirmed")
r = client.post("/exam/start", headers=S, json={"exam_id": exam_id})
assert_eq("status", r.status_code, 400)
assert_true("error mentions confirm", "confirm" in r.json()["detail"].lower())

banner("§8.1  POST /admin/exam/{id}/confirm")
r = client.post(f"/admin/exam/{exam_id}/confirm", headers=A)
assert_eq("status", r.status_code, 200)
assert_eq("admin_confirmed", r.json()["admin_confirmed"], True)

banner("§7.2  POST question after confirm -> 400 (frozen)")
r = client.post(f"/teacher/exam/{exam_id}/question", headers=T, json={
    "question_type": "tf", "body": "frozen?", "item_points": 1.0,
    "choices_count": 2,
    "choices": [
        {"body": "True",  "is_correct": True},
        {"body": "False", "is_correct": False},
    ],
})
assert_eq("status", r.status_code, 400)
assert_true("error mentions frozen", "frozen" in r.json()["detail"].lower())


# ---------------------------------------------------------------------------
# §5  Exam engine — student lifecycle
# ---------------------------------------------------------------------------

banner("§5.1  POST /exam/start")
r = client.post("/exam/start", headers=S, json={"exam_id": exam_id})
assert_eq("status", r.status_code, 200)
state = r.json()
session_id = state["session_id"]
assert_eq("status active", state["status"], "active")
assert_eq("questions_total", state["questions_total"], 4)  # 3 + 1 we added
assert_true("time_remaining > 0", state["time_remaining_seconds"] > 0)

banner("§5.1  POST /exam/start again -> resumes (idempotent)")
r = client.post("/exam/start", headers=S, json={"exam_id": exam_id})
assert_eq("status", r.status_code, 200)
assert_eq("same session_id", r.json()["session_id"], session_id)

banner("§5.2  GET /exam/{session_id}")
r = client.get(f"/exam/{session_id}", headers=S)
assert_eq("status", r.status_code, 200)

banner("§5.3  GET /exam/{session_id}/questions")
r = client.get(f"/exam/{session_id}/questions", headers=S)
assert_eq("status", r.status_code, 200)
qs = r.json()["questions"]
assert_eq("question count returned", len(qs), 4)
# Choices should NOT leak is_correct/weight
sample_choice = qs[0]["choices"][0]
assert_true("choices stripped of is_correct/weight",
            "is_correct" not in sample_choice and "weight" not in sample_choice)

banner("§5.4  POST /exam/{session_id}/answer — pg correct")
r = client.post(f"/exam/{session_id}/answer", headers=S, json={
    "question_id": q_pg_id, "choice_ids": [pg_correct_id],
})
assert_eq("status", r.status_code, 200)

banner("§5.4  POST answer — tf correct")
r = client.post(f"/exam/{session_id}/answer", headers=S, json={
    "question_id": q_tf_id, "choice_ids": [tf_correct_id],
})
assert_eq("status", r.status_code, 200)

banner("§5.4  POST answer — complex_mc both correct")
r = client.post(f"/exam/{session_id}/answer", headers=S, json={
    "question_id": q_cmc_id, "choice_ids": cmc_correct_ids,
})
assert_eq("status", r.status_code, 200)

banner("§5.4  POST answer — pg with 2 choices -> 400")
r = client.post(f"/exam/{session_id}/answer", headers=S, json={
    "question_id": q_pg_id, "choice_ids": [pg_correct_id, cmc_correct_ids[0]],
})
# pg only allows 1 choice; also the cmc choice doesn't belong to pg, so
# the validator may 400 on either ground. Both are acceptable.
assert_eq("status", r.status_code, 400)

banner("§5.5  POST /exam/{session_id}/submit")
r = client.post(f"/exam/{session_id}/submit", headers=S)
assert_eq("status", r.status_code, 200)
result = r.json()
# pg(10) + tf(5) + complex_mc(10) + the 4th question we added (3, unanswered)
assert_eq("max_score", result["max_score"], 28.0)
# We answered 3 of 4 fully correctly: 10 + 5 + 10 = 25
assert_eq("total_score", result["total_score"], 25.0)
assert_eq("percentage", result["percentage"], round(25.0/28.0*100, 2))

banner("§5.5  POST submit again -> 400 (already submitted)")
r = client.post(f"/exam/{session_id}/submit", headers=S)
assert_eq("status", r.status_code, 400)


# ---------------------------------------------------------------------------
# §6  Violation router  — exercise on a separate fresh exam
# ---------------------------------------------------------------------------

banner("Setup: create a second exam for violation testing")
db = SessionLocal()
try:
    subj_v = db.query(Subject).filter_by(name=SUBJ_NAME).first()
    now = datetime.utcnow()
    exam_v = Exam(subject_id=subj_v.id, title="Smoke W2 Exam (violations)",
                  duration_minutes=90, scheduled_at=now - timedelta(minutes=5),
                  time_end=time(23, 59), status="scheduled", admin_confirmed=True)
    db.add(exam_v)
    db.flush()
    qv = Question(exam_id=exam_v.id, question_type="tf", body="?",
                  item_points=1.0, choices_count=2)
    db.add(qv)
    db.flush()
    db.add(Choice(question_id=qv.id, body="True",  is_correct=True,  weight=1.0))
    db.add(Choice(question_id=qv.id, body="False", is_correct=False, weight=0.0))
    db.commit()
    exam_v_id = exam_v.id
    qv_id = qv.id
finally:
    db.close()

r = client.post("/exam/start", headers=S, json={"exam_id": exam_v_id})
assert_eq("start status", r.status_code, 200)
session_v_id = r.json()["session_id"]

banner("§6.1  POST /violation — bad event_type -> 400")
r = client.post(f"/violation/{session_v_id}", headers=S,
                json={"event_type": "screenshot"})
assert_eq("status", r.status_code, 400)

banner("§6.1  POST /violation #1 -> warning")
r = client.post(f"/violation/{session_v_id}", headers=S,
                json={"event_type": "tab_switch"})
assert_eq("status", r.status_code, 200)
assert_eq("count", r.json()["violation_count"], 1)
assert_eq("expelled", r.json()["expelled"], False)

banner("§6.2  POST /violation #2 -> 30s lockout")
r = client.post(f"/violation/{session_v_id}", headers=S,
                json={"event_type": "tab_switch"})
assert_eq("status", r.status_code, 200)
assert_eq("count", r.json()["violation_count"], 2)
assert_true("locked_until set", r.json()["locked_until"] is not None)

banner("§6.2  POST /violation #3 -> expelled")
r = client.post(f"/violation/{session_v_id}", headers=S,
                json={"event_type": "tab_switch"})
assert_eq("status", r.status_code, 200)
assert_eq("count", r.json()["violation_count"], 3)
assert_eq("expelled", r.json()["expelled"], True)
assert_eq("status expelled", r.json()["status"], "expelled")

banner("§5.4  POST answer to expelled session -> 400")
r = client.post(f"/exam/{session_v_id}/answer", headers=S, json={
    "question_id": qv_id, "choice_ids": [],
})
# 400 because session.status == 'expelled'
assert_eq("status", r.status_code, 400)

banner("§6.4  GET /violation/{session_id} — admin sees full history")
r = client.get(f"/violation/{session_v_id}", headers=A)
assert_eq("status", r.status_code, 200)
assert_eq("violation rows", len(r.json()["violations"]), 3)

banner("§6.4  GET /violation/{session_id} — student token rejected")
r = client.get(f"/violation/{session_v_id}", headers=S)
# get_current_teacher rejects the student JWT
assert_eq("status", r.status_code, 403)


# ---------------------------------------------------------------------------
# §6.3  Panic — needs a third fresh exam since session_v is expelled
# ---------------------------------------------------------------------------

banner("Setup: third exam for panic test")
db = SessionLocal()
try:
    subj_v = db.query(Subject).filter_by(name=SUBJ_NAME).first()
    now = datetime.utcnow()
    exam_p = Exam(subject_id=subj_v.id, title="Smoke W2 Exam (panic)",
                  duration_minutes=90, scheduled_at=now - timedelta(minutes=5),
                  time_end=time(23, 59), status="scheduled", admin_confirmed=True)
    db.add(exam_p)
    db.flush()
    qp = Question(exam_id=exam_p.id, question_type="tf", body="?",
                  item_points=1.0, choices_count=2)
    db.add(qp)
    db.flush()
    db.add(Choice(question_id=qp.id, body="True",  is_correct=True,  weight=1.0))
    db.add(Choice(question_id=qp.id, body="False", is_correct=False, weight=0.0))
    db.commit()
    exam_p_id = exam_p.id
finally:
    db.close()

r = client.post("/exam/start", headers=S, json={"exam_id": exam_p_id})
assert_eq("start status", r.status_code, 200)
session_p_id = r.json()["session_id"]

banner("§6.3  POST /violation/{id}/panic")
r = client.post(f"/violation/{session_p_id}/panic", headers=S)
assert_eq("status", r.status_code, 200)
assert_eq("session status", r.json()["status"], "panic")


# ---------------------------------------------------------------------------
# §8.2  Monitor — by now we should have at least 3 sessions on our subject
# ---------------------------------------------------------------------------

banner("§8.2  GET /admin/exam/{id}/monitor")
r = client.get(f"/admin/exam/{exam_v_id}/monitor", headers=A)
assert_eq("status", r.status_code, 200)
mon = r.json()
assert_true("at least 1 session", mon["total"] >= 1)
assert_eq("expelled count", mon["expelled"], 1)
assert_true("violations rows present", len(mon["violations"]) >= 1)


banner("ALL WEEK 2 SMOKE TESTS PASSED")
