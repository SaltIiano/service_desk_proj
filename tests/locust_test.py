import os
import re
import uuid

from dotenv import load_dotenv
from locust import HttpUser, task, between
from locust.exception import StopUser


load_dotenv()

USERNAME = os.getenv("LOAD_USERNAME")
PASSWORD = os.getenv("LOAD_PASSWORD")


class ServiceDeskUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        if not USERNAME or not PASSWORD:
            raise StopUser()

        response = self.client.get("/login")

        csrf_match = re.search(
            r'name="_csrf_token"\s+value="([^"]+)"',
            response.text
        )

        if not csrf_match:
            raise StopUser()

        csrf_token = csrf_match.group(1)

        with self.client.post(
                "/login",
                data={
                    "_csrf_token": csrf_token,
                    "username": USERNAME,
                    "password": PASSWORD
                },
                catch_response=True,
                name="/login [авторизация]"
        ) as login_response:

            if (
                    login_response.status_code != 200
                    or "/login" in login_response.url
            ):
                print("\n=== ОШИБКА АВТОРИЗАЦИИ ===")
                print(f"Статус: {login_response.status_code}")
                print(f"URL: {login_response.url}")
                print(f"Ответ сервера: {login_response.text[:600]}")

                login_response.failure(
                    "Не удалось авторизоваться"
                )
                raise StopUser()

            if "topnav" not in login_response.text:
                login_response.failure(
                    "После входа не отображается навигация"
                )
                raise StopUser()

            login_response.success()

    @task(70)
    def view_tickets(self):
        with self.client.get(
                "/",
                name="/ [список заявок]",
                catch_response=True
        ) as response:

            if response.status_code != 200:
                response.failure(
                    f"Ошибка загрузки списка: {response.status_code}"
                )

            elif "Заявки" not in response.text:
                response.failure(
                    "Страница не содержит списка заявок"
                )

            else:
                response.success()

    @task(15)
    def view_ticket_details(self):
        response = self.client.get("/")
        match = re.search(
            r'href="/tickets/([a-f0-9]{24})"',
            response.text
        )

        if not match:
            return

        ticket_id = match.group(1)

        self.client.get(
            f"/tickets/{ticket_id}",
            name="/tickets/[id] [карточка заявки]"
        )

    @task(10)
    def view_reports(self):
        with self.client.get(
                "/reports",
                name="/reports [отчётность]",
                catch_response=True
        ) as response:

            if response.status_code != 200:
                response.failure(
                    f"Ошибка отчётности: {response.status_code}"
                )

            elif "Отчётность" not in response.text:
                response.failure(
                    "Страница отчётности не загрузилась"
                )

            else:
                response.success()

    @task(5)
    def create_ticket(self):
        response = self.client.get("/tickets/new")
        csrf_match = re.search(
            r'name="_csrf_token"\s+value="([^"]+)"',
            response.text
        )

        if not csrf_match:
            return

        csrf_token = csrf_match.group(1)

        unique_id = uuid.uuid4().hex[:8]

        with self.client.post(
                "/tickets/new",
                data={
                    "_csrf_token": csrf_token,
                    "title": f"Нагрузочная заявка {unique_id}",
                    "description": (
                            "Заявка создана автоматически "
                            "во время нагрузочного тестирования."
                    )
                },
                name="/tickets/new [создание заявки]",
                catch_response=True
        ) as create_response:

            if create_response.status_code != 200:
                create_response.failure(
                    f"Ошибка создания заявки: "
                    f"{create_response.status_code}"
                )

            elif "создана" not in create_response.text:
                create_response.failure(
                    "Не получено подтверждение создания заявки"
                )

            else:
                create_response.success()
