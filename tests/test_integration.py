import uuid
import pytest
from bson import ObjectId
from cryptography.fernet import Fernet
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from app import create_app


@pytest.fixture
def mongo_database():
    client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
    try:
        client.admin.command("ping")
    except Exception as error:
        client.close()
        pytest.skip(f"MongoDB Community Server недоступен: {error}")

    database_name = "service_desk_test_" + uuid.uuid4().hex
    db = client[database_name]
    yield db
    client.drop_database(database_name)
    client.close()


@pytest.fixture
def app_client(mongo_database):
    user_id = ObjectId()
    mongo_database.users.insert_one({
        "_id": user_id,
        "username": "integration_admin",
        "password_hash": generate_password_hash("Testpass1!"),
        "role": "admin",
        "active": True,
    })
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "integration-test-secret",
        "DB": mongo_database,
        "FERNET_KEY": Fernet.generate_key().decode(),
    })
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = str(user_id)
        session["_csrf_token"] = "integration-csrf"
    return app, client, mongo_database


def test_ticket_is_written_to_mongodb(app_client):
    app, client, db = app_client
    response = client.post("/tickets/new", data={
        "_csrf_token": "integration-csrf",
        "title": "Проверка интеграции",
        "description": "Заявка должна сохраниться в MongoDB",
    })
    assert response.status_code == 302

    ticket = db.tickets.find_one({"title": "Проверка интеграции"})
    assert ticket is not None
    assert ticket["status"] == "Открыта"
    assert ticket["progress"] == 0


def test_client_contacts_are_encrypted_in_database_and_decrypted_in_page(app_client):
    app, client, db = app_client
    response = client.post("/clients", data={
        "_csrf_token": "integration-csrf",
        "name": "Тестовая организация",
        "type": "Организация",
        "phone": "+7 900 000-00-00",
        "email": "test@example.com",
    })
    assert response.status_code == 302

    stored = db.clients.find_one({"name": "Тестовая организация"})
    assert stored is not None
    assert stored["phone"] != "+7 900 000-00-00"
    assert stored["email"] != "test@example.com"

    page = client.get("/clients")
    assert page.status_code == 200
    assert "+7 900 000-00-00".encode() in page.data
    assert b"test@example.com" in page.data


def test_deleting_client_clears_ticket_reference_but_keeps_ticket(app_client):
    app, client, db = app_client
    client_id = ObjectId()
    db.clients.insert_one({"_id": client_id, "name": "Клиент для удаления", "type": "Организация"})
    ticket_id = ObjectId()
    db.tickets.insert_one({
        "_id": ticket_id,
        "number": "000001",
        "title": "Связанная заявка",
        "description": "Проверка внешней ссылки",
        "client_id": str(client_id),
        "status": "Открыта",
        "progress": 0,
    })

    response = client.post(f"/clients/{client_id}/delete", data={
        "_csrf_token": "integration-csrf"
    })
    assert response.status_code == 302
    assert db.clients.find_one({"_id": client_id}) is None

    saved_ticket = db.tickets.find_one({"_id": ticket_id})
    assert saved_ticket is not None
    assert saved_ticket["client_id"] is None