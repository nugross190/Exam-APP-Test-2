from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
from models import Exam, ExamSession, SessionViolation, Teacher
from routers.auth import require_role

router = APIRouter(prefix="/admin", tags=["admin"])

# --- Schemas ---
class ConfirmResponse(BaseModel):
    exam_id: str
    admin_confirmed: bool
    status: str

class MonitorResponse(BaseModel):
    total: int
    active: int
    submitted: int
    expelled: int
    panic: int
    violations: list  # Keep simple for now
    homeroom_flags: list

# --- 8.1 POST /admin/exam/{exam_id}/confirm ---
@router.post("/exam/{exam_id}/confirm", response_model=ConfirmResponse)
def confirm_exam(
    exam_id: str,
    db: Session = Depends(get_db),
    # Only admins or owners can open an exam
    payload: dict = Depends(require_role("admin", "owner"))
):
    exam = db.query(Exam).filter_by(id=exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    exam.admin_confirmed = True
    
    # If the scheduled time has already passed or is happening now, mark it open
    if datetime.utcnow() >= exam.scheduled_at:
        exam.status = "open"
    else:
        exam.status = "scheduled"

    db.commit()
    db.refresh(exam)
    
    return ConfirmResponse(
        exam_id=exam.id,
        admin_confirmed=exam.admin_confirmed,
        status=exam.status
    )

# --- 8.2 GET /admin/exam/{exam_id}/monitor ---
@router.get("/exam/{exam_id}/monitor", response_model=MonitorResponse)
def monitor_live_exam(
    exam_id: str,
    db: Session = Depends(get_db),
    payload: dict = Depends(require_role("admin", "owner", "homeroom"))
):
    sessions = db.query(ExamSession).filter_by(exam_id=exam_id).all()
    
    active = sum(1 for s in sessions if s.status == "active")
    submitted = sum(1 for s in sessions if s.status == "submitted")
    expelled = sum(1 for s in sessions if s.status == "expelled")
    panic = sum(1 for s in sessions if s.status == "panic")

    # Fetch active violations (count > 0)
    violations = [
        {
            "student_name": s.student.name,
            "class": s.student.class_.name,
            "violation_count": s.violation_count
        }
        for s in sessions if s.violation_count > 0
    ]

    homeroom_flags = [
        {"session_id": s.id, "student_name": s.student.name}
        for s in sessions if s.status == "expelled"
    ]

    return MonitorResponse(
        total=len(sessions),
        active=active,
        submitted=submitted,
        expelled=expelled,
        panic=panic,
        violations=violations,
        homeroom_flags=homeroom_flags
    )