import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from werkzeug.security import generate_password_hash

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
db = client[os.getenv("MONGO_DB", "service_desk")]

db.users.create_index([("username", ASCENDING)], unique=True)
db.clients.create_index([("name", ASCENDING)])
db.employees.create_index([("name", ASCENDING)])
db.equipment.create_index([("number", ASCENDING)], unique=True)
db.tickets.create_index([("number", ASCENDING)], unique=True)
db.tickets.create_index([("status", ASCENDING), ("created_at", -1)])

username = os.getenv("ADMIN_USERNAME", "admin")
password = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")
if len(password) < 8 or not any(ch.isdigit() for ch in password) or not any(not ch.isalnum() for ch in password):
    raise SystemExit("ADMIN_PASSWORD должен содержать минимум 8 символов, хотя бы одну цифру и один спецсимвол. Проверьте .env.")

if not db.users.find_one({"username": username}):
    db.users.insert_one({
        "username": username,
        "password_hash": generate_password_hash(password),
        "role": "admin",
        "active": True,
        "created_at": datetime.now(timezone.utc),
    })
    print(f"Создан администратор: {username}")
    print("Пароль взят из переменной ADMIN_PASSWORD. Смените его перед реальным использованием.")
else:
    print(f"Пользователь {username} уже существует.")

if db.clients.count_documents({}) == 0:
    db.clients.insert_many([
        {"name": "ООО «Пример»", "type": "Организация", "phone": "", "email": ""},
        {"name": "Иван Петров", "type": "Частное лицо", "phone": "", "email": ""},
    ])
if db.employees.count_documents({}) == 0:
    db.employees.insert_many([
        {"name": "Анна Смирнова", "job": "Системный администратор", "experience": 4, "start_working": "2022-03-01", "status": "Работает"},
        {"name": "Илья Козлов", "job": "Специалист техподдержки", "experience": 2, "start_working": "2024-01-15", "status": "Работает"},
    ])
if db.equipment.count_documents({}) == 0:
    db.equipment.insert_many([
        {"name": "Ноутбук для диагностики", "number": "IT-001", "cost": 65000},
        {"name": "Тестер сетевого кабеля", "number": "IT-002", "cost": 4500},
    ])
if db.tickets.count_documents({}) == 0:
    db.tickets.insert_many([
        {"number": "000001", "title": "Не работает принтер в бухгалтерии", "description": "Проверить подключение и драйверы.", "client_id": None, "employee_id": None, "equipment_id": None, "progress": 0, "status": "Открыта", "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc), "created_by": "seed", "completed_at": None},
        {"number": "000002", "title": "Обновить рабочую станцию", "description": "Установить актуальные обновления ОС.", "client_id": None, "employee_id": None, "equipment_id": None, "progress": 100, "status": "Закрыта", "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc), "created_by": "seed", "completed_at": datetime.now(timezone.utc)},
    ])
print("Готово. Индексы и демонстрационные данные проверены.")
