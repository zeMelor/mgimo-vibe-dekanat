"""Модель данных кабинета.

Ключевое правило безопасности: всё «личное» (оценки, заявки, уведомления,
сессии входа) привязано к user_id, и каждый запрос к этим таблицам в
обработчиках фильтруется по текущему пользователю.
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Faculty(Base):
    __tablename__ = "faculties"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    short: Mapped[str] = mapped_column(String(20))
    office: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(100), default="")
    hours: Mapped[str] = mapped_column(String(200), default="")


class Program(Base):
    __tablename__ = "programs"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    profile: Mapped[str] = mapped_column(String(200), default="")
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"))
    faculty: Mapped[Faculty] = relationship(lazy="joined")


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20))
    course: Mapped[int] = mapped_column(Integer)
    semester: Mapped[int] = mapped_column(Integer)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"))
    program: Mapped[Program] = relationship(lazy="joined")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(10))  # student | staff
    full_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(100), default="")
    # студент
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("groups.id"), nullable=True)
    record_book: Mapped[str] = mapped_column(String(20), default="")
    study_form: Mapped[str] = mapped_column(String(20), default="")
    funding: Mapped[str] = mapped_column(String(20), default="")
    enrolled_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # сотрудник
    position: Mapped[str] = mapped_column(String(100), default="")
    group: Mapped[Optional[Group]] = relationship(lazy="joined")

    @property
    def first_name(self) -> str:
        parts = self.full_name.split()
        return parts[1] if len(parts) > 1 else self.full_name

    @property
    def initials(self) -> str:
        parts = self.full_name.split()
        return "".join(p[0] for p in parts[:2]).upper()


class AuthSession(Base):
    """Сессия входа. В cookie лежит случайный токен, в базе — только его хеш."""

    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class ScheduleItem(Base):
    """Шаблон расписания группы: день недели, номер пары, чётность недели."""

    __tablename__ = "schedule_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = понедельник
    pair: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(40))
    room: Mapped[str] = mapped_column(String(60))
    teacher: Mapped[str] = mapped_column(String(100))
    parity: Mapped[str] = mapped_column(String(5), default="all")  # all | odd | even


class ScheduleChange(Base):
    """Изменение на конкретную дату: отмена, перенос, замена аудитории или преподавателя, доп. занятие."""

    __tablename__ = "schedule_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    pair: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))  # cancel | room | teacher | moved | extra
    new_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    new_pair: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_room: Mapped[str] = mapped_column(String(60), default="")
    new_teacher: Mapped[str] = mapped_column(String(100), default="")
    subject: Mapped[str] = mapped_column(String(150), default="")
    lesson_kind: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(String(200), default="")


class Course(Base):
    """Дисциплина текущего семестра у группы."""

    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    teacher: Mapped[str] = mapped_column(String(100))
    control: Mapped[str] = mapped_column(String(10))  # exam | credit | diff
    credits: Mapped[int] = mapped_column(Integer, default=3)


class Assessment(Base):
    """Контрольная точка студента по дисциплине (баллы текущего контроля)."""

    __tablename__ = "assessments"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    max_points: Mapped[int] = mapped_column(Integer)
    points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    due: Mapped[Optional[date]] = mapped_column(Date, nullable=True)


class PastResult(Base):
    """Итоговые оценки прошлых сессий; debt=True — академическая задолженность."""

    __tablename__ = "past_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    semester: Mapped[int] = mapped_column(Integer)
    course: Mapped[str] = mapped_column(String(150))
    control: Mapped[str] = mapped_column(String(10))
    grade: Mapped[str] = mapped_column(String(20))
    points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    debt: Mapped[bool] = mapped_column(Boolean, default=False)
    retake_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retake_room: Mapped[str] = mapped_column(String(60), default="")


class Exam(Base):
    """Расписание сессии группы: зачёты, экзамены, консультации."""

    __tablename__ = "exams"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    title: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(10))  # exam | credit | diff | consult
    at: Mapped[datetime] = mapped_column(DateTime)
    room: Mapped[str] = mapped_column(String(60))
    teacher: Mapped[str] = mapped_column(String(100))


class Deadline(Base):
    __tablename__ = "deadlines"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("groups.id"), nullable=True, index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(150))
    due: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20))  # study | docs | money | admin
    note: Mapped[str] = mapped_column(String(250), default="")


class StudentRequest(Base):
    """Заявка студента: справка или заявление."""

    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type_code: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(150))
    purpose: Mapped[str] = mapped_column(String(300), default="")
    copies: Mapped[int] = mapped_column(Integer, default=1)
    delivery: Mapped[str] = mapped_column(String(15), default="electronic")  # electronic | paper
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(15), default="submitted")
    staff_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    student: Mapped[User] = relationship()
    events: Mapped[list["RequestEvent"]] = relationship(
        order_by="(RequestEvent.created_at, RequestEvent.id)", cascade="all, delete-orphan"
    )

    @property
    def issued_at(self) -> datetime | None:
        """Историческим заявкам без события выдачи не приписываем выдуманную дату."""
        return next((event.created_at for event in self.events if event.status == "ready"), None)


class RetakeDirection(Base):
    """Контекст новой заявки. Старые заявки продолжают работать без этой записи."""

    __tablename__ = "retake_directions"
    debt_id: Mapped[int] = mapped_column(ForeignKey("past_results.id"), primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), unique=True)


class DocumentCheck(Base):
    """Случайная ссылка из QR-кода электронной справки: по ней любой проверит подлинность."""

    __tablename__ = "document_checks"
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), primary_key=True)
    token: Mapped[str] = mapped_column(String(40), unique=True, index=True)


class RequestEvent(Base):
    __tablename__ = "request_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(15))
    comment: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(150), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(15))  # news | schedule | request | deadline
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(200), default="")
    important: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
