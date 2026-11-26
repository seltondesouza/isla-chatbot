"""Passwords, session cookies and role checks."""

import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db, secret_key
from .models import User

COOKIE_NAME = "isla_session"
_ALGO = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def generate_password() -> str:
    """Readable temporary password, e.g. 'kq4m-7zpa-x2nd'."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


def validate_new_password(password: str) -> None:
    if len(password) < 10:
        raise HTTPException(400, "A palavra-passe deve ter pelo menos 10 caracteres.")


def set_session_cookie(response: Response, user: User) -> None:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(hours=s.session_hours)
    token = jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp}, secret_key(), algorithm=_ALGO)
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="lax", secure=s.cookie_secure or s.on_vercel,
        max_age=s.session_hours * 3600, path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão não iniciada.")
    try:
        payload = jwt.decode(token, secret_key(), algorithms=[_ALGO])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão expirada. Entra novamente.")
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Conta inativa.")
    return user


def require_staff(user: User = Depends(current_user)) -> User:
    if user.role not in ("staff", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem permissão.")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem permissão.")
    return user


class LoginThrottle:
    """Blocks an email+IP pair for 15 minutes after 8 failed logins."""

    MAX_FAILS, WINDOW = 8, 15 * 60

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = defaultdict(list)

    def _recent(self, key: str) -> list[float]:
        now = time.monotonic()
        self._fails[key] = [t for t in self._fails[key] if now - t < self.WINDOW]
        return self._fails[key]

    def check(self, key: str) -> None:
        if len(self._recent(key)) >= self.MAX_FAILS:
            raise HTTPException(429, "Demasiadas tentativas. Tenta novamente daqui a 15 minutos.")

    def fail(self, key: str) -> None:
        self._recent(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


login_throttle = LoginThrottle()
