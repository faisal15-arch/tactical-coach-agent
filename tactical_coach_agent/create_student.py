"""Provision a student account from an administrator terminal."""

import argparse
import getpass

from core.auth import create_student
from core.db import SessionLocal, create_tables


def main():
    parser = argparse.ArgumentParser(description="Create a Tactical Coach student")
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Student password (minimum 10 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")

    create_tables()
    db = SessionLocal()
    try:
        student = create_student(db, args.student_id, args.name, password)
        print(f"Created student {student.student_id}: {student.full_name}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
