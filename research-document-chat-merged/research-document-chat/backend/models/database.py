"""
Database Engine & Session Management for Standalone Research Document Chat
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models.models import Base, User

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./research_chat.db")

# SQLite thread support
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables and seed default local development user if empty."""
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    try:
        dev_user = db.query(User).filter_by(username="researcher").first()
        if not dev_user:
            dev_user = User(
                user_id="usr_default_researcher",
                username="researcher",
                email="researcher@example.com",
                role="contributor"
            )
            db.add(dev_user)
            db.commit()
    finally:
        db.close()


def get_db():
    """FastAPI dependency for database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
