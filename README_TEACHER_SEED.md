# Teacher Portal Seeding Guide

## Problem Fixed
The teacher portal was returning 500 errors because there were no teachers in the database. The app now includes a new seeding mechanism to populate teachers from a JSON file.

## Solution Overview

### 1. New File: `database/teacher.json`
Created a JSON file containing teacher accounts:

```json
[
  {
    "username": "admin",
    "password": "admin123",
    "full_name": "Administrator",
    "role": "admin"
  },
  {
    "username": "guru_matematika",
    "password": "guru123",
    "full_name": "Guru Matematika",
    "role": "teacher"
  }
]
```

### 2. Updated `seed.py`
Added a new `--teachers` flag and `seed_teachers()` function that:
- Reads teachers from `database/teacher.json`
- Hashes passwords with bcrypt
- Creates Teacher records idempotently (skips existing usernames)
- Supports roles: `admin`, `teacher`, `owner`, `homeroom`

## Usage

### Seed Teachers Only
```bash
# Fresh install (creates tables + seeds teachers)
python seed.py --teachers --create-tables

# Add teachers to existing database
python seed.py --teachers
```

### Seed Full Database (Students + Teachers)
```bash
# First seed teachers
python seed.py --teachers --create-tables

# Then seed students and schedule
python seed.py --xi daftar_peserta_kelas_XI_updated.xlsx \
               --x daftar_peserta_kelas_X_updated.xlsx \
               --schedule database/schedule_sample.xlsx
```

## Testing the Teacher Portal

### 1. Start the Server
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 2. Login as Admin
```bash
curl -X POST http://localhost:8000/auth/teacher/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

Response:
```json
{
  "access_token": "eyJhbGci...",
  "teacher_id": "...",
  "name": "Administrator",
  "role": "admin"
}
```

### 3. Access Teacher Endpoints
```bash
# List exams for this teacher's subjects
curl http://localhost:8000/teacher/exams \
  -H "Authorization: Bearer <YOUR_TOKEN>"

# List questions in an exam
curl http://localhost:8000/teacher/exam/{exam_id}/questions \
  -H "Authorization: Bearer <YOUR_TOKEN>"

# Create a question
curl -X POST http://localhost:8000/teacher/exam/{exam_id}/question \
  -H "Authorization: Bearer <YOUR_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "question_type": "pg",
    "body": "What is 2+2?",
    "item_points": 1.0,
    "choices_count": 4,
    "choices": [
      {"body": "3", "is_correct": false},
      {"body": "4", "is_correct": true},
      {"body": "5", "is_correct": false},
      {"body": "6", "is_correct": false}
    ]
  }'
```

## Customizing Teachers

Edit `database/teacher.json` to add your own teachers:

```json
[
  {
    "username": "your_username",
    "password": "your_password",
    "full_name": "Your Name",
    "role": "teacher"
  },
  {
    "username": "math_teacher",
    "password": "secure_password",
    "full_name": "John Doe",
    "role": "teacher"
  },
  {
    "username": "science_head",
    "password": "secure_password",
    "full_name": "Jane Smith",
    "role": "homeroom"
  }
]
```

**Available roles:**
- `teacher` - Regular teacher who can create/edit questions for their subjects
- `admin` - Administrator with full access
- `owner` - System owner
- `homeroom` - Homeroom teacher (wali kelas) who can view expelled students

## Troubleshooting

### 500 Error on `/teacher/exams`
- **Cause**: No teachers in database or JWT authentication failing
- **Fix**: Run `python seed.py --teachers --create-tables`

### 401 Unauthorized
- **Cause**: Missing or invalid token
- **Fix**: Re-login and use the fresh token

### 404 Not Found on Exam/Question
- **Cause**: Teacher doesn't own the subject for that exam
- **Fix**: Ensure the Subject has `teacher_id` set to your teacher's ID

### Password Changes
The seed script is idempotent - it won't update existing teachers. To change a password:
1. Delete the teacher from the database manually, or
2. Clear the database and re-seed

## Security Notes

- Default passwords are for development only
- Change passwords in production via admin UI or direct DB update
- Set `JWT_SECRET` environment variable in production
- Use strong bcrypt rounds (default is 10, increase to 12 for prod)
