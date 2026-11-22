"""Цифровой деканат — личный кабинет студента (концепт для конкурса).

Сервер отдаёт готовые HTML-страницы (FastAPI + Jinja2). Права проверяются
на сервере в каждом обработчике: студент получает только свои записи,
чужие — 404, как будто их нет.
"""
from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

import segno
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import models as m
from . import security as sec
from . import services as svc
from .db import Base, SessionLocal, engine
from .seed import seed_if_empty

# Типы файлов, которых нет в стандартной таблице Python на части систем
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/manifest+json", ".webmanifest")

log = logging.getLogger("dekanat")
BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if seed_if_empty(db):
            log.warning("База пустая — загружены демо-данные")
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters.update(svc.FILTERS)
templates.env.globals.update(
    STATUS_LABEL=svc.STATUS_LABEL, STATUS_TONE=svc.STATUS_TONE, CONTROL_LABEL=svc.CONTROL_LABEL,
    REQUEST_TYPES=svc.REQUEST_TYPES, DELIVERY_LABEL=svc.DELIVERY_LABEL, PAIRS=svc.PAIRS,
    WEEKDAYS=svc.WEEKDAYS, WEEKDAYS_SHORT=svc.WEEKDAYS_SHORT, PARITY_LABEL=svc.PARITY_LABEL,
    plural=svc.plural, parity=svc.parity, default_week=svc.default_week, SEMESTER_MAX=svc.SEMESTER_MAX, ADMISSION=svc.ADMISSION,
)

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; "
       "base-uri 'none'; object-src 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin != "null":
            hosts = {request.headers.get("host"), request.headers.get("x-forwarded-host")}
            if urlparse(origin).netloc not in hosts:
                return PlainTextResponse("Запрос с чужого сайта отклонён", status_code=403)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if sec.is_https(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if not request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------- зависимости

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> m.User:
    user, sess = sec.load_session(db, request.cookies.get(sec.COOKIE))
    if not user:
        raise LoginRequired()
    request.state.user = user
    request.state.csrf = sess.csrf
    return user


def student(user: m.User = Depends(current_user)) -> m.User:
    if user.role != "student":
        raise HTTPException(status_code=403)
    return user


def staff(user: m.User = Depends(current_user)) -> m.User:
    if user.role != "staff":
        raise HTTPException(status_code=403)
    return user


def unread_count(db: Session, user: m.User) -> int:
    return db.scalar(select(func.count()).select_from(m.Notification).where(
        m.Notification.user_id == user.id, m.Notification.read_at.is_(None))) or 0


def render(request: Request, name: str, db: Session | None = None, status_code: int = 200, **ctx):
    user = getattr(request.state, "user", None)
    base = dict(user=user, csrf=getattr(request.state, "csrf", ""), today=svc.today(),
                theme="dark" if request.cookies.get("theme") == "dark" else "light",
                now=svc.now(), path=request.url.path,
                unread=unread_count(db, user) if (db is not None and user is not None) else 0)
    base.update(ctx)
    return templates.TemplateResponse(request, name, base, status_code=status_code)


def safe_next(target: str | None) -> str:
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, _exc: LoginRequired):
    nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    target = "/login" if nxt == "/" else f"/login?next={quote(nxt)}"
    return RedirectResponse(target, status_code=303)


ERRORS = {
    400: ("Некорректный запрос", "Проверьте адрес страницы или вернитесь на главную."),
    403: ("Нет доступа", "Этот раздел недоступен для вашей учётной записи. Если вы отправляли форму — "
                         "обновите страницу и попробуйте ещё раз."),
    404: ("Страница не найдена", "Такой страницы нет, или у вас нет к ней доступа."),
    405: ("Так нельзя", "Это действие выполняется только из формы на сайте."),
}


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    title, text = ERRORS.get(exc.status_code, ("Что-то пошло не так", "Попробуйте обновить страницу."))
    return render(request, "error.html", code=exc.status_code, title=title, text=text,
                  status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, _exc: RequestValidationError):
    title, text = ERRORS[400]
    return render(request, "error.html", code=400, title=title, text=text, status_code=400)


@app.exception_handler(Exception)
async def _server_error(request: Request, exc: Exception):
    log.exception("Ошибка при обработке %s", request.url.path, exc_info=exc)
    return render(request, "error.html", code=500, title="Сервис временно не отвечает",
                  text="Мы уже знаем об ошибке. Обновите страницу через минуту.", status_code=500)


# ---------------------------------------------------------------- вход и выход

@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")


# Установка на телефон как приложение (PWA). Service worker отдаётся с корня сайта,
# чтобы его область действия была весь кабинет; кеширует он только статику.
@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest():
    return FileResponse(BASE_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(BASE_DIR / "static" / "sw.js", media_type="application/javascript")


@app.get("/login")
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    user, _ = sec.load_session(db, request.cookies.get(sec.COOKIE))
    if user:
        return RedirectResponse("/staff" if user.role == "staff" else "/", status_code=303)
    return render(request, "login.html", next=safe_next(next), error=None, login_value="")


@app.post("/login")
def login(request: Request, login: str = Form(""), password: str = Form(""),
          next: str = Form("/"), db: Session = Depends(get_db)):
    login_value = login.strip().lower()[:50]
    password = password[:128]
    key = f"{sec.client_ip(request)}|{login_value}"
    wait = sec.limiter.retry_after(key)
    if wait:
        minutes = max(1, round(wait / 60))
        return render(request, "login.html", next=safe_next(next), login_value=login_value,
                      error=f"Слишком много попыток. Попробуйте через {minutes} "
                            f"{svc.plural(minutes, 'минуту', 'минуты', 'минут')}.",
                      status_code=429)
    if not login_value or not password:
        return render(request, "login.html", next=safe_next(next), login_value=login_value,
                      error="Введите логин и пароль.", status_code=400)
    user = db.scalar(select(m.User).where(m.User.login == login_value))
    if not sec.verify_password(password, user.password_hash if user else None):
        sec.limiter.fail(key)
        return render(request, "login.html", next=safe_next(next), login_value=login_value,
                      error="Неверный логин или пароль.", status_code=401)
    sec.limiter.reset(key)
    token = sec.create_session(db, user)
    target = safe_next(next)
    if user.role == "staff" and target == "/":
        target = "/staff"
    response = RedirectResponse(target, status_code=303)
    sec.set_session_cookie(response, token, request)
    return response


@app.post("/logout")
def logout(request: Request, csrf: str = Form(""), user: m.User = Depends(current_user),
           db: Session = Depends(get_db)):
    sec.check_csrf(request, csrf)
    sec.destroy_session(db, request.cookies.get(sec.COOKIE))
    response = RedirectResponse("/login?bye=1", status_code=303)
    response.delete_cookie(sec.COOKIE, path="/")
    return response


# ---------------------------------------------------------------- главная

@app.get("/")
def dashboard(request: Request, user: m.User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role == "staff":
        return RedirectResponse("/staff", status_code=303)
    t = svc.today()
    lessons = svc.build_day(db, user.group_id, t)
    remaining = [e for e in lessons if e["active"] and e["state"] != "past"]
    next_day, next_lessons = (None, [])
    if not remaining:
        next_day, next_lessons = svc.next_study_day(db, user.group_id, t)
    grades = svc.grades_overview(db, user)
    nearest = min((r for r in grades["debts"] if r.retake_at and r.retake_at >= svc.now()),
                  key=lambda r: (r.retake_at, r.id), default=None)
    next_action = svc.retake_choices(db, user, grades["debts"])[nearest.id] if nearest else None
    deadlines = svc.upcoming(db, user, days=14)
    active_requests = db.scalars(
        select(m.StudentRequest).where(m.StudentRequest.student_id == user.id,
                                       m.StudentRequest.status.in_(svc.ACTIVE_STATUSES))
        .order_by(m.StudentRequest.updated_at.desc()).limit(3)).all()
    notes = db.scalars(select(m.Notification).where(m.Notification.user_id == user.id)
                       .order_by(m.Notification.created_at.desc()).limit(3)).all()
    week = svc.default_week(t)
    changes = svc.changes_between(db, user.group_id, week, week + timedelta(days=6))
    return render(request, "dashboard.html", db, lessons=lessons, has_remaining=bool(remaining),
                  next_day=next_day, next_lessons=next_lessons, grades=grades, nearest=nearest, next_action=next_action,
                  deadlines=deadlines[:4], deadlines_total=len(deadlines),
                  active_requests=active_requests, notes=notes, week_changes=changes)


# ---------------------------------------------------------------- расписание

@app.get("/schedule")
def schedule(request: Request, week: date | None = None, view: str = "week",
             user: m.User = Depends(student), db: Session = Depends(get_db)):
    t = svc.today()
    monday = svc.week_start(week) if week else svc.default_week(t)
    if abs((monday - t).days) > 400:
        raise HTTPException(status_code=400)
    view = "today" if view == "today" else "week"
    ctx = dict(view=view, monday=monday, prev_week=monday - timedelta(days=7),
               next_week=monday + timedelta(days=7), this_week=svc.week_start(t),
               following_week=svc.week_start(t) + timedelta(days=7),
               week_parity=svc.parity(monday))
    if view == "today":
        lessons = svc.build_day(db, user.group_id, t)
        nd, nl = (None, [])
        if not any(e["active"] for e in lessons):
            nd, nl = svc.next_study_day(db, user.group_id, t)
        ctx.update(lessons=lessons, next_day=nd, next_lessons=nl)
    else:
        days = svc.build_week(db, user.group_id, monday)
        # Считаем записи об изменениях, а не дни: перенос пары — одно изменение, хоть и виден в двух днях.
        total = svc.changes_between(db, user.group_id, monday, monday + timedelta(days=6))
        ctx.update(days=days, total_changes=total)
    return render(request, "schedule.html", db, **ctx)


@app.get("/schedule.ics")
def schedule_ics(user: m.User = Depends(student), db: Session = Depends(get_db)):
    """Экспорт пар на 4 недели вперёд в календарь телефона (формат iCalendar)."""
    t = svc.today()
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Digital Dekanat Concept//RU",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:Расписание (концепт)", "X-WR-TIMEZONE:Europe/Moscow"]

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    for i in range(28):
        day = t + timedelta(days=i)
        for e in svc.build_day(db, user.group_id, day):
            if not e["active"]:
                continue
            start = datetime.combine(day, datetime.strptime(e["start"], "%H:%M").time())
            end = datetime.combine(day, datetime.strptime(e["end"], "%H:%M").time())
            lines += ["BEGIN:VEVENT", f"UID:{user.id}-{day:%Y%m%d}-{e['pair']}@dekanat-concept",
                      f"DTSTAMP:{stamp}",
                      f"DTSTART;TZID=Europe/Moscow:{start:%Y%m%dT%H%M%S}",
                      f"DTEND;TZID=Europe/Moscow:{end:%Y%m%dT%H%M%S}",
                      f"SUMMARY:{esc(e['subject'])} ({esc(e['kind'])})",
                      f"LOCATION:{esc(e['room'])}",
                      f"DESCRIPTION:{esc(e['teacher'] + ('. ' + e['note'] if e['note'] else ''))}",
                      "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="schedule.ics"'})


# ---------------------------------------------------------------- успеваемость

@app.get("/grades")
def grades(request: Request, user: m.User = Depends(student), db: Session = Depends(get_db)):
    overview = svc.grades_overview(db, user)
    return render(request, "grades.html", db, grades=overview,
                  retakes=svc.retake_choices(db, user, overview["debts"]),
                  exams=svc.session_exams(db, user))


# ---------------------------------------------------------------- заявки

def own_request(db: Session, user: m.User, request_id: int) -> m.StudentRequest:
    """Заявка, только если она принадлежит пользователю (или он сотрудник). Иначе 404."""
    req = db.get(m.StudentRequest, request_id)
    if not req or (user.role != "staff" and req.student_id != user.id):
        raise HTTPException(status_code=404)
    return req


@app.get("/requests")
def requests_list(request: Request, user: m.User = Depends(student), db: Session = Depends(get_db)):
    rows = db.scalars(select(m.StudentRequest).where(m.StudentRequest.student_id == user.id)
                      .order_by(m.StudentRequest.created_at.desc())).all()
    active = [r for r in rows if r.status in svc.ACTIVE_STATUSES]
    archive = [r for r in rows if r.status not in svc.ACTIVE_STATUSES]
    return render(request, "requests.html", db, active=active, archive=archive,
                  ok=request.query_params.get("ok"))


def debt_context(db, user, type_code, debt_id):
    if debt_id is None:
        return None, None
    debts = db.scalars(select(m.PastResult).where(m.PastResult.student_id == user.id,
                                                  m.PastResult.debt.is_(True))).all()
    debt = next((item for item in debts if item.id == debt_id), None)
    if type_code != "retake" or debt is None:
        raise HTTPException(status_code=404)
    action, existing = svc.retake_choices(db, user, debts)[debt_id]
    if action == "existing":
        return debt, RedirectResponse(f"/requests/{existing.id}", status_code=303)
    if action == "list":
        return debt, RedirectResponse("/requests", status_code=303)
    return debt, None


@app.get("/requests/new")
def request_new(request: Request, type: str | None = None, debt_id: int | None = None, user: m.User = Depends(student),
                db: Session = Depends(get_db)):
    if type and type not in svc.REQUEST_TYPES:
        raise HTTPException(status_code=404)
    debt, redirect = debt_context(db, user, type, debt_id)
    if redirect:
        return redirect
    return render(request, "request_new.html", db, type_code=type, debt=debt,
                  spec=svc.REQUEST_TYPES.get(type) if type else None, errors={}, form={"purpose": debt.course if debt else ""})


@app.post("/requests/new")
def request_create(request: Request, debt_id: int | None = Form(None), type_code: str = Form(...), purpose: str = Form(""),
                   copies: int = Form(1), delivery: str = Form("electronic"), comment: str = Form(""),
                   csrf: str = Form(""), user: m.User = Depends(student), db: Session = Depends(get_db)):
    sec.check_csrf(request, csrf)
    spec = svc.REQUEST_TYPES.get(type_code)
    if not spec:
        raise HTTPException(status_code=400)
    debt, redirect = debt_context(db, user, type_code, debt_id)
    if redirect:
        return redirect
    purpose, comment = (debt.course if debt else purpose.strip()), comment.strip()
    errors = {}
    if spec["purpose_required"] and not purpose:
        errors["purpose"] = f"Заполните поле «{spec['purpose_label']}»."
    if len(purpose) > 300:
        errors["purpose"] = "Не больше 300 символов."
    if len(comment) > 1000:
        errors["comment"] = "Не больше 1000 символов."
    if spec["cat"] == "cert" and not 1 <= copies <= 5:
        errors["copies"] = "От 1 до 5 экземпляров."
    if delivery not in svc.DELIVERY_LABEL or (delivery == "electronic" and not spec["electronic"]):
        delivery = "paper"
    if errors:
        return render(request, "request_new.html", db, type_code=type_code, spec=spec, errors=errors, debt=debt,
                      form=dict(purpose=purpose, copies=copies, delivery=delivery, comment=comment),
                      status_code=422)
    ts = svc.now()
    req = m.StudentRequest(student_id=user.id, type_code=type_code, title=spec["title"],
                           purpose=purpose, copies=copies if spec["cat"] == "cert" else 1,
                           delivery=delivery, comment=comment, status="submitted",
                           created_at=ts, updated_at=ts)
    req.events.append(m.RequestEvent(status="submitted", actor=user.full_name, created_at=ts))
    db.add(req)
    if debt is not None:
        db.flush()
        # Заменяем только привязку закрытой без выдачи заявки.
        inactive = select(m.StudentRequest.id).where(
            m.StudentRequest.student_id == user.id,
            m.StudentRequest.status.not_in(svc.RETAKE_CURRENT_STATUSES))
        db.execute(delete(m.RetakeDirection).where(
            m.RetakeDirection.debt_id == debt.id, m.RetakeDirection.request_id.in_(inactive)))
        db.add(m.RetakeDirection(debt_id=debt.id, request_id=req.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if debt_id is not None:
            _, redirect = debt_context(db, user, type_code, debt_id)
            if redirect:
                return redirect
        raise
    return RedirectResponse(f"/requests/{req.id}?ok=created", status_code=303)


@app.get("/requests/{request_id}")
def request_detail(request: Request, request_id: int, user: m.User = Depends(student),
                   db: Session = Depends(get_db)):
    req = own_request(db, user, request_id)
    return render(request, "request_detail.html", db, req=req, spec=svc.REQUEST_TYPES.get(req.type_code),
                  ok=request.query_params.get("ok"))


def change_request_status(db: Session, req: m.StudentRequest, new_status: str, ts: datetime,
                          event_id: int | None = None) -> bool:
    # Проверяем состояние и в момент записи: два одновременных ответа не создают два события.
    statement = update(m.StudentRequest).where(
        m.StudentRequest.id == req.id, m.StudentRequest.status == req.status,
        m.StudentRequest.updated_at == req.updated_at)
    if event_id is not None:
        latest = select(func.max(m.RequestEvent.id)).where(m.RequestEvent.request_id == req.id).scalar_subquery()
        statement = statement.where(latest == event_id)
    result = db.execute(statement.values(status=new_status, updated_at=ts))
    return result.rowcount == 1


@app.post("/requests/{request_id}/reply")
def request_reply(request: Request, request_id: int, question_id: int = Form(...), reply: str = Form(""),
                  csrf: str = Form(""), user: m.User = Depends(student), db: Session = Depends(get_db)):
    sec.check_csrf(request, csrf)
    req = own_request(db, user, request_id)
    path = f"/requests/{req.id}"
    if req.status != "clarify" or not req.events or req.events[-1].id != question_id:
        return RedirectResponse(path + "?ok=changed", status_code=303)
    reply = reply.strip()
    if not 1 <= len(reply) <= 1000:
        return render(request, "request_detail.html", db, req=req, spec=svc.REQUEST_TYPES.get(req.type_code),
                      error="Введите ответ от 1 до 1000 символов.", reply=reply, status_code=422)
    ts = svc.now()
    if not change_request_status(db, req, "in_progress", ts, question_id):
        return RedirectResponse(path + "?ok=changed", status_code=303)
    req.staff_comment = ""  # Вопрос остаётся в истории, но уже не требует ответа.
    req.events.append(m.RequestEvent(status="in_progress", comment=reply, actor=user.full_name, created_at=ts))
    db.commit()
    return RedirectResponse(path + "?ok=replied", status_code=303)


@app.post("/requests/{request_id}/cancel")
def request_cancel(request: Request, request_id: int, csrf: str = Form(""),
                   user: m.User = Depends(student), db: Session = Depends(get_db)):
    sec.check_csrf(request, csrf)
    req = own_request(db, user, request_id)
    if req.status != "submitted":
        return RedirectResponse(f"/requests/{req.id}?ok=locked", status_code=303)
    ts = svc.now()
    if not change_request_status(db, req, "cancelled", ts):
        return RedirectResponse(f"/requests/{req.id}?ok=locked", status_code=303)
    req.events.append(m.RequestEvent(status="cancelled", actor=user.full_name, created_at=ts))
    db.commit()
    return RedirectResponse(f"/requests/{req.id}?ok=cancelled", status_code=303)


@app.get("/requests/{request_id}/document")
def request_document(request: Request, request_id: int, user: m.User = Depends(current_user),
                     db: Session = Depends(get_db)):
    req = own_request(db, user, request_id)
    if req.status not in ("ready", "done") or req.delivery != "electronic":
        raise HTTPException(status_code=404)
    check = svc.document_check(db, req)
    host = request.headers.get("host", "")
    scheme = "http" if host.split(":")[0] in ("localhost", "127.0.0.1", "testserver") else "https"
    check_url = f"{scheme}://{host}/check/{check.token}"
    qr = segno.make(check_url, error="m").svg_inline(scale=3, border=0, dark="#111111")
    return render(request, "request_document.html", db, req=req, s=req.student, check=check, check_url=check_url, qr=qr)


@app.get("/check/{token}")
def document_check_page(request: Request, token: str, db: Session = Depends(get_db)):
    """Публичная проверка подлинности справки по QR-коду. Вход не нужен, данных — минимум."""
    check = db.scalar(select(m.DocumentCheck).where(m.DocumentCheck.token == token)) if len(token) <= 40 else None
    if check is None:
        raise HTTPException(status_code=404)
    req = db.get(m.StudentRequest, check.request_id)
    valid = req.status in ("ready", "done") and req.delivery == "electronic"
    return render(request, "check.html", req=req, valid=valid, holder=svc.short_name(req.student.full_name),
                  faculty=req.student.group.program.faculty.name)


# ---------------------------------------------------------------- уведомления

NOTE_KINDS = {"news": "Деканат", "schedule": "Расписание", "request": "Заявки", "deadline": "Дедлайны"}


@app.get("/notifications")
def notifications(request: Request, kind: str = "all", user: m.User = Depends(current_user),
                  db: Session = Depends(get_db)):
    q = select(m.Notification).where(m.Notification.user_id == user.id)
    if kind in NOTE_KINDS:
        q = q.where(m.Notification.kind == kind)
    else:
        kind = "all"
    rows = db.scalars(q.order_by(m.Notification.created_at.desc()).limit(100)).all()
    deadlines = svc.upcoming(db, user, days=21) if user.role == "student" else []
    return render(request, "notifications.html", db, rows=rows, kind=kind, kinds=NOTE_KINDS,
                  deadlines=deadlines)


@app.post("/notifications/read-all")
def notifications_read_all(request: Request, csrf: str = Form(""), user: m.User = Depends(current_user),
                           db: Session = Depends(get_db)):
    sec.check_csrf(request, csrf)
    db.execute(update(m.Notification).where(m.Notification.user_id == user.id,
                                            m.Notification.read_at.is_(None)).values(read_at=svc.now()))
    db.commit()
    return RedirectResponse("/notifications", status_code=303)


@app.get("/notifications/{note_id}")
def notification_open(note_id: int, user: m.User = Depends(current_user), db: Session = Depends(get_db)):
    note = db.get(m.Notification, note_id)
    if not note or note.user_id != user.id:
        raise HTTPException(status_code=404)
    if note.read_at is None:
        note.read_at = svc.now()
        db.commit()
    return RedirectResponse(safe_next(svc.notification_link(db, note, user)) if note.link else "/notifications", status_code=303)


# ---------------------------------------------------------------- профиль

@app.get("/profile")
def profile(request: Request, user: m.User = Depends(current_user), db: Session = Depends(get_db)):
    group = user.group
    program = group.program if group else None
    faculty = program.faculty if program else None
    sessions = db.scalar(select(func.count()).select_from(m.AuthSession).where(
        m.AuthSession.user_id == user.id, m.AuthSession.expires_at > svc.now())) or 0
    return render(request, "profile.html", db, group=group, program=program, faculty=faculty,
                  sessions=sessions)


@app.post("/profile/logout-all")
def logout_all(request: Request, csrf: str = Form(""), user: m.User = Depends(current_user),
               db: Session = Depends(get_db)):
    sec.check_csrf(request, csrf)
    from sqlalchemy import delete
    db.execute(delete(m.AuthSession).where(m.AuthSession.user_id == user.id))
    db.commit()
    response = RedirectResponse("/login?bye=1", status_code=303)
    response.delete_cookie(sec.COOKIE, path="/")
    return response


# ---------------------------------------------------------------- кабинет сотрудника

@app.get("/staff")
def staff_queue(request: Request, status: str = "open", q: str = "", user: m.User = Depends(staff),
                db: Session = Depends(get_db)):
    query = select(m.StudentRequest).join(m.User, m.StudentRequest.student_id == m.User.id)
    if status == "open":
        query = query.where(m.StudentRequest.status.in_(("submitted", "in_progress")))
    elif status in svc.STATUS_LABEL:
        query = query.where(m.StudentRequest.status == status)
    else:
        status = "all"
    q = q.strip()[:60]
    if q:
        query = query.where(m.User.full_name.ilike(f"%{q}%") | m.StudentRequest.title.ilike(f"%{q}%"))
    rows = db.scalars(query.order_by(m.StudentRequest.updated_at.desc(), m.StudentRequest.id.desc())).all()
    counts = dict(db.execute(select(m.StudentRequest.status, func.count())
                             .group_by(m.StudentRequest.status)).all())
    return render(request, "staff_queue.html", db, rows=rows, status=status, q=q, counts=counts)


@app.get("/staff/requests/{request_id}")
def staff_request(request: Request, request_id: int, user: m.User = Depends(staff),
                  db: Session = Depends(get_db)):
    req = own_request(db, user, request_id)
    return render(request, "staff_request.html", db, req=req, spec=svc.REQUEST_TYPES.get(req.type_code),
                  options=svc.STAFF_TRANSITIONS.get(req.status, []), ok=request.query_params.get("ok"),
                  error=None)


@app.post("/staff/requests/{request_id}")
def staff_request_update(request: Request, request_id: int, new_status: str = Form(...),
                         comment: str = Form(""), csrf: str = Form(""), event_id: int | None = Form(None),
                         user: m.User = Depends(staff),
                         db: Session = Depends(get_db)):
    sec.check_csrf(request, csrf)
    req = own_request(db, user, request_id)
    comment = comment.strip()[:1000]
    options = svc.STAFF_TRANSITIONS.get(req.status, [])
    error = None
    if new_status not in options:
        error = "Этот статус сейчас поставить нельзя."
    elif new_status == "clarify" and not comment:
        error = "Напишите, что студенту нужно уточнить."
    elif new_status == "rejected" and not comment:
        error = "При отказе напишите причину — студент увидит её в заявке."
    if error:
        return render(request, "staff_request.html", db, req=req, spec=svc.REQUEST_TYPES.get(req.type_code),
                      options=options, ok=None, error=error, status_code=422)
    ts = svc.now()
    if not change_request_status(db, req, new_status, ts, event_id):
        return RedirectResponse(f"/staff/requests/{req.id}?ok=changed", status_code=303)
    if comment:
        req.staff_comment = comment
    req.events.append(m.RequestEvent(status=new_status, comment=comment, actor=user.full_name, created_at=ts))
    titles = {"clarify": f"Уточните данные: {req.title}",
              "in_progress": f"Заявка «{req.title}» взята в работу",
              "ready": f"Готово: {req.title}",
              "done": f"Заявка «{req.title}» завершена",
              "rejected": f"Заявка «{req.title}» отклонена"}
    svc.add_notification(db, req.student_id, "request", titles[new_status], comment,
                         f"/requests/{req.id}", important=new_status in ("ready", "rejected", "clarify"))
    db.commit()
    return RedirectResponse(f"/staff/requests/{req.id}?ok=updated", status_code=303)


@app.get("/staff/news")
def staff_news(request: Request, user: m.User = Depends(staff), db: Session = Depends(get_db)):
    groups = db.scalars(select(m.Group).order_by(m.Group.name)).all()
    return render(request, "staff_news.html", db, groups=groups, errors={}, form={},
                  ok=request.query_params.get("ok"))


@app.post("/staff/news")
def staff_news_send(request: Request, audience: str = Form("all"), title: str = Form(""),
                    body: str = Form(""), important: str = Form(""), csrf: str = Form(""),
                    user: m.User = Depends(staff), db: Session = Depends(get_db)):
    sec.check_csrf(request, csrf)
    title, body = title.strip(), body.strip()
    groups = db.scalars(select(m.Group).order_by(m.Group.name)).all()
    errors = {}
    if not title:
        errors["title"] = "Добавьте заголовок."
    if len(title) > 150:
        errors["title"] = "Не больше 150 символов."
    if len(body) > 2000:
        errors["body"] = "Не больше 2000 символов."
    q = select(m.User).where(m.User.role == "student")
    if audience != "all":
        try:
            gid = int(audience)
        except ValueError:
            gid = -1
        if gid not in {g.id for g in groups}:
            errors["audience"] = "Выберите группу из списка."
        q = q.where(m.User.group_id == gid)
    if errors:
        return render(request, "staff_news.html", db, groups=groups, errors=errors,
                      form=dict(audience=audience, title=title, body=body, important=important),
                      ok=None, status_code=422)
    recipients = db.scalars(q).all()
    for r in recipients:
        svc.add_notification(db, r.id, "news", title, body, "", important=bool(important))
    db.commit()
    return RedirectResponse(f"/staff/news?ok={len(recipients)}", status_code=303)
