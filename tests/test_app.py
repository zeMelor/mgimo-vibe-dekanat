"""Сквозные проверки: все разделы открываются, данные студентов разделены, защита работает.

Запуск: python -m pytest -q
Тесты поднимают приложение на временной базе и с собственными тестовыми паролями,
настоящие пароли демо-аккаунтов им не нужны.
"""
import os
import re
import tempfile

import bcrypt
import pytest

TEST_PASSWORD = "test-password-123"
_tmp = tempfile.mkdtemp(prefix="dekanat-test-")
os.environ["DATA_DIR"] = _tmp
_h = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
for login in ("STUDENT1", "STUDENT2", "DEKANAT"):
    os.environ[f"DEMO_HASH_{login}"] = _h

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app import security  # noqa: E402


def client_for(login):
    c = TestClient(app, base_url="http://testserver")
    c.__enter__()
    r = c.post("/login", data={"login": login, "password": TEST_PASSWORD}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


def csrf_of(html):
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "csrf token not found"
    return m.group(1)


@pytest.fixture(scope="module")
def s1():
    return client_for("student1")


@pytest.fixture(scope="module")
def s2():
    return client_for("student2")


@pytest.fixture(scope="module")
def staff():
    return client_for("dekanat")


def request_ids(client):
    html = client.get("/requests").text
    return sorted({int(x) for x in re.findall(r'href="/requests/(\d+)"', html)})


def test_anonymous_redirected_to_login():
    with TestClient(app) as c:
        for path in ["/", "/schedule", "/grades", "/requests", "/notifications", "/profile", "/requests/1", "/staff"]:
            r = c.get(path, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"].startswith("/login"), path


def test_wrong_password_and_rate_limit():
    security.limiter.reset("testclient|student1")
    with TestClient(app) as c:
        for _ in range(5):
            r = c.post("/login", data={"login": "student1", "password": "wrong"})
            assert r.status_code == 401
            assert "Неверный логин или пароль" in r.text
        r = c.post("/login", data={"login": "student1", "password": TEST_PASSWORD})
        assert r.status_code == 429  # даже верный пароль не принимается во время блокировки
    security.limiter.reset("testclient|student1")


def test_unknown_login_same_message():
    with TestClient(app) as c:
        r = c.post("/login", data={"login": "nobody", "password": "x"})
        assert r.status_code == 401 and "Неверный логин или пароль" in r.text


def test_background_students_cannot_login():
    with TestClient(app) as c:
        r = c.post("/login", data={"login": "bg-sobolev", "password": "!"})
        assert r.status_code == 401


@pytest.mark.parametrize("path", ["/", "/schedule", "/schedule?view=today", "/grades", "/requests",
                                  "/requests/new", "/requests/new?type=study", "/requests/new?type=aid",
                                  "/notifications", "/notifications?kind=schedule", "/profile",
                                  "/schedule.ics"])
def test_student_pages_ok(s1, s2, path):
    for c in (s1, s2):
        r = c.get(path)
        assert r.status_code == 200, (path, r.text[:500])


def test_profiles_differ(s1, s2):
    p1, p2 = s1.get("/profile").text, s2.get("/profile").text
    assert "Кедрова Мирослава" in p1 and "Ласточкин" not in p1
    assert "Ласточкин Тимур" in p2 and "Кедрова" not in p2
    for field in ["Факультет", "Направление", "Курс", "Группа"]:
        assert field in p1 and field in p2


def test_foreign_request_is_404(s1, s2):
    mine1, mine2 = request_ids(s1), request_ids(s2)
    assert mine1 and mine2 and not set(mine1) & set(mine2)
    for rid in mine2:
        assert s1.get(f"/requests/{rid}").status_code == 404
        assert s1.get(f"/requests/{rid}/document").status_code == 404
        r = s1.post(f"/requests/{rid}/cancel", data={"csrf": csrf_of(s1.get("/requests/new?type=study").text)})
        assert r.status_code == 404
    for rid in mine1:
        assert s2.get(f"/requests/{rid}").status_code == 404
    assert s1.get("/requests/999999").status_code == 404


def test_foreign_notification_is_404(s1, s2):
    ids2 = re.findall(r'href="/notifications/(\d+)"', s2.get("/notifications").text)
    assert ids2
    for nid in ids2:
        assert s1.get(f"/notifications/{nid}", follow_redirects=False).status_code == 404


def test_student_cannot_open_staff(s1):
    assert s1.get("/staff").status_code == 403
    assert s1.get("/staff/requests/1").status_code == 403
    assert s1.get("/staff/news").status_code == 403


def test_csrf_required(s1):
    r = s1.post("/requests/new", data={"type_code": "study", "purpose": "x"})
    assert r.status_code == 403
    r = s1.post("/requests/new", data={"type_code": "study", "purpose": "x", "csrf": "forged"})
    assert r.status_code == 403


def test_foreign_origin_rejected(s1):
    token = csrf_of(s1.get("/requests/new?type=study").text)
    r = s1.post("/requests/new", data={"type_code": "study", "purpose": "x", "csrf": token},
                headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_full_request_flow(s1, staff):
    page = s1.get("/requests/new?type=study").text
    # пустое обязательное поле — понятная ошибка, а не падение
    r = s1.post("/requests/new", data={"type_code": "study", "purpose": "", "csrf": csrf_of(page)})
    assert r.status_code == 422 and "Заполните поле" in r.text
    r = s1.post("/requests/new", data={"type_code": "study", "purpose": "<script>alert(1)</script>",
                                       "copies": 2, "delivery": "electronic", "csrf": csrf_of(page)},
                follow_redirects=False)
    assert r.status_code == 303
    rid = int(r.headers["location"].split("/")[2].split("?")[0])
    detail = s1.get(f"/requests/{rid}").text
    assert "&lt;script&gt;" in detail and "<script>alert(1)" not in detail  # экранирование
    # сотрудник видит заявку и меняет статус
    sp = staff.get(f"/staff/requests/{rid}")
    assert sp.status_code == 200
    r = staff.post(f"/staff/requests/{rid}", data={"new_status": "rejected", "comment": "",
                                                    "csrf": csrf_of(sp.text)})
    assert r.status_code == 422  # отказ без причины нельзя
    r = staff.post(f"/staff/requests/{rid}", data={"new_status": "ready", "comment": "Готово, забирайте",
                                                    "csrf": csrf_of(sp.text)}, follow_redirects=False)
    assert r.status_code == 303
    assert "Готово" in s1.get(f"/requests/{rid}").text
    assert "Готово: Справка об обучении" in s1.get("/notifications").text
    assert s1.get(f"/requests/{rid}/document").status_code == 200


def test_staff_news_reaches_students(s1, s2, staff):
    page = staff.get("/staff/news").text
    r = staff.post("/staff/news", data={"audience": "all", "title": "Тестовая новость деканата",
                                        "body": "Текст", "csrf": csrf_of(page)}, follow_redirects=False)
    assert r.status_code == 303
    assert "Тестовая новость деканата" in s1.get("/notifications").text
    assert "Тестовая новость деканата" in s2.get("/notifications").text


def test_security_headers(s1):
    r = s1.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    cookie_flags = TestClient(app).post("/login", data={"login": "student2", "password": TEST_PASSWORD},
                                        follow_redirects=False).headers["set-cookie"].lower()
    assert "httponly" in cookie_flags and "samesite=lax" in cookie_flags


def test_passwords_hashed_in_db():
    from app.db import SessionLocal
    from app import models as m
    with SessionLocal() as db:
        for u in db.query(m.User).all():
            assert TEST_PASSWORD not in u.password_hash
            assert u.password_hash.startswith("$2") or u.password_hash == "!"
        for s in db.query(m.AuthSession).all():
            assert len(s.token_hash) == 64  # в базе только SHA-256 от токена сессии


def test_logout(s2):
    c = client_for("student2")
    token = csrf_of(c.get("/profile").text)
    r = c.post("/logout", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/", follow_redirects=False).status_code == 303


def test_bad_query_is_friendly(s1):
    r = s1.get("/schedule?week=not-a-date")
    assert r.status_code == 400 and "Некорректный запрос" in r.text
    assert s1.get("/requests/new?type=hack").status_code == 404


def test_pwa_files():
    import json
    with TestClient(app) as c:
        r = c.get("/manifest.webmanifest")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
        man = json.loads(r.content)
        assert man["display"] == "standalone" and len(man["icons"]) >= 2
        for icon in man["icons"]:
            assert c.get(icon["src"]).status_code == 200
        r = c.get("/sw.js")
        assert r.status_code == 200 and "javascript" in r.headers["content-type"]
        assert r.headers["cache-control"] == "no-store"   # обновление service worker без задержек
        assert c.get("/static/offline.html").status_code == 200


def test_theme_light_by_default_dark_by_choice(s1):
    assert 'data-theme="light"' in s1.get("/").text
    s1.cookies.set("theme", "dark")
    assert 'data-theme="dark"' in s1.get("/").text
    s1.cookies.set("theme", "<script>")       # чужое значение не попадает в разметку
    assert 'data-theme="light"' in s1.get("/").text
    s1.cookies.delete("theme")


def test_font_served_with_font_type():
    with TestClient(app) as c:
        r = c.get("/static/fonts/open-sans-cyrillic-wdth-normal.woff2")
        assert r.status_code == 200 and r.headers["content-type"] == "font/woff2"
        assert r.content[:4] == b"wOF2"


@pytest.mark.parametrize("day,default_label", [(22, "следующая неделя"), (23, "текущая неделя")])
def test_schedule_calendar_week(s2, monkeypatch, day, default_label):
    from datetime import date
    from app import services

    monkeypatch.setattr(services, "today", lambda: date(2026, 11, day))
    html = s2.get("/schedule").text
    assert default_label + "</span>" in html
    if day == 22:
        assert 'href="/schedule?week=2026-11-16">к текущей неделе' in html
        assert "текущая неделя</span>" in s2.get("/schedule?week=2026-11-16").text


def test_retake_link_requires_unique_own_match(s2):
    from sqlalchemy import select
    from app import models as m, services
    from app.db import SessionLocal

    with SessionLocal() as db:
        user = db.scalar(select(m.User).where(m.User.login == "student2"))
        debt, = services.grades_overview(db, user)["debts"]
        existing = services.retake_choices(db, user, [debt])[debt.id][1]
        assert f'href="/requests/{existing.id}">Открыть готовое направление' in s2.get("/grades").text
        other = db.scalar(select(m.User).where(m.User.login == "student1"))
        duplicate = m.StudentRequest(student_id=other.id, type_code="retake", purpose=debt.course,
                                     title=existing.title, status="ready", created_at=existing.created_at,
                                     updated_at=existing.updated_at)
        db.add(duplicate)
        assert services.retake_choices(db, user, [debt])[debt.id] == ("existing", existing)
        duplicate.student_id = user.id
        assert services.retake_choices(db, user, [debt])[debt.id] == ("list", None)
        duplicate.purpose = "Другая дисциплина"
        assert services.retake_choices(db, user, [debt])[debt.id] == ("existing", existing)
        existing.status = "cancelled"
        assert services.retake_choices(db, user, [debt])[debt.id] == ("new", None)


@pytest.fixture
def new_debt():
    from datetime import timedelta
    from sqlalchemy import delete, select
    from app import models as m, services
    from app.db import SessionLocal

    with SessionLocal() as db:
        student = db.scalar(select(m.User).where(m.User.login == "student1"))
        debt = m.PastResult(student_id=student.id, semester=1, course="Тестовая дисциплина",
                            control="exam", grade="2", debt=True,
                            retake_at=services.now() + timedelta(days=1), retake_room="А-101")
        db.add(debt)
        db.commit()
        yield debt
        db.execute(delete(m.RetakeDirection).where(m.RetakeDirection.debt_id == debt.id))
        db.delete(debt)
        db.commit()


def test_retake_from_dashboard_to_saved_request(s1, new_debt):
    path = f"/requests/new?type=retake&debt_id={new_debt.id}"
    home = s1.get("/").text
    assert "Ближайшая пересдача" in home and "А-101" in home
    assert path.replace("&", "&amp;") in home
    form = s1.get(path).text
    assert f'value="{new_debt.course}"' in form
    data = dict(type_code="retake", debt_id=new_debt.id, csrf=csrf_of(form),
                delivery="electronic", purpose="Произвольная строка", comment="я" * 1001)
    invalid = s1.post("/requests/new", data=data)
    assert invalid.status_code == 422 and f'value="{new_debt.course}"' in invalid.text
    assert f'name="debt_id" value="{new_debt.id}"' in invalid.text
    data["comment"] = ""
    sent = s1.post("/requests/new", data=data, follow_redirects=False)
    url = sent.headers["location"].split("?")[0]
    assert new_debt.course in s1.get(url).text
    assert f'href="{url}">Посмотреть заявку' in s1.get("/").text
    assert s1.post("/requests/new", data=data, follow_redirects=False).headers["location"] == url
    assert s1.get(path, follow_redirects=False).headers["location"] == url


def test_retake_context_is_scoped_to_student_and_type(s1, s2, new_debt):
    for client, kind in [(s2, "retake"), (s1, "topic")]:
        assert client.get(f"/requests/new?type={kind}&debt_id={new_debt.id}").status_code == 404
        token = csrf_of(client.get("/requests/new?type=retake").text)
        assert client.post("/requests/new", data=dict(type_code=kind, debt_id=new_debt.id,
                                                      csrf=token, purpose="Тест")).status_code == 404


def test_retake_dashboard_ready_document(s2):
    home = s2.get("/").text
    match = re.search(r'href="(/requests/\d+)">Открыть готовое направление', home)
    assert match and "Ближайшая пересдача" in home
    assert match[1] + "/document" in s2.get(match[1]).text
    assert s2.get(match[1] + "/document").status_code == 200
