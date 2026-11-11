"""
FastAPI Security & Authentication Dependencies
Provides user context for standalone research chat.
Supports optional JWT Bearer token or defaults to the local developer user.
"""
from typing import Optional
import os
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from models.models import User
from models.database import get_db
from routes.api_principal import ApiPrincipal, resolve_api_principal

DEFAULT_DEV_USER_ID = "usr_default_researcher"


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    principal: ApiPrincipal = Depends(resolve_api_principal),
) -> User:
    """
    Legacy User model for research-chat routes.
    Portal Bearer sessions map to a synthetic User row keyed by portal user_id.
    Dev fallback only when RESEARCH_CHAT_ALLOW_DEV_AUTH=true and no Bearer token.
    """
    if principal.source == "portal":
        user = db.query(User).filter_by(user_id=principal.user_id).first()
        if not user:
            user = User(
                user_id=principal.user_id,
                username=principal.user_id,
                email=f"{principal.user_id}@portal.local",
                role=principal.role if principal.role in {"admin", "doctor", "msl"} else "doctor",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user
    if os.getenv("RESEARCH_CHAT_ALLOW_DEV_AUTH", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=401,
            detail="Development authentication is disabled. Use Authorization: Bearer <session_id>.",
        )
    user = db.query(User).filter_by(user_id=DEFAULT_DEV_USER_ID).first()
    if not user:
        user = User(
            user_id=DEFAULT_DEV_USER_ID,
            username="researcher",
            email="researcher@example.com",
            role="super_admin",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_optional_user(
    db: Session = Depends(get_db),
    principal: ApiPrincipal = Depends(resolve_api_principal),
) -> Optional[User]:
    return get_current_user(db=db, principal=principal)
