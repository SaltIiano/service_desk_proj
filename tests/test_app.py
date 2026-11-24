import pytest
from werkzeug.security import generate_password_hash
from app import create_app
from bson import ObjectId


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find_one(self, query, projection=None):
        for row in self.rows:
            if all(str(row.get(k)) == str(v) for k, v in query.items()):
                return row
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        rows = [r for r in self.rows if all(r.get(k) == v for k, v in query.items())]
        return FakeCursor(rows)

    def count_documents(self, query):
        return len(list(self.find(query)))

    def insert_one(self, row):
        row = dict(row)
        row.setdefault("_id", f"id{len(self.rows) + 1}")
        self.rows.append(row)
        return type("Result", (), {"inserted_id": row["_id"]})()

    def insert_many(self, rows):
        for row in rows:
            self.insert_one(row)

    def update_one(self, query, update):
        row = self.find_one(query)
        if row:
            row.update(update.get("$set", {}))
        return type("Result", (), {"modified_count": 1 if row else 0})()

    def update_many(self, query, update):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                row.update(update.get("$set", {}))
        return type("Result", (), {"modified_count": 1})()

    def delete_one(self, query):
        row = self.find_one(query)
        if row:
            self.rows.remove(row)
        return type("Result", (), {"deleted_count": 1 if row else 0})()


class FakeCursor(list):
    def sort(self, *args):
        return self

    def limit(self, n):
        return FakeCursor(self[:n])


class FakeDB:
    def __init__(self):
        self.users = FakeCollection([{
            "_id": "u1", "username": "testadmin",
            "password_hash": generate_password_hash("password123"),
            "role": "admin", "active": True
        }])
        self.tickets = FakeCollection()
        self.clients = FakeCollection()
        self.employees = FakeCollection()
        self.equipment = FakeCollection()

def make_full_db():
    db = FakeDB()
    db.clients.insert_one({"_id": ObjectId(), "name": "Клиент", "type": "Организация",
                           "phone": "", "email": ""})
    db.employees.insert_one({"_id": ObjectId(), "name": "Иван", "job": "Инженер",
                             "experience": 3, "status": "Работает"})
    db.equipment.insert_one({"_id": ObjectId(), "name": "Принтер", "number": "IT-100", "cost": 1000})
    return db

def login(client):
    with client.session_transaction() as session:
        session["_csrf_token"] = "test-csrf"
    return client.post("/login", data={
        "_csrf_token": "test-csrf", "username": "testadmin", "password": "password123"
    }, follow_redirects=True)


def test_login_page_is_available():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": FakeDB()})
    response = app.test_client().get("/login")
    assert response.status_code == 200
    assert "Вход в Service Desk".encode() in response.data


def test_wrong_password_does_not_log_in():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": FakeDB()})
    client = app.test_client()
    with client.session_transaction() as session:
        session["_csrf_token"] = "test-csrf"
    response = client.post("/login", data={
        "_csrf_token": "test-csrf", "username": "testadmin", "password": "wrong-password"
    })
    assert response.status_code == 200
    assert "Неверный логин или пароль".encode() in response.data


def test_unauthenticated_user_is_redirected():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": FakeDB()})
    response = app.test_client().get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_csrf_is_required_for_post():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": FakeDB()})
    response = app.test_client().post("/login", data={"username": "testadmin", "password": "password123"})
    assert response.status_code == 400


def make_authorized_client(role="admin"):
    db = FakeDB()
    user_id = ObjectId()
    db.users.rows[0]["_id"] = user_id
    db.users.rows[0]["role"] = role
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": db})
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = str(user_id)
        session["_csrf_token"] = "test-csrf"
    return app, client, db


def test_password_strength_accepts_valid_password():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": FakeDB()})
    password_is_strong = app.jinja_env.globals["password_is_strong"]
    assert password_is_strong("Abcdef1!") is True


def test_password_strength_rejects_missing_digit_or_symbol_or_length():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": FakeDB()})
    password_is_strong = app.jinja_env.globals["password_is_strong"]
    assert password_is_strong("Abcdefgh!") is False  # нет цифры
    assert password_is_strong("Abcdefg1") is False  # нет спецсимвола
    assert password_is_strong("Ab1!") is False  # меньше 8 символов


def test_regular_user_cannot_open_admin_users_page():
    app, client, db = make_authorized_client(role="user")
    response = client.get("/users")
    assert response.status_code == 403


def test_ticket_creation_rejects_empty_required_fields():
    app, client, db = make_authorized_client()
    response = client.post("/tickets/new", data={
        "_csrf_token": "test-csrf", "title": "", "description": "Описание"
    })
    assert response.status_code == 200
    assert len(db.tickets.rows) == 0
    assert "Заполните тему и описание заявки".encode() in response.data


def test_ticket_fields_validation():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": FakeDB()})
    validate_ticket_fields = app.jinja_env.globals["validate_ticket_fields"]
    assert validate_ticket_fields(
        "Не работает принтер",
        "Принтер не реагирует на команды"
    ) is True

    assert validate_ticket_fields("", "Описание проблемы") is False
    assert validate_ticket_fields("Тема", "") is False
    assert validate_ticket_fields("   ", "Описание") is False
    assert validate_ticket_fields("Тема", "   ") is False


def test_progress_validation():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": FakeDB()})
    parse_progress = app.jinja_env.globals["parse_progress"]
    assert parse_progress("50") == 50
    assert parse_progress("0") == 0
    assert parse_progress("100") == 100
    assert parse_progress("-10") == 0
    assert parse_progress("150") == 100
    assert parse_progress("ошибка") == 0


def test_ticket_creation_saves_ticket_with_initial_status():
    app, client, db = make_authorized_client()
    response = client.post("/tickets/new", data={
        "_csrf_token": "test-csrf",
        "title": "Не работает принтер",
        "description": "Принтер не отвечает на задания печати"
    })
    assert response.status_code == 302
    assert len(db.tickets.rows) == 1
    ticket = db.tickets.rows[0]
    assert ticket["title"] == "Не работает принтер"
    assert ticket["status"] == "Открыта"
    assert ticket["progress"] == 0


def test_ticket_edit_rejects_unknown_status():
    app, client, db = make_authorized_client()
    ticket_id = ObjectId()
    db.tickets.insert_one({
        "_id": ticket_id, "number": "000001", "title": "Старая тема",
        "description": "Описание", "status": "Открыта", "progress": 0
    })
    response = client.post(f"/tickets/{ticket_id}/edit", data={
        "_csrf_token": "test-csrf", "title": "Новая тема",
        "description": "Описание", "status": "Неизвестный статус", "progress": "50"
    })
    assert response.status_code == 200
    saved = db.tickets.find_one({"_id": ticket_id})
    assert saved["status"] == "Открыта"
    assert saved["title"] == "Старая тема"


def test_closed_ticket_is_saved_with_100_percent_progress():
    app, client, db = make_authorized_client()
    ticket_id = ObjectId()
    db.tickets.insert_one({
        "_id": ticket_id, "number": "000001", "title": "Заявка",
        "description": "Описание", "status": "Открыта", "progress": 20
    })
    response = client.post(f"/tickets/{ticket_id}/edit", data={
        "_csrf_token": "test-csrf", "title": "Заявка",
        "description": "Описание", "status": "Закрыта", "progress": "20"
    })
    assert response.status_code == 302
    saved = db.tickets.find_one({"_id": ticket_id})
    assert saved["status"] == "Закрыта"
    assert saved["progress"] == 100

@pytest.mark.parametrize("name, job, status, expected_status, should_create", [
    ("Анна", "Инженер", "Работает", 302, True),
    ("", "Инженер", "Работает", 200, False),
    ("Анна", "", "Работает", 200, False),
    ("Анна", "Инженер", "Уволен", 302, True),
    ("Анна", "Инженер", "Неизвестно", 200, False),
])
def test_employees_create(name, job, status, expected_status, should_create):
    app, client, db = make_authorized_client()
    response = client.post("/employees", data={
        "_csrf_token": "test-csrf", "name": name, "job": job, "status": status,
        "experience": "3", "start_working": "2024-01-01"
    })
    assert response.status_code == expected_status
    assert (len(db.employees.rows) == 1) is should_create


def test_employees_create_bad_experience_uses_zero():
    app, client, db = make_authorized_client()
    response = client.post("/employees", data={
        "_csrf_token": "test-csrf", "name": "Анна", "job": "Инженер",
        "status": "Работает", "experience": "abc"
    })
    assert response.status_code == 302
    assert db.employees.rows[0]["experience"] == 0


def test_employees_list_renders_for_admin():
    app, client, db = make_authorized_client()
    db.employees.insert_one({"_id": ObjectId(), "name": "Анна", "job": "Инженер",
                             "experience": 3, "status": "Работает"})
    r = client.get("/employees")
    assert r.status_code == 200
    assert "Анна".encode() in r.data


def test_employees_create_forbidden_for_regular_user():
    app, client, db = make_authorized_client(role="user")
    r = client.post("/employees", data={"_csrf_token": "test-csrf",
                                        "name": "A", "job": "B", "status": "Работает"})
    assert r.status_code == 403


def test_employee_edit_valid_and_invalid():
    app, client, db = make_authorized_client()
    eid = ObjectId()
    db.employees.insert_one({"_id": eid, "name": "Старое", "job": "Job",
                             "experience": 1, "status": "Работает"})
    assert client.get(f"/employees/{eid}/edit").status_code == 200
    r = client.post(f"/employees/{eid}/edit", data={
        "_csrf_token": "test-csrf", "name": "Новое", "job": "Eng",
        "status": "Отпуск", "experience": "5"
    })
    assert r.status_code == 302
    assert db.employees.find_one({"_id": eid})["name"] == "Новое"
    r = client.post(f"/employees/{eid}/edit", data={
        "_csrf_token": "test-csrf", "name": "X", "job": "Y",
        "status": "Работает", "experience": "-1"
    })
    assert r.status_code == 200
    assert db.employees.find_one({"_id": eid})["name"] == "Новое"


def test_employee_edit_404():
    app, client, db = make_authorized_client()
    r = client.get(f"/employees/{ObjectId()}/edit")
    assert r.status_code == 404


def test_employee_edit_invalid_oid_404():
    app, client, db = make_authorized_client()
    r = client.get("/employees/not-an-objectid/edit")
    assert r.status_code == 404


@pytest.mark.parametrize("name, number, cost, expected, should_create", [
    ("Принтер", "IT-001", "1000", 302, True),
    ("Принтер", "IT-001", "1000,5", 302, True),
    ("", "IT-002", "1000", 200, False),
    ("Принтер", "", "1000", 200, False),
    ("Принтер", "IT-003", "-5", 200, False),
    ("Принтер", "IT-004", "abc", 200, False),
])
def test_equipment_create(name, number, cost, expected, should_create):
    app, client, db = make_authorized_client()
    r = client.post("/equipment", data={
        "_csrf_token": "test-csrf", "name": name, "number": number, "cost": cost
    })
    assert r.status_code == expected
    assert (len(db.equipment.rows) == 1) is should_create


def test_equipment_duplicate_number_rejected():
    app, client, db = make_authorized_client()
    db.equipment.insert_one({"_id": ObjectId(), "name": "X", "number": "IT-001", "cost": 0})
    r = client.post("/equipment", data={
        "_csrf_token": "test-csrf", "name": "Y", "number": "IT-001", "cost": "10"
    })
    assert r.status_code == 200
    assert "уже существует".encode() in r.data
    assert len(db.equipment.rows) == 1


def test_equipment_edit_duplicate_and_ok():
    app, client, db = make_authorized_client()
    eid1, eid2 = ObjectId(), ObjectId()
    db.equipment.insert_one({"_id": eid1, "name": "A", "number": "N1", "cost": 10})
    db.equipment.insert_one({"_id": eid2, "name": "B", "number": "N2", "cost": 20})
    assert client.get(f"/equipment/{eid1}/edit").status_code == 200
    r = client.post(f"/equipment/{eid1}/edit", data={
        "_csrf_token": "test-csrf", "name": "A", "number": "N2", "cost": "10"
    })
    assert r.status_code == 200
    assert "уже существует".encode() in r.data
    r = client.post(f"/equipment/{eid1}/edit", data={
        "_csrf_token": "test-csrf", "name": "A2", "number": "N1", "cost": "100"
    })
    assert r.status_code == 302
    assert db.equipment.find_one({"_id": eid1})["name"] == "A2"


def test_equipment_edit_invalid_data():
    app, client, db = make_authorized_client()
    eid = ObjectId()
    db.equipment.insert_one({"_id": eid, "name": "A", "number": "N1", "cost": 10})
    r = client.post(f"/equipment/{eid}/edit", data={
        "_csrf_token": "test-csrf", "name": "", "number": "N1", "cost": "10"
    })
    assert r.status_code == 200


def test_equipment_delete():
    app, client, db = make_authorized_client()
    eid = ObjectId()
    db.equipment.insert_one({"_id": eid, "name": "A", "number": "N1", "cost": 10})
    tid = ObjectId()
    db.tickets.insert_one({"_id": tid, "number": "1", "title": "t",
                           "description": "d", "status": "Открыта", "progress": 0,
                           "equipment_id": str(eid)})
    r = client.post(f"/equipment/{eid}/delete", data={"_csrf_token": "test-csrf"})
    assert r.status_code == 302
    assert db.equipment.find_one({"_id": eid}) is None
    assert db.tickets.find_one({"_id": tid})["equipment_id"] is None


def test_employee_delete_clears_ticket_reference():
    app, client, db = make_authorized_client()
    eid = ObjectId()
    db.employees.insert_one({"_id": eid, "name": "X", "job": "Y",
                             "status": "Работает", "experience": 0})
    tid = ObjectId()
    db.tickets.insert_one({"_id": tid, "number": "1", "title": "t",
                           "description": "d", "status": "Открыта", "progress": 0,
                           "employee_id": str(eid)})
    r = client.post(f"/employees/{eid}/delete", data={"_csrf_token": "test-csrf"})
    assert r.status_code == 302
    assert db.employees.find_one({"_id": eid}) is None
    assert db.tickets.find_one({"_id": tid})["employee_id"] is None


def test_login_required_redirects_to_login():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": FakeDB()})
    r = app.test_client().get("/tickets/new")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_logout_clears_session_and_redirects():
    app, client, db = make_authorized_client()
    r = client.post("/logout", data={"_csrf_token": "test-csrf"})
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
    assert client.get("/").status_code == 302     # Разлогинен


def test_ticket_detail_and_edit_get():
    app, client, db = make_authorized_client()
    tid = ObjectId()
    db.tickets.insert_one({"_id": tid, "number": "000001", "title": "t",
                           "description": "d", "status": "Открыта", "progress": 0})
    assert client.get(f"/tickets/{tid}").status_code == 200
    assert client.get(f"/tickets/{tid}/edit").status_code == 200


def test_ticket_detail_404():
    app, client, db = make_authorized_client()
    assert client.get(f"/tickets/{ObjectId()}").status_code == 404  # 132


def test_ticket_edit_empty_and_long_title():
    app, client, db = make_authorized_client()
    tid = ObjectId()
    db.tickets.insert_one({"_id": tid, "number": "1", "title": "t",
                           "description": "d", "status": "Открыта", "progress": 0})
    for title in ("", "A" * 161):
        r = client.post(f"/tickets/{tid}/edit", data={
            "_csrf_token": "test-csrf", "title": title,
            "description": "d", "status": "Открыта", "progress": "0"
        })
        assert r.status_code == 200

def test_ticket_delete_admin_only():
    app, client, db = make_authorized_client()
    tid = ObjectId()
    db.tickets.insert_one({"_id": tid, "number": "1", "title": "t",
                           "description": "d", "status": "Открыта", "progress": 0})
    r = client.post(f"/tickets/{tid}/delete", data={"_csrf_token": "test-csrf"})
    assert r.status_code == 302
    assert db.tickets.find_one({"_id": tid}) is None


def test_ticket_delete_forbidden_for_user():
    app, client, db = make_authorized_client(role="user")
    tid = ObjectId()
    db.tickets.insert_one({"_id": tid, "number": "1", "title": "t",
                           "description": "d", "status": "Открыта", "progress": 0})
    assert client.post(f"/tickets/{tid}/delete",
                       data={"_csrf_token": "test-csrf"}).status_code == 403

def test_clients_create_forbidden_for_user():
    app, client, db = make_authorized_client(role="user")
    r = client.post("/clients", data={"_csrf_token": "test-csrf",
                                      "name": "X", "type": "Организация"})
    assert r.status_code == 403                       # 357


def test_clients_create_invalid():
    app, client, db = make_authorized_client()
    r = client.post("/clients", data={"_csrf_token": "test-csrf",
                                      "name": "", "type": "Неизвестный"})
    assert r.status_code == 200                       # 361


def test_client_edit_get_and_post():
    app, client, db = make_authorized_client()
    cid = ObjectId()
    db.clients.insert_one({"_id": cid, "name": "A", "type": "Организация",
                           "phone": "", "email": ""})
    assert client.get(f"/clients/{cid}/edit").status_code == 200
    r = client.post(f"/clients/{cid}/edit", data={
        "_csrf_token": "test-csrf", "name": "Bab", "type": "Частное лицо",
        "phone": "+7-900-532-79-65", "email": "email@x.com"
    })
    assert r.status_code == 302
    r = client.post(f"/clients/{cid}/edit", data={
        "_csrf_token": "test-csrf", "name": "", "type": "Организация"
    })
    assert r.status_code == 200

def test_index_for_authenticated_user():
    app, client, db = make_authorized_client()
    db.tickets.insert_one({"_id": ObjectId(), "number": "000001",
                           "title": "t", "description": "d",
                           "status": "Открыта", "progress": 0})
    assert client.get("/").status_code == 200


def test_ticket_new_get_renders_form():
    app, client, db = make_authorized_client()
    assert client.get("/tickets/new").status_code == 200


def test_login_when_already_logged_in_redirects():
    app, client, db = make_authorized_client()
    r = client.get("/login")
    assert r.status_code == 302
    assert "/" in r.headers["Location"]

def test_broken_user_id_in_session_clears_it():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": FakeDB()})
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = "broken-not-an-objectid"
        s["_csrf_token"] = "x"
    assert c.get("/login").status_code == 200



# Для курсовой
@pytest.mark.parametrize("password, expected", [
    ("Password1!", True),
    ("12345678!", True),
    ("Password!", False),
    ("Password1", False),
    ("Pass1!", False),
    ("", False),
])
def test_password_strength(password, expected):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DB": FakeDB()
    })

    validator = app.jinja_env.globals["password_is_strong"]

    assert validator(password) is expected


@pytest.mark.parametrize("title, description, expected_status, should_save", [
    ("Не работает принтер", "Описание", 302, True),
    ("", "Описание", 200, False),
    ("А" * 161, "Описание", 200, False),
    ("А" * 160, "Описание", 302, True),
])
def test_equivalence_classes_ticket_title(title, description, expected_status, should_save):
    app, client, db = make_authorized_client()
    response = client.post("/tickets/new", data={
        "_csrf_token": "test-csrf", "title": title, "description": description
    })
    assert response.status_code == expected_status
    assert (len(db.tickets.rows) == 1) is should_save


@pytest.mark.parametrize("title_length, expected_status, should_save", [
    (0, 200, False),
    (1, 302, True),
    (159, 302, True),
    (160, 302, True),
    (161, 200, False),
])
def test_boundary_values_ticket_title(title_length, expected_status, should_save):
    app, client, db = make_authorized_client()
    response = client.post("/tickets/new", data={
        "_csrf_token": "test-csrf",
        "title": "А" * title_length,
        "description": "Валидное описание"
    })
    assert response.status_code == expected_status
    assert (len(db.tickets.rows) == 1) is should_save


@pytest.mark.parametrize("status, expected_status_code, should_update", [
    ("Открыта", 302, True),
    ("В работе", 302, True),
    ("Закрыта", 302, True),
    ("Отменена", 302, True),
    ("Ожидание", 200, False),
])
def test_equivalence_classes_ticket_status(status, expected_status_code, should_update):
    app, client, db = make_authorized_client()
    ticket_id = ObjectId()
    db.tickets.insert_one({
        "_id": ticket_id, "number": "000001", "title": "Тест",
        "description": "Тест", "status": "Открыта", "progress": 0
    })

    response = client.post(f"/tickets/{ticket_id}/edit", data={
        "_csrf_token": "test-csrf", "title": "Тест",
        "description": "Тест", "status": status, "progress": "50"
    })

    assert response.status_code == expected_status_code
    saved_ticket = db.tickets.find_one({"_id": ticket_id})

    if should_update:
        assert saved_ticket["status"] == status
    else:
        assert saved_ticket["status"] == "Открыта"
        assert "Недопустимый статус заявки".encode() in response.data


def test_decision_table_access_control():
    # Сценарий 1. Правильные логин/пароль + admin
    db = FakeDB()
    db.users.rows[0]["_id"] = ObjectId()
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DB": db})

    client = app.test_client()
    with client.session_transaction() as session:
        session["_csrf_token"] = "test-csrf"

    resp_login = client.post("/login", data={
        "_csrf_token": "test-csrf", "username": "testadmin", "password": "password123"
    }, follow_redirects=True)

    assert "Вы успешно вошли в систему".encode() in resp_login.data
    # Проверка доступа к админке
    resp_admin = client.get("/users")
    assert resp_admin.status_code == 200

    # Сценарий 2. Правильные логин/пароль + user
    app_user, client_user, db_user = make_authorized_client(role="user")
    resp_admin_user = client_user.get("/users")
    assert resp_admin_user.status_code == 403

    # Сценарий 3 и 4: Неправильные логин/пароль (неважно, есть ли роль admin)
    client_fail = app.test_client()
    with client_fail.session_transaction() as session:
        session["_csrf_token"] = "test-csrf"
    resp_fail_login = client_fail.post("/login", data={
        "_csrf_token": "test-csrf", "username": "testadmin", "password": "wrongpassword"
    })
    assert resp_fail_login.status_code == 200
    assert "Неверный логин или пароль".encode() in resp_fail_login.data

    # Попытка зайти в админку без сессии
    resp_no_auth = client_fail.get("/users")
    assert resp_no_auth.status_code == 302
    assert "/login" in resp_no_auth.headers["Location"]
