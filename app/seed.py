"""Демо-данные. Все люди, группы, оценки и заявки вымышлены.

Даты считаются от дня первого запуска, поэтому в любой день проверки у
студентов есть пары, изменения в расписании, ближайшие дедлайны и сессия.

Пароли тестовых аккаунтов в репозитории не хранятся — только bcrypt-хеши.
Сами пароли переданы жюри в комментарии к работе. Хеши можно заменить
переменными окружения DEMO_HASH_STUDENT1 / DEMO_HASH_STUDENT2 / DEMO_HASH_DEKANAT.
"""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models as m
from .services import add_notification, default_week, moved_lesson_link, now, today

DEMO_HASHES = {
    "student1": "$2b$12$AGnWjfgp60d8RncsM8yt1OWWMoaIHTHYPj/0xph3lc.Fmev2k/oii",
    "student2": "$2b$12$TvZs9l1DV2.gFfGFBu4Vx.pOiKR7sF2tJ7gl63tUW7ugTIgkrdT5q",
    "dekanat": "$2b$12$NSzASTmbbdzuvRnF7bNwOeKEddKo3mdhkVAFC2ZuzKUkjDwpWz8Bq",
}
NO_LOGIN = "!"  # у фоновых студентов нет пароля: войти под ними нельзя


def _hash(login: str) -> str:
    return os.environ.get(f"DEMO_HASH_{login.upper()}") or DEMO_HASHES[login]


def seed_if_empty(db: Session) -> bool:
    if db.scalar(select(func.count()).select_from(m.User)):
        return False
    seed(db)
    return True


def seed(db: Session) -> None:
    T = today()
    M = default_week(T)          # понедельник недели, которую студент увидит первой
    NOW = now()

    def d(offset: int):
        return M + timedelta(days=offset)

    def dt(day, hhmm: str) -> datetime:
        h, mi = hhmm.split(":")
        return datetime.combine(day, time(int(h), int(mi)))

    def ago(days: float, hour: int | None = None) -> datetime:
        x = NOW - timedelta(days=days)
        return x.replace(hour=hour, minute=(x.minute // 5) * 5) if hour is not None else x

    # ------------------------------------------------------------ структура
    f1 = m.Faculty(name="Факультет международных отношений", short="ФМО",
                   office="Корпус А, каб. 214", phone="+7 (000) 000-12-14",
                   email="dekanat-fmo@university.example", hours="Пн–Пт 10:00–17:00, обед 13:00–14:00")
    f2 = m.Faculty(name="Факультет экономики и анализа данных", short="ФЭАД",
                   office="Корпус Б, каб. 105", phone="+7 (000) 000-11-05",
                   email="dekanat-fead@university.example", hours="Пн–Пт 09:30–17:30, обед 13:00–13:45")
    db.add_all([f1, f2])
    db.flush()
    p1 = m.Program(code="41.03.05", name="Международные отношения",
                   profile="Мировая политика", faculty_id=f1.id)
    p2 = m.Program(code="38.03.01", name="Экономика",
                   profile="Анализ данных в экономике", faculty_id=f2.id)
    db.add_all([p1, p2])
    db.flush()
    g1 = m.Group(name="МО-21", course=2, semester=3, program_id=p1.id)
    g2 = m.Group(name="ЭК-34", course=3, semester=5, program_id=p2.id)
    db.add_all([g1, g2])
    db.flush()

    s1 = m.User(login="student1", password_hash=_hash("student1"), role="student",
                full_name="Кедрова Мирослава Андреевна", email="m.kedrova@student.example",
                group_id=g1.id, record_book="25-0457", study_form="очная", funding="бюджет",
                enrolled_year=2025)
    s2 = m.User(login="student2", password_hash=_hash("student2"), role="student",
                full_name="Ласточкин Тимур Ринатович", email="t.lastochkin@student.example",
                group_id=g2.id, record_book="24-1183", study_form="очная", funding="контракт",
                enrolled_year=2024)
    staff = m.User(login="dekanat", password_hash=_hash("dekanat"), role="staff",
                   full_name="Белоусова Елена Викторовна", email="e.belousova@university.example",
                   position="Специалист учебного офиса")
    bg = [
        m.User(login="bg-sobolev", password_hash=NO_LOGIN, role="student",
               full_name="Соболев Илья Маркович", group_id=g1.id, record_book="25-0461",
               study_form="очная", funding="бюджет", enrolled_year=2025),
        m.User(login="bg-novikova", password_hash=NO_LOGIN, role="student",
               full_name="Новикова Полина Сергеевна", group_id=g1.id, record_book="25-0470",
               study_form="очная", funding="контракт", enrolled_year=2025),
        m.User(login="bg-garipova", password_hash=NO_LOGIN, role="student",
               full_name="Гарипова Диляра Ильдаровна", group_id=g2.id, record_book="24-1190",
               study_form="очная", funding="бюджет", enrolled_year=2024),
    ]
    db.add_all([s1, s2, staff, *bg])
    db.flush()

    # ------------------------------------------------------------ расписание
    def lessons(group, rows):
        for wd, pair, subject, kind, room, teacher, *par in rows:
            db.add(m.ScheduleItem(group_id=group.id, weekday=wd, pair=pair, subject=subject,
                                  kind=kind, room=room, teacher=teacher,
                                  parity=par[0] if par else "all"))

    TMO, HD, WE, ST = ("Теория международных отношений", "История дипломатии",
                       "Мировая экономика", "Статистика для международников")
    EN, ES, PE = "Английский язык", "Испанский язык", "Физическая культура"
    lessons(g1, [
        (0, 1, TMO, "Лекция", "ауд. 1012", "Орлов Г. С."),
        (0, 2, TMO, "Семинар", "ауд. 3041", "Орлов Г. С."),
        (0, 3, EN, "Практика", "ауд. 4110", "Захарова И. П."),
        (1, 2, HD, "Лекция", "ауд. 1003", "Гаврилов П. Н."),
        (1, 3, ES, "Практика", "ауд. 4207", "Ветрова Л. А."),
        (1, 4, PE, "Практика", "Спорткомплекс, зал 2", "Демидов А. А."),
        (2, 1, WE, "Лекция", "ауд. 1020", "Сабурова Е. М."),
        (2, 2, WE, "Семинар", "ауд. 2015", "Сабурова Е. М.", "odd"),
        (2, 2, ST, "Семинар", "ауд. 2112", "Ким Д. В.", "even"),
        (2, 3, EN, "Практика", "ауд. 4110", "Захарова И. П."),
        (3, 2, ST, "Лекция", "ауд. 1012", "Ким Д. В."),
        (3, 3, HD, "Семинар", "ауд. 3022", "Гаврилов П. Н."),
        (3, 4, ES, "Практика", "ауд. 4207", "Ветрова Л. А."),
        (4, 1, EN, "Практика", "ауд. 4110", "Захарова И. П."),
        (4, 2, ES, "Практика", "ауд. 4207", "Ветрова Л. А."),
        (5, 2, HD, "Лекция", "Онлайн, ссылка в ЛМС", "Гаврилов П. Н.", "even"),
    ])
    ECM, MAC, CF, PY, BE, CW = ("Эконометрика", "Макроэкономика (продвинутый курс)",
                                "Корпоративные финансы", "Анализ данных на Python",
                                "Деловой английский", "Курсовая работа")
    lessons(g2, [
        (0, 3, ECM, "Лекция", "ауд. 2201", "Ибрагимов Р. Т."),
        (0, 4, ECM, "Семинар", "комп. класс 2310", "Ибрагимов Р. Т."),
        (1, 1, MAC, "Лекция", "ауд. 1101", "Лаптева О. В."),
        (1, 2, MAC, "Семинар", "ауд. 2105", "Лаптева О. В."),
        (1, 3, BE, "Практика", "ауд. 4012", "Носова А. Р."),
        (2, 2, CF, "Лекция", "ауд. 1104", "Фролов К. И."),
        (2, 3, CF, "Семинар", "ауд. 2108", "Фролов К. И."),
        (3, 3, PY, "Лабораторная", "комп. класс 2312", "Ким Д. В."),
        (3, 4, PY, "Лабораторная", "комп. класс 2312", "Ким Д. В."),
        (3, 5, "Консультация по курсовой", "Консультация", "ауд. 2205", "Лаптева О. В.", "odd"),
        (4, 1, BE, "Практика", "ауд. 4012", "Носова А. Р."),
        (4, 2, ECM, "Семинар", "комп. класс 2310", "Ибрагимов Р. Т."),
    ])

    changes = [
        # группа, дата, пара, вид, поля
        (g1, d(0), 1, "room", dict(new_room="ауд. 1215", note="В ауд. 1012 ремонт проектора")),
        (g1, d(1), 3, "moved", dict(new_date=d(4), new_pair=3, new_room="ауд. 4207",
                                    note="Перенос по просьбе преподавателя")),
        (g1, d(2), 4, "extra", dict(subject="Консультация по эссе", lesson_kind="Консультация",
                                    new_room="ауд. 3041", new_teacher="Орлов Г. С.",
                                    note="Для тех, кто пишет эссе по ТМО")),
        (g1, d(3), 2, "teacher", dict(new_teacher="Полякова Н. Ю.",
                                      note="Ким Д. В. на конференции — замена")),
        (g1, d(7), 2, "cancel", dict(note="Преподаватель на больничном, задание — в ЛМС")),
        (g1, d(9), 1, "room", dict(new_room="Онлайн, ссылка в ЛМС", note="Лекция пройдёт онлайн")),
        (g1, d(14), 3, "room", dict(new_room="ауд. 4301", note="Временная замена аудитории")),
        (g1, d(-7), 2, "cancel", dict(note="Праздничное мероприятие факультета")),
        (g2, d(0), 4, "cancel", dict(note="Семинар отменён: преподаватель на защите диссертации")),
        (g2, d(2), 2, "room", dict(new_room="ауд. 1301", note="Совместно с группой ЭК-35")),
        (g2, d(3), 5, "extra", dict(subject="Отработка по эконометрике", lesson_kind="Отработка",
                                    new_room="комп. класс 2310", new_teacher="Ибрагимов Р. Т.",
                                    note="Для тех, у кого меньше 20 баллов")),
        (g2, d(4), 1, "moved", dict(new_date=d(5), new_pair=2, new_room="ауд. 4012",
                                    note="Пятница — день открытых дверей")),
        (g2, d(8), 1, "teacher", dict(new_teacher="Демьянова С. Л.", note="Замена преподавателя")),
        (g2, d(15), 3, "room", dict(new_room="ауд. 2210", note="Временная замена аудитории")),
    ]
    moves = {}
    for group, day, pair, kind, extra in changes:
        change = m.ScheduleChange(group_id=group.id, date=day, pair=pair, kind=kind, **extra)
        db.add(change)
        if kind == "moved":
            moves[group.id, day, pair] = change

    # ------------------------------------------------------------ дисциплины и баллы
    def courses(group, rows):
        out = {}
        for name, teacher, control, credits in rows:
            c = m.Course(group_id=group.id, name=name, teacher=teacher, control=control, credits=credits)
            db.add(c)
            out[name] = c
        db.flush()
        return out

    c1 = courses(g1, [
        (TMO, "Орлов Г. С.", "exam", 5), (HD, "Гаврилов П. Н.", "exam", 4),
        (ES, "Ветрова Л. А.", "exam", 4), (EN, "Захарова И. П.", "diff", 4),
        (WE, "Сабурова Е. М.", "credit", 3), (ST, "Ким Д. В.", "credit", 3),
        (PE, "Демидов А. А.", "credit", 2),
    ])
    c2 = courses(g2, [
        (ECM, "Ибрагимов Р. Т.", "exam", 5), (MAC, "Лаптева О. В.", "exam", 5),
        (CF, "Фролов К. И.", "exam", 4), (PY, "Ким Д. В.", "diff", 4),
        (BE, "Носова А. Р.", "credit", 3), (CW, "Лаптева О. В.", "diff", 3),
    ])

    def points(student, course, rows):
        for title, mx, pts, *due in rows:
            db.add(m.Assessment(student_id=student.id, course_id=course.id, title=title,
                                max_points=mx, points=pts,
                                due=(T + timedelta(days=due[0])) if due else None))

    points(s1, c1[TMO], [("Работа на семинарах", 10, 9), ("Контрольная работа № 1", 15, 13),
                         ("Эссе", 15, None, 5), ("Контрольная работа № 2", 20, None, 19)])
    points(s1, c1[HD], [("Доклад", 10, 9), ("Тест № 1", 15, 12), ("Работа на семинарах", 15, 13),
                        ("Тест № 2", 20, None, 12)])
    points(s1, c1[ES], [("Диктант", 20, 16), ("Устная тема", 20, 18), ("Лексический тест", 20, None, 9)])
    points(s1, c1[EN], [("Эссе", 15, 14), ("Устный опрос", 15, 13), ("Итоговый тест", 30, None, 16)])
    points(s1, c1[WE], [("Кейс № 1", 20, 18), ("Кейс № 2", 20, 17), ("Тест", 20, None, 15)])
    points(s1, c1[ST], [("Домашнее задание № 1", 15, 11), ("Домашнее задание № 2", 15, 9),
                        ("Контрольная работа", 30, None, 11)])
    points(s1, c1[PE], [("Посещаемость", 40, 31), ("Нормативы", 20, None, 20)])

    points(s2, c2[ECM], [("Домашнее задание № 1", 10, 4), ("Домашнее задание № 2", 10, 3),
                         ("Контрольная работа № 1", 20, 9), ("Проект: регрессионная модель", 20, None, 10)])
    points(s2, c2[MAC], [("Тест № 1", 15, 12), ("Эссе", 15, 11), ("Контрольная работа", 30, None, 17)])
    points(s2, c2[CF], [("Кейс", 20, 15), ("Тест", 20, 14), ("Финансовая модель", 20, None, 13)])
    points(s2, c2[PY], [("Лабораторные 1–3", 30, 29), ("Проект", 30, None, 11)])
    points(s2, c2[BE], [("Презентация", 20, 17), ("Участие в дискуссиях", 20, 15), ("Тест", 20, None, 8)])
    points(s2, c2[CW], [("План работы", 10, 10), ("Глава 1", 20, None, 4), ("Итоговый текст", 30, None, 30)])

    # ------------------------------------------------------------ прошлые сессии
    def past(student, sem, rows):
        for course, control, grade, pts, *rest in rows:
            r = m.PastResult(student_id=student.id, semester=sem, course=course, control=control,
                             grade=grade, points=pts)
            if rest:
                r.debt = True
                r.retake_at, r.retake_room = rest[0], rest[1]
            db.add(r)

    past(s1, 1, [("Введение в теорию международных отношений", "exam", "отлично", 92),
                 ("История России", "exam", "отлично", 88), ("Английский язык", "diff", "отлично", 95),
                 ("Основы права", "exam", "хорошо", 78), ("Информатика", "credit", "зачтено", 81),
                 ("Физическая культура", "credit", "зачтено", None)])
    past(s1, 2, [("История международных отношений", "exam", "хорошо", 81),
                 ("Английский язык", "diff", "отлично", 90), ("Испанский язык", "exam", "отлично", 87),
                 ("Экономическая теория", "exam", "хорошо", 76), ("Философия", "credit", "зачтено", 72),
                 ("Физическая культура", "credit", "зачтено", None)])
    past(s2, 3, [("Микроэкономика", "exam", "хорошо", 77), ("Линейная алгебра", "exam", "удовл.", 61),
                 ("Английский язык", "credit", "зачтено", 80), ("Программирование на Python", "diff", "отлично", 93)])
    past(s2, 4, [("Теория вероятностей и мат. статистика", "exam", "неуд.", 41,
                  dt(T + timedelta(days=8), "15:40"), "ауд. 2201"),
                 ("Базы данных и SQL", "exam", "отлично", 91),
                 ("История экономических учений", "exam", "удовл.", 62),
                 ("Английский язык", "credit", "зачтено", 75)])

    # ------------------------------------------------------------ сессия
    def exams(group, rows):
        for title, kind, offset, hhmm, room, teacher in rows:
            db.add(m.Exam(group_id=group.id, title=title, kind=kind, at=dt(d(offset), hhmm),
                          room=room, teacher=teacher))

    exams(g1, [
        (PE, "credit", 26, "10:30", "Спорткомплекс", "Демидов А. А."),
        (WE, "credit", 28, "10:30", "ауд. 1020", "Сабурова Е. М."),
        (ST, "credit", 29, "12:40", "ауд. 2112", "Ким Д. В."),
        (EN, "diff", 31, "09:00", "ауд. 4110", "Захарова И. П."),
        (TMO, "consult", 48, "14:10", "ауд. 3041", "Орлов Г. С."),
        (TMO, "exam", 49, "10:00", "ауд. 1012", "Орлов Г. С."),
        (HD, "consult", 52, "14:10", "ауд. 3022", "Гаврилов П. Н."),
        (HD, "exam", 53, "10:00", "ауд. 1003", "Гаврилов П. Н."),
        (ES, "exam", 57, "10:00", "ауд. 4207", "Ветрова Л. А."),
    ])
    exams(g2, [
        (BE, "credit", 28, "09:00", "ауд. 4012", "Носова А. Р."),
        (CW, "diff", 29, "12:40", "ауд. 2205", "Лаптева О. В."),
        (PY, "diff", 30, "12:40", "комп. класс 2312", "Ким Д. В."),
        (ECM, "consult", 49, "14:10", "ауд. 2201", "Ибрагимов Р. Т."),
        (ECM, "exam", 50, "10:00", "ауд. 2201", "Ибрагимов Р. Т."),
        (MAC, "exam", 54, "10:00", "ауд. 1101", "Лаптева О. В."),
        (CF, "exam", 58, "10:00", "ауд. 1104", "Фролов К. И."),
    ])

    # ------------------------------------------------------------ дедлайны
    for kw in [
        dict(group_id=g1.id, title="Запись на курсы по выбору (весна)", due=T + timedelta(days=12),
             kind="admin", note="Форма записи — в разделе «Заявки» с 1 декабря"),
        dict(group_id=g1.id, title="Медосмотр для занятий физкультурой", due=T + timedelta(days=16),
             kind="docs", note="Справку принести на кафедру физвоспитания"),
        dict(student_id=s1.id, title="Оплата общежития за декабрь", due=T + timedelta(days=9),
             kind="money", note="Через банк или в кассе корпуса А"),
        dict(group_id=g2.id, title="Выбор темы ВКР: предварительная заявка", due=T + timedelta(days=18),
             kind="admin", note="Согласуйте тему с кафедрой"),
        dict(student_id=s2.id, title="Оплата обучения за весенний семестр", due=T + timedelta(days=20),
             kind="money", note="По договору — до начала семестра"),
    ]:
        db.add(m.Deadline(**kw))

    # ------------------------------------------------------------ заявки
    def request(student, code, title, purpose, status, created_days, delivery="electronic",
                copies=1, comment="", history=()):
        created = ago(created_days)
        r = m.StudentRequest(student_id=student.id, type_code=code, title=title, purpose=purpose,
                             copies=copies, delivery=delivery, comment=comment, status=status,
                             created_at=created, updated_at=created)
        r.events.append(m.RequestEvent(status="submitted", comment="", actor=student.full_name,
                                       created_at=created))
        for st, days, text in history:
            ts = ago(days)
            r.events.append(m.RequestEvent(status=st, comment=text, actor=staff.full_name, created_at=ts))
            r.updated_at = ts
            if text:
                r.staff_comment = text
        db.add(r)
        return r

    request(s1, "study", "Справка об обучении", "По месту работы мамы", "done", 6,
            history=[("in_progress", 5.9, ""), ("ready", 5.5, "Электронная справка готова — она в карточке заявки."),
                     ("done", 5.4, "")])
    request(s1, "visa", "Справка для визы (на английском)", "Консульство Сербии", "in_progress", 2,
            delivery="paper", comment="Нужно к пятнице, если можно",
            history=[("in_progress", 1.5, "Готовим перевод, обычно это 3 рабочих дня.")])
    request(s2, "retake", "Направление на пересдачу", "Теория вероятностей и мат. статистика", "ready", 4,
            history=[("in_progress", 3.8, ""), ("ready", 3.1, "Направление готово: откройте его в карточке или заберите в каб. 105.")])
    request(s2, "aid", "Заявление на материальную помощь", "Сложная финансовая ситуация в семье", "rejected", 9,
            delivery="paper",
            history=[("in_progress", 8, ""), ("rejected", 7, "Не приложены документы-основания. Подайте заявление ещё раз и принесите копии документов в каб. 105.")])
    request(s2, "study", "Справка об обучении", "Для Социального фонда", "submitted", 0.2)
    request(bg[0], "military", "Справка для военкомата", "Военкомат Центрального района", "submitted", 0.6,
            delivery="paper")
    request(bg[1], "transcript", "Выписка оценок", "Летняя школа за рубежом", "in_progress", 3,
            history=[("in_progress", 2, "")])
    request(bg[2], "individual", "Индивидуальный график сессии", "Участие в чемпионате по шахматам",
            "submitted", 1.2, delivery="paper")

    # ------------------------------------------------------------ уведомления
    def note(user, kind, title, body, link, days, important=False, read=False):
        add_notification(db, user.id, kind, title, body, link, important, at=ago(days), read=read)

    for u in (s1, s2):
        note(u, "news", "Опубликовано расписание зимней сессии",
             "Зачётная неделя — с конца декабря, экзамены — в январе. Даты и аудитории — в разделе «Успеваемость».",
             "/grades#session", 1.1, important=True)
        note(u, "news", "День карьеры: встречи с работодателями",
             "Стенды компаний и короткие интервью в холле корпуса А. Регистрация не нужна.", "", 3.2, read=True)
    note(s1, "schedule", "Аудитория изменена: Теория международных отношений",
         f"Лекция в {d(0):%d.%m} пройдёт в ауд. 1215 вместо 1012.", f"/schedule?week={M}", 0.4)
    note(s1, "schedule", "Перенос: Испанский язык",
         f"Пара {d(1):%d.%m} переносится на {d(4):%d.%m}, 3 пара.", moved_lesson_link(d(4), moves[g1.id, d(1), 3].id), 0.3)
    note(s1, "schedule", "Замена преподавателя: Статистика",
         f"Лекцию {d(3):%d.%m} проведёт Полякова Н. Ю.", f"/schedule?week={M}", 0.2)
    note(s1, "request", "Заявка «Справка для визы» взята в работу",
         "Готовим перевод, обычно это 3 рабочих дня.", "", 1.5)
    note(s1, "request", "Справка готова: Справка об обучении",
         "Электронная справка доступна в карточке заявки.", "", 5.5, read=True)
    note(s1, "deadline", "Через 5 дней сдать эссе по ТМО",
         "Загрузите эссе в ЛМС. За эссе можно получить до 15 баллов.", "/grades", 0.1)
    note(s2, "deadline", "Пересдача по теории вероятностей",
         f"Пересдача — {T + timedelta(days=8):%d.%m} в 15:40, ауд. 2201. Направление уже готово.",
         "/grades#debts", 0.5, important=True)
    note(s2, "schedule", "Отменён семинар по эконометрике",
         f"Семинар {d(0):%d.%m} (4 пара) отменён.", f"/schedule?week={M}", 0.4)
    note(s2, "schedule", "Перенос: Деловой английский",
         f"Пара {d(4):%d.%m} переносится на субботу {d(5):%d.%m}, 2 пара.", moved_lesson_link(d(5), moves[g2.id, d(4), 1].id), 0.3)
    note(s2, "request", "Направление на пересдачу готово",
         "Откройте его в карточке заявки или заберите в каб. 105.", "", 3.1)
    note(s2, "request", "Заявление на материальную помощь отклонено",
         "Не приложены документы-основания. Подайте заявление ещё раз.", "", 7, read=True)
    note(s2, "deadline", "Через 4 дня — первая глава курсовой",
         "Отправьте главу 1 научному руководителю, Лаптевой О. В.", "/grades", 0.1)

    db.commit()

    # привязываем ссылки уведомлений о заявках к самим заявкам
    for n in db.scalars(select(m.Notification).where(m.Notification.kind == "request",
                                                     m.Notification.link == "")):
        req = db.scalars(select(m.StudentRequest).where(m.StudentRequest.student_id == n.user_id)
                         .order_by(m.StudentRequest.id)).all()
        for r in req:
            if r.title.split(" (")[0].lower() in n.title.lower() or \
                    (r.type_code == "retake" and "пересдач" in n.title.lower()) or \
                    (r.type_code == "aid" and "помощ" in n.title.lower()):
                n.link = f"/requests/{r.id}"
                break
    db.commit()
