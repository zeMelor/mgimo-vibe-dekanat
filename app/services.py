"""Бизнес-логика кабинета: время, расписание, успеваемость, дедлайны, справочники заявок.

Все функции, которые читают личные данные, принимают пользователя и
фильтруют выборку по нему — обработчики не собирают такие запросы сами.
"""
from __future__ import annotations

import re
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import models as m

MSK = ZoneInfo("Europe/Moscow")


def now() -> datetime:
    return datetime.now(MSK).replace(tzinfo=None, microsecond=0)


def today() -> date:
    return now().date()


# ---------------------------------------------------------------- форматирование

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    n1 = n % 10
    if n1 == 1:
        return one
    if 2 <= n1 <= 4:
        return few
    return many


def ru_date(d: date | datetime | None, weekday: bool = False, short: bool = False) -> str:
    if d is None:
        return ""
    s = f"{d.day} {MONTHS[d.month - 1]}"
    if d.year != today().year:
        s += f" {d.year}"
    if weekday:
        wd = WEEKDAYS_SHORT[d.weekday()] if short else WEEKDAYS[d.weekday()]
        s = f"{wd}, {s}"
    return s


MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def ru_month_short(d: date | datetime | None) -> str:
    """Короткий месяц для плитки с датой: «янв», «дек» — помещается в 46 px при любой ширине шрифта."""
    return MONTHS_SHORT[d.month - 1] if d else ""


def ru_datetime(dt: datetime | None) -> str:
    if dt is None:
        return ""
    delta = (dt.date() - today()).days
    day = {0: "сегодня", -1: "вчера", 1: "завтра"}.get(delta) or ru_date(dt)
    return f"{day}, {dt:%H:%M}"


def rel_day(d: date | datetime | None) -> str:
    if d is None:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    n = (d - today()).days
    if n == 0:
        return "сегодня"
    if n == 1:
        return "завтра"
    if n == 2:
        return "послезавтра"
    if n > 2:
        return f"через {n} {plural(n, 'день', 'дня', 'дней')}"
    if n == -1:
        return "вчера"
    return f"{-n} {plural(-n, 'день', 'дня', 'дней')} назад"


def days_left(d: date | datetime) -> int:
    if isinstance(d, datetime):
        d = d.date()
    return (d - today()).days


def short_name(full_name: str) -> str:
    """«Ласточкин Тимур Ринатович» → «Ласточкин Т. Р.»: на публичной странице проверки — минимум данных."""
    parts = full_name.split()
    return " ".join(parts[:1] + [p[0] + "." for p in parts[1:]])


def document_check(db: Session, req: m.StudentRequest) -> m.DocumentCheck:
    """Ссылка для QR-кода. Создаётся при первом открытии справки, дальше не меняется."""
    check = db.get(m.DocumentCheck, req.id)
    if check is None:
        check = m.DocumentCheck(request_id=req.id, token=secrets.token_urlsafe(16))
        db.add(check)
        try:
            db.commit()
        except Exception:
            db.rollback()
            check = db.get(m.DocumentCheck, req.id)
    return check


def width_class(part: float, total: float) -> str:
    """Ширина полосы прогресса классом w-0…w-100 (шаг 5) — без inline-стилей, ради строгой CSP."""
    if not total:
        return "w-0"
    pct = max(0.0, min(1.0, part / total)) * 100
    return f"w-{int(round(pct / 5) * 5)}"


def decimal(x) -> str:
    """4.62 → «4,62»: в русском тексте дробная часть отделяется запятой."""
    return str(x).replace(".", ",")


def duration(mins: int) -> str:
    """45 → «45 мин», 80 → «1 ч 20 мин», 120 → «2 ч»."""
    h, m = divmod(max(0, int(mins)), 60)
    return " ".join(p for p in (f"{h} ч" if h else "", f"{m} мин" if m or not h else "") if p)


FILTERS = {
    "duration": duration,
    "decimal": decimal,
    "month_short": ru_month_short,
    "ru_date": ru_date,
    "ru_datetime": ru_datetime,
    "rel_day": rel_day,
    "days_left": days_left,
}

# ---------------------------------------------------------------- расписание

PAIRS = {
    1: ("09:00", "10:20"),
    2: ("10:30", "11:50"),
    3: ("12:40", "14:00"),
    4: ("14:10", "15:30"),
    5: ("15:40", "17:00"),
    6: ("17:10", "18:30"),
}


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def default_week(d: date) -> date:
    """В воскресенье неделя уже закончилась — показываем следующую."""
    return week_start(d + timedelta(days=1)) if d.weekday() == 6 else week_start(d)


def parity(d: date) -> str:
    return "odd" if d.isocalendar().week % 2 else "even"


PARITY_LABEL = {"odd": "числитель", "even": "знаменатель"}


def _pair_dt(day: date, pair: int, idx: int) -> datetime:
    hh, mm = PAIRS[pair][idx].split(":")
    return datetime(day.year, day.month, day.day, int(hh), int(mm))


def build_day(db: Session, group_id: int | None, day: date) -> list[dict]:
    """Пары группы на дату с учётом изменений. Возвращает список словарей для шаблона."""
    if not group_id:
        return []
    p = parity(day)
    items = db.scalars(
        select(m.ScheduleItem).where(
            m.ScheduleItem.group_id == group_id, m.ScheduleItem.weekday == day.weekday()
        )
    ).all()
    items = [i for i in items if i.parity in ("all", p)]
    changes = db.scalars(
        select(m.ScheduleChange).where(
            m.ScheduleChange.group_id == group_id,
            or_(m.ScheduleChange.date == day, m.ScheduleChange.new_date == day),
        )
    ).all()
    by_pair = {c.pair: c for c in changes if c.date == day and c.kind != "extra"}

    out: list[dict] = []
    for it in items:
        e = dict(pair=it.pair, subject=it.subject, kind=it.kind, room=it.room,
                 teacher=it.teacher, status="normal", note="", old_room="", old_teacher="",
                 moved_to=None, moved_from=None)
        c = by_pair.get(it.pair)
        if c:
            e["note"] = c.note
            if c.kind == "cancel":
                e["status"] = "cancelled"
            elif c.kind == "room":
                e.update(status="changed", old_room=it.room, room=c.new_room)
            elif c.kind == "teacher":
                e.update(status="changed", old_teacher=it.teacher, teacher=c.new_teacher)
            elif c.kind == "moved":
                e.update(status="moved_out", moved_to=(c.new_date, c.new_pair))
        out.append(e)

    for c in changes:
        if c.kind == "extra" and c.date == day:
            out.append(dict(pair=c.pair, subject=c.subject, kind=c.lesson_kind or "Занятие",
                            room=c.new_room, teacher=c.new_teacher, status="extra", note=c.note,
                            old_room="", old_teacher="", moved_to=None, moved_from=None))
        if c.kind == "moved" and c.new_date == day:
            src = db.scalars(
                select(m.ScheduleItem).where(
                    m.ScheduleItem.group_id == group_id,
                    m.ScheduleItem.weekday == c.date.weekday(),
                    m.ScheduleItem.pair == c.pair,
                    m.ScheduleItem.parity.in_(("all", parity(c.date))),
                )
            ).first()
            if src:
                out.append(dict(pair=c.new_pair, subject=src.subject, kind=src.kind,
                                room=c.new_room or src.room, teacher=src.teacher,
                                status="moved_in", change_id=c.id, note=c.note, old_room="", old_teacher="",
                                moved_to=None, moved_from=(c.date, c.pair)))

    order = {"cancelled": 1, "moved_out": 1}
    out.sort(key=lambda e: (e["pair"], order.get(e["status"], 0)))

    current = now()
    next_marked = False
    for e in out:
        e["start"], e["end"] = PAIRS[e["pair"]]
        e["active"] = e["status"] not in ("cancelled", "moved_out")
        e["state"] = ""
        e["mins"] = None   # для «идёт ещё N мин» и «через N мин»
        if day == current.date() and e["active"]:
            start, end = _pair_dt(day, e["pair"], 0), _pair_dt(day, e["pair"], 1)
            if start <= current <= end:
                e["state"] = "now"
                e["mins"] = -(-int((end - current).total_seconds()) // 60)
            elif current > end:
                e["state"] = "past"
            elif not next_marked:
                e["state"] = "next"
                e["mins"] = -(-int((start - current).total_seconds()) // 60)
                next_marked = True
    return out


def build_week(db: Session, group_id: int | None, monday: date) -> list[dict]:
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        lessons = build_day(db, group_id, d)
        if i == 6 and not lessons:
            continue  # воскресенье без пар не показываем
        days.append(dict(date=d, lessons=lessons,
                         changes=sum(1 for e in lessons if e["status"] != "normal")))
    return days


def moved_lesson_link(day: date, change_id: int) -> str:
    return f"/schedule?week={week_start(day)}#move-{change_id}"


def notification_link(db: Session, note: m.Notification, user: m.User) -> str:
    """У старых уведомлений была лишь ссылка на неделю. Уточняем только точное совпадение."""
    if note.kind != "schedule" or not note.title.startswith("Перенос: ") or not user.group_id:
        return note.link
    week = re.fullmatch(r"/schedule\?week=(\d{4}-\d{2}-\d{2})", note.link or "")
    body = re.fullmatch(r"Пара (\d\d\.\d\d) переносится на (?:[а-яё]+ )?(\d\d\.\d\d), (\d+) пара\.", note.body)
    if not week or not body:
        return note.link
    try:
        monday = date.fromisoformat(week[1])
    except ValueError:
        return note.link
    candidates = db.scalars(select(m.ScheduleChange).where(
        m.ScheduleChange.group_id == user.group_id, m.ScheduleChange.kind == "moved",
        m.ScheduleChange.date.between(monday, monday + timedelta(days=6)),
        m.ScheduleChange.new_pair == int(body[3]))).all()
    matches = []
    for change in candidates:
        if not change.new_date or change.date.strftime("%d.%m") != body[1] or change.new_date.strftime("%d.%m") != body[2]:
            continue
        items = db.scalars(select(m.ScheduleItem).where(
            m.ScheduleItem.group_id == user.group_id, m.ScheduleItem.weekday == change.date.weekday(),
            m.ScheduleItem.pair == change.pair, m.ScheduleItem.parity.in_(("all", parity(change.date))))).all()
        if len(items) == 1 and items[0].subject == note.title.removeprefix("Перенос: "):
            matches.append(change)
    return moved_lesson_link(matches[0].new_date, matches[0].id) if len(matches) == 1 else note.link


def next_study_day(db: Session, group_id: int | None, after: date, horizon: int = 10):
    for i in range(1, horizon + 1):
        d = after + timedelta(days=i)
        lessons = build_day(db, group_id, d)
        if any(e["active"] for e in lessons):
            return d, lessons
    return None, []


def changes_between(db: Session, group_id: int | None, start: date, end: date) -> int:
    if not group_id:
        return 0
    rows = db.scalars(
        select(m.ScheduleChange).where(
            m.ScheduleChange.group_id == group_id,
            m.ScheduleChange.date >= start,
            m.ScheduleChange.date <= end,
        )
    ).all()
    return len(rows)


# ---------------------------------------------------------------- успеваемость

SEMESTER_MAX = 60      # баллы текущего контроля за семестр
ADMISSION = 30         # минимум для допуска к экзамену или зачёту
CONTROL_LABEL = {"exam": "Экзамен", "credit": "Зачёт", "diff": "Дифф. зачёт", "consult": "Консультация"}
GRADE_POINTS = {"отлично": 5, "хорошо": 4, "удовл.": 3, "неуд.": 2}


def course_overview(course: m.Course, items: list[m.Assessment]) -> dict:
    graded = [a for a in items if a.points is not None]
    earned = sum(a.points for a in graded)
    so_far = sum(a.max_points for a in graded)
    total = sum(a.max_points for a in items) or SEMESTER_MAX
    remaining = sum(a.max_points for a in items if a.points is None)
    pct = earned / so_far if so_far else None
    need = max(0, ADMISSION - earned)
    if pct is None:
        status, label, advice = "none", "Нет оценок", "Оценок пока нет — первые баллы появятся после контрольной точки."
    elif need > remaining:
        status, label, advice = "critical", "Недопуск", (
            f"До допуска не хватает {need} б., а набрать можно только {remaining}. "
            "Обратитесь к преподавателю или в деканат за отработкой.")
    elif pct < 0.5 or (need and need > remaining * 0.6):
        status, label, advice = "risk", "Есть риск", (
            f"Для допуска нужно ещё {need} б. из {remaining} возможных. "
            "Не пропускайте ближайшие контрольные точки.")
    elif pct < 0.75:
        status, label = "ok", "Нормально"
        advice = (f"До допуска осталось {need} б." if need else "Допуск уже набран.") + \
            " Если поднажать, можно выйти на «хорошо»."
    else:
        status, label = "good", "Отлично идёт"
        advice = ("Допуск уже набран. " if not need else f"До допуска {need} б. ") + \
            "Держите темп — так выходит «отлично»."
    upcoming = sorted((a for a in items if a.points is None and a.due), key=lambda a: a.due)
    # Один возможный набор баллов в порядке сроков; работа без даты идёт последней.
    pending = [a for a in items if a.points is None]
    as_of = today()
    overdue = [a for a in pending if a.due and a.due < as_of]
    candidates = sorted((a for a in pending if not a.due or a.due >= as_of),
                        key=lambda a: (a.due or date.max, a.id or 0))
    plan = []
    gap = need
    for assessment in candidates:
        take = min(gap, assessment.max_points)
        if take > 0:
            plan.append((assessment, take))
            gap -= take
    return dict(course=course, kts=items, admission_plan=plan, uncovered=gap, overdue=overdue, earned=earned, so_far=so_far, total=total,
                remaining=remaining, pct=pct, need=need, status=status, label=label,
                advice=advice, next_item=upcoming[0] if upcoming else None,
                w_earned=width_class(earned, total), w_so_far=width_class(so_far, total),
                w_admission=width_class(ADMISSION, total))


RETAKE_CURRENT_STATUSES = ("submitted", "in_progress", "clarify", "ready", "done")


def retake_choices(db: Session, user: m.User, debts: list[m.PastResult]) -> dict:
    """В старых заявках дисциплина хранится в свободном поле «Цель»."""
    def normalized(name: str) -> str:
        return " ".join(name.casefold().split())

    debts_by_course = defaultdict(list)
    for debt in debts:
        debts_by_course[normalized(debt.course)].append(debt.id)

    requests_by_course = defaultdict(list)
    choices = {}
    if debts:
        requests = db.execute(select(m.StudentRequest, m.RetakeDirection.debt_id)
                              .outerjoin(m.RetakeDirection, m.RetakeDirection.request_id == m.StudentRequest.id)
                              .where(
            m.StudentRequest.student_id == user.id, m.StudentRequest.type_code == "retake",
            m.StudentRequest.status.in_(RETAKE_CURRENT_STATUSES),
        ))
        for request, debt_id in requests:
            if debt_id is None:
                requests_by_course[normalized(request.purpose)].append(request)
            else:
                choices[debt_id] = ("existing", request)

    for course, ids in debts_by_course.items():
        matches = requests_by_course[course]
        for debt_id in ids:
            if debt_id in choices:
                continue
            if not matches:
                choices[debt_id] = ("new", None)
            elif len(ids) == len(matches) == 1:
                choices[debt_id] = ("existing", matches[0])
            else:
                choices[debt_id] = ("list", None)
    return choices


def grades_overview(db: Session, user: m.User) -> dict:
    courses = db.scalars(
        select(m.Course).where(m.Course.group_id == user.group_id).order_by(m.Course.id)
    ).all() if user.group_id else []
    items = db.scalars(select(m.Assessment).where(m.Assessment.student_id == user.id)).all()
    by_course: dict[int, list[m.Assessment]] = {}
    for a in items:
        by_course.setdefault(a.course_id, []).append(a)
    rows = [course_overview(c, by_course.get(c.id, [])) for c in courses]

    earned = sum(r["earned"] for r in rows)
    so_far = sum(r["so_far"] for r in rows)
    avg_pct = round(earned / so_far * 100) if so_far else None

    past = db.scalars(
        select(m.PastResult).where(m.PastResult.student_id == user.id)
        .order_by(m.PastResult.semester, m.PastResult.id)
    ).all()
    debts = [r for r in past if r.debt]
    marks = [GRADE_POINTS[r.grade] for r in past if r.grade in GRADE_POINTS]
    gpa = round(sum(marks) / len(marks), 2) if marks else None
    semesters: dict[int, list[m.PastResult]] = {}
    for r in past:
        semesters.setdefault(r.semester, []).append(r)
    sem_rows = []
    for sem, results in sorted(semesters.items(), reverse=True):
        sm = [GRADE_POINTS[r.grade] for r in results if r.grade in GRADE_POINTS]
        sem_rows.append(dict(semester=sem, results=results,
                             avg=round(sum(sm) / len(sm), 2) if sm else None))

    critical = [r for r in rows if r["status"] == "critical"]
    risk = [r for r in rows if r["status"] == "risk"]
    if debts or critical:
        tone, headline = "bad", "Нужно ваше внимание"
        parts = []
        if debts:
            parts.append(f"{len(debts)} {plural(len(debts), 'долг', 'долга', 'долгов')} с прошлой сессии")
        if critical:
            parts.append(f"недопуск по {len(critical)} {plural(len(critical), 'дисциплине', 'дисциплинам', 'дисциплинам')}")
        if risk:
            parts.append(f"риск по {len(risk)} {plural(len(risk), 'дисциплине', 'дисциплинам', 'дисциплинам')}")
        text = ", ".join(parts).capitalize() + ". Ниже — что сделать и до какого числа."
    elif risk:
        tone, headline = "warn", "В целом нормально, но есть риск"
        text = "Риск недопуска: " + ", ".join(r["course"].name for r in risk) + "."
    elif rows:
        tone, headline = "good", "Всё идёт хорошо"
        text = "Долгов нет, по всем дисциплинам вы идёте к допуску."
    else:
        tone, headline, text = "neutral", "Данных пока нет", "Оценки появятся после первых контрольных точек."

    scholarship = None
    if user.funding == "бюджет":
        low = [r for r in rows if r["pct"] is not None and r["pct"] < 0.7]
        if debts:
            scholarship = ("bad", "Стипендия под угрозой: с долгами её не назначат.")
        elif low:
            scholarship = ("warn", "Стипендия: чтобы её сохранить, нужны «хорошо» и «отлично». Стоит подтянуть: " +
                           ", ".join("«" + r["course"].name + "»" for r in low) + ".")
        else:
            scholarship = ("good", "Стипендия: если сдадите сессию без троек, она сохранится.")

    return dict(rows=rows, avg_pct=avg_pct, gpa=gpa, debts=debts, semesters=sem_rows,
                critical=critical, risk=risk, tone=tone, headline=headline, text=text,
                scholarship=scholarship)


def session_exams(db: Session, user: m.User) -> list[m.Exam]:
    if not user.group_id:
        return []
    return list(db.scalars(
        select(m.Exam).where(m.Exam.group_id == user.group_id).order_by(m.Exam.at)
    ).all())


# ---------------------------------------------------------------- дедлайны

DEADLINE_KIND = {"study": "Учёба", "docs": "Документы", "money": "Оплата", "admin": "Деканат",
                 "exam": "Сессия", "retake": "Пересдача", "assessment": "Контрольная точка"}


def upcoming(db: Session, user: m.User, days: int = 21) -> list[dict]:
    """Ближайшие дедлайны студента: личные и групповые, сессия, пересдачи, контрольные точки."""
    t = today()
    end = t + timedelta(days=days)
    out: list[dict] = []
    conds = [m.Deadline.student_id == user.id]
    if user.group_id:
        conds.append(m.Deadline.group_id == user.group_id)
    for d in db.scalars(select(m.Deadline).where(or_(*conds), m.Deadline.due >= t, m.Deadline.due <= end)):
        out.append(dict(date=d.due, time="", title=d.title, kind=d.kind, note=d.note, link=""))
    if user.group_id:
        for e in db.scalars(select(m.Exam).where(
                m.Exam.group_id == user.group_id, m.Exam.at >= datetime.combine(t, datetime.min.time()),
                m.Exam.at <= datetime.combine(end, datetime.max.time()))):
            out.append(dict(date=e.at.date(), time=f"{e.at:%H:%M}",
                            title=f"{CONTROL_LABEL.get(e.kind, '')}: {e.title}", kind="exam",
                            note=e.room, link="/grades#session"))
    for r in db.scalars(select(m.PastResult).where(m.PastResult.student_id == user.id,
                                                   m.PastResult.debt.is_(True))):
        if r.retake_at and t <= r.retake_at.date() <= end:
            out.append(dict(date=r.retake_at.date(), time=f"{r.retake_at:%H:%M}",
                            title=f"Пересдача: {r.course}", kind="retake", note=r.retake_room,
                            link="/grades#debts"))
    courses = {c.id: c for c in db.scalars(select(m.Course).where(m.Course.group_id == user.group_id))} \
        if user.group_id else {}
    for a in db.scalars(select(m.Assessment).where(
            m.Assessment.student_id == user.id, m.Assessment.points.is_(None),
            m.Assessment.due >= t, m.Assessment.due <= end)):
        c = courses.get(a.course_id)
        out.append(dict(date=a.due, time="", title=f"{a.title}" + (f" — {c.name}" if c else ""),
                        kind="assessment", note=f"до {a.max_points} б.", link="/grades"))
    out.sort(key=lambda x: (x["date"], x["time"]))
    for x in out:
        x["left"] = (x["date"] - t).days
        x["kind_label"] = DEADLINE_KIND.get(x["kind"], "")
        x["urgent"] = x["left"] <= 3
    return out


# ---------------------------------------------------------------- заявки

# Справочник видов заявок. Поле purpose подписывается по-разному для каждого вида.
REQUEST_TYPES: dict[str, dict] = {
    "study": dict(cat="cert", title="Справка об обучении", days=1, electronic=True,
                  purpose_label="Куда нужна справка", purpose_hint="Например: по месту работы родителей",
                  purpose_required=True, about="Подтверждает, что вы учитесь. Нужна для работы, банка, соцзащиты."),
    "military": dict(cat="cert", title="Справка для военкомата", days=3, electronic=False,
                     purpose_label="Военный комиссариат", purpose_hint="Название военкомата",
                     purpose_required=True, about="Для отсрочки. Выдаётся только на бумаге с печатью."),
    "visa": dict(cat="cert", title="Справка для визы (на английском)", days=5, electronic=False,
                 purpose_label="Страна и консульство", purpose_hint="Например: консульство Сербии",
                 purpose_required=True, about="Справка об обучении на английском языке для консульства."),
    "scholarship": dict(cat="cert", title="Справка о стипендии", days=3, electronic=True,
                        purpose_label="За какой период", purpose_hint="Например: сентябрь–ноябрь 2026",
                        purpose_required=True, about="Сумма выплаченной стипендии за выбранный период."),
    "transcript": dict(cat="cert", title="Выписка оценок", days=7, electronic=True,
                       purpose_label="Куда нужна выписка", purpose_hint="Например: летняя школа",
                       purpose_required=False, about="Все оценки за прошедшие сессии с печатью деканата."),
    "aid": dict(cat="stmt", title="Заявление на материальную помощь", days=10, electronic=False,
                purpose_label="Основание", purpose_hint="Кратко опишите ситуацию",
                purpose_required=True, about="Разовая выплата. Документы-основания принесите в деканат."),
    "individual": dict(cat="stmt", title="Индивидуальный график сессии", days=5, electronic=False,
                       purpose_label="Причина", purpose_hint="Например: соревнования, болезнь",
                       purpose_required=True, about="Перенос зачётов и экзаменов по уважительной причине."),
    "retake": dict(cat="stmt", title="Направление на пересдачу", days=2, electronic=True,
                   purpose_label="Дисциплина", purpose_hint="Название дисциплины",
                   purpose_required=True, about="Без направления на пересдачу не допустят."),
    "topic": dict(cat="stmt", title="Утверждение темы курсовой", days=5, electronic=False,
                  purpose_label="Тема и руководитель", purpose_hint="Тема — ФИО руководителя",
                  purpose_required=True, about="Тему согласуйте с руководителем до подачи."),
}

STATUS_LABEL = {
    "submitted": "Подана",
    "in_progress": "В работе",
    "clarify": "Нужно уточнение",
    "ready": "Готово",
    "done": "Завершена",
    "rejected": "Отклонена",
    "cancelled": "Отозвана",
}
STATUS_TONE = {"submitted": "info", "in_progress": "warn", "clarify": "warn", "ready": "good", "done": "neutral",
               "rejected": "bad", "cancelled": "neutral"}
ACTIVE_STATUSES = ("submitted", "in_progress", "clarify", "ready")
# Какие статусы сотрудник может поставить из текущего
STAFF_TRANSITIONS = {
    "submitted": ["in_progress", "clarify", "ready", "rejected"],
    "in_progress": ["ready", "clarify", "rejected"],
    "ready": ["done"],
}
DELIVERY_LABEL = {"electronic": "Электронно, в кабинете", "paper": "На бумаге, в деканате"}


def add_notification(db: Session, user_id: int, kind: str, title: str, body: str = "",
                     link: str = "", important: bool = False, at: datetime | None = None,
                     read: bool = False) -> None:
    ts = at or now()
    db.add(m.Notification(user_id=user_id, kind=kind, title=title, body=body, link=link,
                          important=important, created_at=ts, read_at=ts if read else None))
