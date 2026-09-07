"""Password hashing and server-side student authentication sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from core.models import StudentAccount, StudentAuthSession


PASSWORD_ITERATIONS = 310_000


def normalize_student_id(student_id: str) -> str:
    return " ".join(student_id.strip().upper().split())


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "$".join((
        "pbkdf2_sha256",
        str(PASSWORD_ITERATIONS),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    ))


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def create_student(
    db: Session,
    student_id: str,
    full_name: str,
    password: str,
) -> StudentAccount:
    normalized_id = normalize_student_id(student_id)
    if len(normalized_id) < 3:
        raise ValueError("Student ID must contain at least 3 characters")
    if len(full_name.strip()) < 2:
        raise ValueError("Student name must contain at least 2 characters")
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters")
    if db.get(StudentAccount, normalized_id):
        raise ValueError("That student ID already exists")

    student = StudentAccount(
        student_id=normalized_id,
        full_name=full_name.strip(),
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def authenticate_student(
    db: Session,
    student_id: str,
    password: str,
) -> Optional[StudentAccount]:
    student = db.get(StudentAccount, normalize_student_id(student_id))
    if not student or not student.is_active:
        return None
    return student if verify_password(password, student.password_hash) else None


def create_auth_session(
    db: Session,
    student: StudentAccount,
    lifetime_hours: int = 12,
) -> tuple[str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=lifetime_hours)
    db.add(StudentAuthSession(
        token_hash=token_hash,
        student_id=student.student_id,
        expires_at=expires_at,
    ))
    db.commit()
    return raw_token, expires_at


def student_for_token(
    db: Session,
    raw_token: str | None,
) -> Optional[StudentAccount]:
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    auth_session = db.get(StudentAuthSession, token_hash)
    if not auth_session:
        return None

    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        db.delete(auth_session)
        db.commit()
        return None

    student = db.get(StudentAccount, auth_session.student_id)
    return student if student and student.is_active else None


def revoke_auth_session(db: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    auth_session = db.get(StudentAuthSession, token_hash)
    if auth_session:
        db.delete(auth_session)
        db.commit()
