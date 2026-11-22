"""Вход, сессии, CSRF и ограничение попыток входа.

- Пароли: bcrypt (соль внутри хеша, 12 раундов). Открытых паролей нет ни в коде, ни в базе.
- Сессия: случайный токен в HttpOnly-cookie; в базе хранится только SHA-256 от токена,
  поэтому утечка базы не даёт войти чужой сессией.
- CSRF: у каждой сессии свой токен, он вшит скрытым полем в каждую форму.
- Перебор паролей: 5 неудачных попыток за 10 минут с одного адреса на один логин.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

import bcrypt
from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import models as m
from .services import now

COOKIE = "dk_session"
SESSION_TTL = timedelta(days=7)
_DUMMY_HASH = bcrypt.hashpw(b"timing-equalizer", bcrypt.gensalt(rounds=12))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate = password.encode()[:72]
    if not password_hash or not password_hash.startswith("$2"):
        bcrypt.checkpw(candidate, _DUMMY_HASH)  # одинаковое время ответа для несуществующего логина
        return False
    try:
        return bcrypt.checkpw(candidate, password_hash.encode())
    except ValueError:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: m.User) -> str:
    token = secrets.token_urlsafe(32)
    ts = now()
    db.execute(delete(m.AuthSession).where(m.AuthSession.expires_at < ts))
    db.add(m.AuthSession(token_hash=_token_hash(token), user_id=user.id,
                         csrf=secrets.token_urlsafe(24), created_at=ts,
                         expires_at=ts + SESSION_TTL))
    db.commit()
    return token


def load_session(db: Session, token: str | None):
    if not token or len(token) > 128:
        return None, None
    sess = db.scalar(select(m.AuthSession).where(m.AuthSession.token_hash == _token_hash(token)))
    if not sess or sess.expires_at < now():
        return None, None
    return db.get(m.User, sess.user_id), sess


def destroy_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(m.AuthSession).where(m.AuthSession.token_hash == _token_hash(token)))
        db.commit()


def is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


def set_session_cookie(response, token: str, request: Request) -> None:
    response.set_cookie(COOKIE, token, max_age=int(SESSION_TTL.total_seconds()), httponly=True,
                        samesite="lax", secure=is_https(request), path="/")


def client_ip(request: Request) -> str:
    # Берём последний адрес из X-Forwarded-For: его дописал прокси хостинга,
    # а первые значения клиент может подставить сам.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def check_csrf(request: Request, token: str | None) -> None:
    expected = getattr(request.state, "csrf", None)
    if not expected or not token or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="csrf")


class LoginLimiter:
    def __init__(self, attempts: int = 5, window: int = 600):
        self.attempts = attempts
        self.window = window
        self._fails: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str) -> deque:
        q = self._fails[key]
        t = time.monotonic()
        while q and t - q[0] > self.window:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        with self._lock:
            q = self._prune(key)
            if len(q) >= self.attempts:
                return int(self.window - (time.monotonic() - q[0])) + 1
            if not q:
                self._fails.pop(key, None)
            return 0

    def fail(self, key: str) -> None:
        with self._lock:
            self._prune(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


limiter = LoginLimiter()
