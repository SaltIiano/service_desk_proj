import os
import secrets
from datetime import datetime, timezone
from functools import wraps
import re

from bson import ObjectId
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from flask import (
    Flask, abort, flash, g, redirect, render_template, request, session, url_for
)
from pymongo import MongoClient, DESCENDING
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

MIN_PROGRESS = 0
MAX_PROGRESS = 100
MIN_PASSWORD_LENGTH = 8
EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
ALLOWED_PHONE_CHARS_REGEX = re.compile(r"^\+?[\d\s\-()]+$")

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-this-secret"),
        MONGO_URI=os.getenv("MONGO_URI", "mongodb://localhost:27017/"),
        MONGO_DB=os.getenv("MONGO_DB", "service_desk"),
        FERNET_KEY=os.getenv("FERNET_KEY", ""),
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    if app.config.get("TESTING") and app.config.get("DB") is not None:
        db = app.config["DB"]
        mongo_client = None
    else:
        mongo_client = MongoClient(app.config["MONGO_URI"], serverSelectionTimeoutMS=2000)
        db = mongo_client[app.config["MONGO_DB"]]

    app.extensions["mongo_client"] = mongo_client
    app.extensions["db"] = db

    def encryptor():
        key = app.config.get("FERNET_KEY")
        if not key:
            return None
        try:
            return Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError):
            app.logger.warning("FERNET_KEY is invalid; confidential fields will not be encrypted.")
            return None

    def protect(value):
        if value is None or value == "":
            return ""
        f = encryptor()
        return f.encrypt(str(value).encode()).decode() if f else str(value)

    def reveal(value):
        if not value:
            return ""
        f = encryptor()
        if not f:
            return value
        try:
            return f.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError, TypeError):
            return value

    app.jinja_env.filters["datetime"] = lambda value: (
        value.strftime("%d.%m.%Y %H:%M") if isinstance(value, datetime) else "—"
    )

    def now():
        return datetime.now(timezone.utc)

    def csrf_token():
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_urlsafe(32)
        return session["_csrf_token"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    def is_valid_email(email: str) -> bool:
        if not email:
            return True
        if len(email) > 254:
            return False
        return bool(EMAIL_REGEX.match(email))

    def is_valid_phone(phone: str) -> bool:
        if not phone:
            return True
        if not ALLOWED_PHONE_CHARS_REGEX.match(phone):
            return False
        digits_only = "".join(ch for ch in phone if ch.isdigit())
        return 7 <= len(digits_only) <= 15

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        user_id = session.get("user_id")
        if user_id:
            try:
                g.user = db.users.find_one({"_id": ObjectId(user_id), "active": True})
            except Exception:
                session.clear()
        if request.method == "POST":
            supplied = request.form.get("_csrf_token", "")
            expected = session.get("_csrf_token", "")
            if not expected or not secrets.compare_digest(supplied, expected):
                abort(400, description="Ошибка проверки формы. Обновите страницу и повторите действие.")

    @app.context_processor
    def inject_globals():
        return {"current_user": getattr(g, "user", None)}

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.user:
                flash("Для продолжения необходимо войти в систему.", "warning")
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    def admin_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.user:
                return redirect(url_for("login"))
            if g.user.get("role") != "admin":
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def oid(value):
        try:
            return ObjectId(value)
        except Exception:
            abort(404)

    def get_request_or_404(request_id):
        item = db.tickets.find_one({"_id": oid(request_id)})
        if not item:
            abort(404)
        return item

    def get_client_name(client_id):
        if not client_id:
            return "Не назначен"
        try:
            client = db.clients.find_one({"_id": ObjectId(client_id)})
            return client.get("name", "Неизвестный клиент") if client else "Неизвестный клиент"
        except Exception:
            return "Неизвестный клиент"

    def get_employee_name(employee_id):
        if not employee_id:
            return "Не назначен"
        try:
            employee = db.employees.find_one({"_id": ObjectId(employee_id)})
            return employee.get("name", "Не назначен") if employee else "Не назначен"
        except Exception:
            return "Не назначен"

    def get_equipment_name(equipment_id):
        if not equipment_id:
            return "Не назначено"
        try:
            equipment = db.equipment.find_one({"_id": ObjectId(equipment_id)})
            return equipment.get("name", "Не назначено") if equipment else "Не назначено"
        except Exception:
            return "Не назначено"

    def decorate_ticket(ticket):
        ticket = dict(ticket)
        ticket["id"] = str(ticket["_id"])
        ticket["client_name"] = get_client_name(ticket.get("client_id"))
        ticket["employee_name"] = get_employee_name(ticket.get("employee_id"))
        ticket["equipment_name"] = get_equipment_name(ticket.get("equipment_id"))
        return ticket

    def form_value(name, default=""):
        return request.form.get(name, default).strip()

    def password_is_strong(password: str) -> bool:
        if not isinstance(password, str):
            return False

        has_digit = any(ch.isdigit() for ch in password)
        has_special = any(not ch.isalnum() for ch in password)

        return (
            len(password) >= MIN_PASSWORD_LENGTH
            and has_digit
            and has_special
        )

    app.jinja_env.globals["password_is_strong"] = password_is_strong

    def validate_ticket_fields(title: str, description: str) -> bool:
        if not isinstance(title, str) or not isinstance(description, str):
            return False

        clean_title = title.strip()
        clean_description = description.strip()

        return bool(clean_title) and bool(clean_description)

    app.jinja_env.globals["validate_ticket_fields"] = validate_ticket_fields

    def parse_progress(value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return MIN_PROGRESS

        return max(MIN_PROGRESS, min(MAX_PROGRESS, number))

    app.jinja_env.globals["parse_progress"] = parse_progress


    @app.route("/")
    def index():
        if not g.user:
            return redirect(url_for("login"))
        status = request.args.get("status", "").strip()
        query = request.args.get("q", "").strip()
        filters = {}
        if status in ("Открыта", "В работе", "Закрыта", "Отменена"):
            filters["status"] = status
        if query:
            filters["$or"] = [
                {"title": {"$regex": query, "$options": "i"}},
                {"description": {"$regex": query, "$options": "i"}},
                {"number": {"$regex": query, "$options": "i"}},
            ]
        tickets = [decorate_ticket(t) for t in db.tickets.find(filters).sort("created_at", DESCENDING).limit(500)]
        return render_template("index.html", tickets=tickets, selected_status=status, q=query)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("index"))
        if request.method == "POST":
            username = form_value("username")
            password = request.form.get("password", "")

            # Согласие с политикой конфиденциальности и обработкой ПДн
            agree_privacy = request.form.get("agree_privacy") == "on"
            agree_personal_data = request.form.get("agree_personal_data") == "on"

            if not (agree_privacy and agree_personal_data):
                flash(
                    "Для входа необходимо согласиться с политикой конфиденциальности "
                    "и дать согласие на обработку персональных данных.",
                    "danger",
                )
                return render_template("login.html")

            user = db.users.find_one({"username": username, "active": True})
            if user and check_password_hash(user.get("password_hash", ""), password):
                session.clear()
                session["user_id"] = str(user["_id"])
                session["_csrf_token"] = secrets.token_urlsafe(32)
                flash("Вы успешно вошли в систему.", "success")
                target = request.args.get("next", "")
                if target.startswith("/") and not target.startswith("//"):
                    return redirect(target)
                return redirect(url_for("index"))
            flash("Неверный логин или пароль.", "danger")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        session.clear()
        flash("Вы вышли из системы.", "success")
        return redirect(url_for("login"))

    @app.route("/tickets/new", methods=["GET", "POST"])
    @login_required
    def ticket_new():
        if request.method == "POST":
            title = form_value("title")
            description = form_value("description")
            if not title or not description:
                flash("Заполните тему и описание заявки.", "danger")
            elif len(title) > 160:
                flash("Тема заявки не должна превышать 160 символов.", "danger")
            else:
                last_ticket = next(iter(db.tickets.find().sort("number", DESCENDING).limit(1)), None)
                try:
                    next_number = int(last_ticket.get("number", "0")) + 1 if last_ticket else 1
                except (TypeError, ValueError):
                    next_number = db.tickets.count_documents({}) + 1
                ticket = {
                    "number": f"{next_number:06d}",
                    "title": title,
                    "description": description[:5000],
                    "client_id": form_value("client_id") or None,
                    "employee_id": form_value("employee_id") or None,
                    "equipment_id": form_value("equipment_id") or None,
                    "progress": 0,
                    "status": "Открыта",
                    "created_at": now(),
                    "updated_at": now(),
                    "created_by": str(g.user["_id"]),
                    "completed_at": None,
                }
                db.tickets.insert_one(ticket)
                flash(f"Заявка №{ticket['number']} создана.", "success")
                return redirect(url_for("index"))
        return render_template(
            "ticket_form.html", ticket=None,
            clients=list(db.clients.find().sort("name", 1)),
            employees=list(db.employees.find({"status": "Работает"}).sort("name", 1)),
            equipment=list(db.equipment.find().sort("name", 1)),
            statuses=["Открыта", "В работе", "Закрыта", "Отменена"]
        )

    @app.route("/tickets/<ticket_id>")
    @login_required
    def ticket_detail(ticket_id):
        ticket = decorate_ticket(get_request_or_404(ticket_id))
        return render_template("ticket_detail.html", ticket=ticket)

    @app.route("/tickets/<ticket_id>/edit", methods=["GET", "POST"])
    @login_required
    def ticket_edit(ticket_id):
        ticket = get_request_or_404(ticket_id)
        if request.method == "POST":
            title = form_value("title")
            description = form_value("description")
            status = form_value("status")
            if not title or not description:
                flash("Тема и описание обязательны.", "danger")
            elif len(title) > 160:
                flash("Тема заявки не должна превышать 160 символов.", "danger")
            elif status not in ("Открыта", "В работе", "Закрыта", "Отменена"):
                flash("Недопустимый статус заявки.", "danger")
            else:
                progress = parse_progress(form_value("progress", "0"))
                if status == "Закрыта":
                    progress = 100
                updates = {
                    "title": title,
                    "description": description[:5000],
                    "client_id": form_value("client_id") or None,
                    "employee_id": form_value("employee_id") or None,
                    "equipment_id": form_value("equipment_id") or None,
                    "status": status,
                    "progress": progress,
                    "updated_at": now(),
                    "completed_at": now() if status == "Закрыта" else None,
                }
                db.tickets.update_one({"_id": ticket["_id"]}, {"$set": updates})
                flash("Заявка обновлена.", "success")
                return redirect(url_for("ticket_detail", ticket_id=ticket_id))
        ticket = dict(ticket)
        ticket["id"] = str(ticket["_id"])
        return render_template(
            "ticket_form.html", ticket=ticket,
            clients=list(db.clients.find().sort("name", 1)),
            employees=list(db.employees.find().sort("name", 1)),
            equipment=list(db.equipment.find().sort("name", 1)),
            statuses=["Открыта", "В работе", "Закрыта", "Отменена"]
        )

    @app.route("/tickets/<ticket_id>/delete", methods=["POST"])
    @admin_required
    def ticket_delete(ticket_id):
        ticket = get_request_or_404(ticket_id)
        db.tickets.delete_one({"_id": ticket["_id"]})
        flash(f"Заявка №{ticket.get('number', '')} удалена.", "success")
        return redirect(url_for("index"))

    @app.route("/clients", methods=["GET", "POST"])
    @login_required
    def clients():
        if request.method == "POST":
            if g.user.get("role") != "admin":
                abort(403)
            name, kind = form_value("name"), form_value("type")
            phone, email = form_value("phone"), form_value("email")
            if not name or kind not in ("Частное лицо", "Организация"):
                flash("Укажите имя и корректный тип клиента.", "danger")
            elif not is_valid_phone(phone):
                flash("Введите корректный номер телефона (от 7 до 15 цифр).", "danger")
            elif not is_valid_email(email):
                flash("Введите корректный адрес электронной почты (например, user@example.com).", "danger")
            else:
                db.clients.insert_one({
                    "name": name[:160], "type": kind, "phone": protect(phone),
                    "email": protect(email), "created_at": now()
                })
                flash("Клиент добавлен.", "success")
                return redirect(url_for("clients"))
        rows = []
        for c in db.clients.find().sort("name", 1):
            item = dict(c)
            item["id"] = str(item["_id"])
            item["phone"] = reveal(item.get("phone", ""))
            item["email"] = reveal(item.get("email", ""))
            rows.append(item)
        return render_template("clients.html", clients=rows)

    @app.route("/clients/<client_id>/edit", methods=["GET", "POST"])
    @admin_required
    def client_edit(client_id):
        client = db.clients.find_one({"_id": oid(client_id)})
        if not client:
            abort(404)
        item = dict(client)
        item["id"] = str(item["_id"])
        item["phone"] = reveal(item.get("phone", ""))
        item["email"] = reveal(item.get("email", ""))
        if request.method == "POST":
            name, kind = form_value("name"), form_value("type")
            phone, email = form_value("phone"), form_value("email")
            if not name or kind not in ("Частное лицо", "Организация"):
                flash("Укажите имя и корректный тип клиента.", "danger")
            elif not is_valid_phone(phone):
                flash("Введите корректный номер телефона (от 7 до 15 цифр).", "danger")
            elif not is_valid_email(email):
                flash("Введите корректный адрес электронной почты (например, user@example.com).", "danger")
            else:
                db.clients.update_one({"_id": client["_id"]}, {"$set": {
                    "name": name[:160], "type": kind,
                    "phone": protect(phone),
                    "email": protect(email), "updated_at": now()
                }})
                flash("Данные клиента обновлены.", "success")
                return redirect(url_for("clients"))
        return render_template("client_form.html", client=item)

    @app.route("/clients/<client_id>/delete", methods=["POST"])
    @admin_required
    def client_delete(client_id):
        client = db.clients.find_one({"_id": oid(client_id)})
        if not client:
            abort(404)
        # Remove references before deleting the record so tickets remain valid.
        db.tickets.update_many({"client_id": str(client["_id"])}, {"$set": {"client_id": None}})
        db.clients.delete_one({"_id": client["_id"]})
        flash("Клиент удалён. Его привязки к заявкам сброшены.", "success")
        return redirect(url_for("clients"))

    @app.route("/employees", methods=["GET", "POST"])
    @login_required
    def employees():
        if request.method == "POST":
            if g.user.get("role") != "admin":
                abort(403)
            name, job = form_value("name"), form_value("job")
            status = form_value("status", "Работает")
            if not name or not job or status not in ("Работает", "Отпуск", "Уволен"):
                flash("Заполните имя, должность и допустимый статус.", "danger")
            else:
                try:
                    experience = max(0, int(form_value("experience", "0")))
                except ValueError:
                    experience = 0
                db.employees.insert_one({
                    "name": name[:160], "job": job[:120], "experience": experience,
                    "start_working": form_value("start_working"),
                    "status": status, "created_at": now()
                })
                flash("Сотрудник добавлен.", "success")
                return redirect(url_for("employees"))
        rows = []
        for e in db.employees.find().sort("name", 1):
            e = dict(e)
            e["id"] = str(e["_id"])
            rows.append(e)
        return render_template("employees.html", employees=rows)

    @app.route("/employees/<employee_id>/edit", methods=["GET", "POST"])
    @admin_required
    def employee_edit(employee_id):
        employee = db.employees.find_one({"_id": oid(employee_id)})
        if not employee:
            abort(404)
        item = dict(employee)
        item["id"] = str(item["_id"])
        if request.method == "POST":
            name, job = form_value("name"), form_value("job")
            status = form_value("status")
            try:
                experience = int(form_value("experience", "0"))
            except ValueError:
                experience = -1
            if not name or not job or status not in ("Работает", "Отпуск", "Уволен") or experience < 0:
                flash("Проверьте имя, должность, опыт и статус сотрудника.", "danger")
            else:
                db.employees.update_one({"_id": employee["_id"]}, {"$set": {
                    "name": name[:160], "job": job[:120], "experience": experience,
                    "start_working": form_value("start_working"), "status": status,
                    "updated_at": now()
                }})
                flash("Данные сотрудника обновлены.", "success")
                return redirect(url_for("employees"))
        return render_template("employee_form.html", employee=item)

    @app.route("/employees/<employee_id>/delete", methods=["POST"])
    @admin_required
    def employee_delete(employee_id):
        employee = db.employees.find_one({"_id": oid(employee_id)})
        if not employee:
            abort(404)
        db.tickets.update_many({"employee_id": str(employee["_id"])}, {"$set": {"employee_id": None}})
        db.employees.delete_one({"_id": employee["_id"]})
        flash("Сотрудник удалён. Его привязки к заявкам сброшены.", "success")
        return redirect(url_for("employees"))

    @app.route("/equipment", methods=["GET", "POST"])
    @login_required
    def equipment():
        if request.method == "POST":
            if g.user.get("role") != "admin":
                abort(403)
            name, number = form_value("name"), form_value("number")
            try:
                cost = float(form_value("cost", "0").replace(",", "."))
            except ValueError:
                cost = -1
            if not name or not number or cost < 0:
                flash("Укажите название, инвентарный номер и неотрицательную стоимость.", "danger")
            elif db.equipment.find_one({"number": number}):
                flash("Такой инвентарный номер уже существует.", "danger")
            else:
                db.equipment.insert_one({"name": name[:160], "number": number[:80], "cost": cost, "created_at": now()})
                flash("Оборудование добавлено.", "success")
                return redirect(url_for("equipment"))
        rows = []
        for e in db.equipment.find().sort("name", 1):
            e = dict(e)
            e["id"] = str(e["_id"])
            rows.append(e)
        return render_template("equipment.html", equipment=rows)

    @app.route("/equipment/<equipment_id>/edit", methods=["GET", "POST"])
    @admin_required
    def equipment_edit(equipment_id):
        equipment_item = db.equipment.find_one({"_id": oid(equipment_id)})
        if not equipment_item:
            abort(404)
        item = dict(equipment_item)
        item["id"] = str(item["_id"])
        if request.method == "POST":
            name, number = form_value("name"), form_value("number")
            try:
                cost = float(form_value("cost", "0").replace(",", "."))
            except ValueError:
                cost = -1
            duplicate = db.equipment.find_one({"number": number}) if number else None
            if duplicate and duplicate.get("_id") != equipment_item["_id"]:
                flash("Такой инвентарный номер уже существует.", "danger")
            elif not name or not number or cost < 0:
                flash("Укажите название, инвентарный номер и неотрицательную стоимость.", "danger")
            else:
                db.equipment.update_one({"_id": equipment_item["_id"]}, {"$set": {
                    "name": name[:160], "number": number[:80], "cost": cost, "updated_at": now()
                }})
                flash("Данные оборудования обновлены.", "success")
                return redirect(url_for("equipment"))
        return render_template("equipment_form.html", equipment= item)

    @app.route("/equipment/<equipment_id>/delete", methods=["POST"])
    @admin_required
    def equipment_delete(equipment_id):
        equipment_item = db.equipment.find_one({"_id": oid(equipment_id)})
        if not equipment_item:
            abort(404)
        db.tickets.update_many({"equipment_id": str(equipment_item["_id"])}, {"$set": {"equipment_id": None}})
        db.equipment.delete_one({"_id": equipment_item["_id"]})
        flash("Оборудование удалено. Его привязки к заявкам сброшены.", "success")
        return redirect(url_for("equipment"))

    @app.route("/reports")
    @login_required
    def reports():
        all_tickets = list(db.tickets.find())
        complete = [t for t in all_tickets if t.get("status") == "Закрыта"]
        cancelled = [t for t in all_tickets if t.get("status") == "Отменена"]
        opened = [t for t in all_tickets if t.get("status") in ("Открыта", "В работе")]
        durations = []
        for t in complete:
            start, end = t.get("created_at"), t.get("completed_at")
            if start and end:
                durations.append(max(0, (end - start).total_seconds() / 3600))
        report = {
            "total": len(all_tickets), "complete": len(complete), "opened": len(opened),
            "cancelled": len(cancelled), "employees": db.employees.count_documents({}),
            "clients": db.clients.count_documents({}),
            "average_hours": round(sum(durations) / len(durations), 2) if durations else None,
        }
        return render_template("reports.html", report=report)

    @app.route("/users", methods=["GET", "POST"])
    @admin_required
    def users():
        if request.method == "POST":
            username = form_value("username")
            password = request.form.get("password", "")
            role = form_value("role")
            if len(username) < 3 or not password_is_strong(password) or role not in ("user", "admin"):
                flash("Логин — от 3 символов. Пароль должен содержать минимум 8 символов, цифру и спецсимвол.", "danger")
            elif db.users.find_one({"username": username}):
                flash("Такой логин уже существует.", "danger")
            else:
                db.users.insert_one({
                    "username": username, "password_hash": generate_password_hash(password),
                    "role": role, "active": True, "created_at": now()
                })
                flash("Пользователь создан.", "success")
                return redirect(url_for("users"))
        user_rows = []
        for user in db.users.find({}, {"password_hash": 0}).sort("username", 1):
            user = dict(user)
            user["id"] = str(user["_id"])
            user_rows.append(user)
        return render_template("users.html", users=user_rows)

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("error.html", code=403, message="Недостаточно прав для выполнения действия."), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="Запрашиваемая запись не найдена."), 404

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, message=getattr(error, "description", "Некорректный запрос.")), 400

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
