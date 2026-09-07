"""
Postgres connection setup using SQLAlchemy.

DATABASE_URL comes from .env. For local dev with the docker-compose.yml
in this project, it should be:

    DATABASE_URL=postgresql://coach:coach_dev_password@localhost:5432/tactical_coach
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://coach:coach_dev_password@localhost:5432/tactical_coach",
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """Call once at startup to create tables if they don't exist yet."""
    Base.metadata.create_all(bind=engine)
