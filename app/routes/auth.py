from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import (
    clear_session_cookie, current_user, hash_password, login_throttle, set_session_cookie,
    validate_new_password, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


def user_out(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "student_number": u.student_number,
            "program": u.program, "year": u.year, "class_group": u.class_group,
            "must_change_password": u.must_change_password}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ident = body.email.strip().lower()
    key = f"{ident}|{request.client.host if request.client else ''}"
    login_throttle.check(key)
    # Students may log in with email or student number.
    user = db.scalar(select(User).where((User.email == ident) | (User.student_number == body.email.strip())))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        login_throttle.fail(key)
        raise HTTPException(401, "Email/número ou palavra-passe incorretos.")
    login_throttle.reset(key)
    set_session_cookie(response, user)
    return user_out(user)


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "A palavra-passe atual está errada.")
    validate_new_password(body.new_password)
    if body.new_password == body.current_password:
        raise HTTPException(400, "Escolhe uma palavra-passe diferente da atual.")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    db.commit()
    return {"ok": True}
