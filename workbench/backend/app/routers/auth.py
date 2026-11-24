import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ALLOW_REGISTER
from ..deps import get_current_user, get_db
from ..models import User
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterIn(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: str
    password: str


def _user_out(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


@router.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if not ALLOW_REGISTER:
        raise HTTPException(status_code=403, detail="当前未开放注册，请联系管理员开通账号")
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    exists = db.scalar(select(User).where(User.email == email))
    if exists:
        raise HTTPException(status_code=409, detail="该邮箱已注册，请直接登录")
    user = User(
        email=email,
        name=body.name.strip(),
        password_hash=hash_password(body.password),
        role="member",
    )
    db.add(user)
    db.commit()
    return {"token": create_token(user.id), "user": _user_out(user)}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    return {"token": create_token(user.id), "user": _user_out(user)}


@router.get("/config")
def auth_config():
    """登录页需要的公开配置：是否开放注册。"""
    return {"allow_register": ALLOW_REGISTER}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_out(user)
