import os
import uuid

import pytest
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import WebDriverException

load_dotenv()

BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:5000")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")


# Хелпер для ожидания
def wait_for_body_text(driver, text, timeout=15):
    def _predicate(d):
        try:
            return text in d.find_element(By.TAG_NAME, "body").text
        except WebDriverException:
            return False
    WebDriverWait(driver, timeout).until(_predicate)


@pytest.fixture
def browser():
    options = webdriver.ChromeOptions()

    if os.getenv("E2E_HEADLESS", "0") == "1":
        options.add_argument("--headless=new")

    options.add_argument("--window-size=1440,1000")

    driver = webdriver.Chrome(options=options)
    driver.implicitly_wait(3)

    yield driver

    driver.quit()

# Логин как админ
def login_as_admin(driver):
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        pytest.skip("В .env не заданы ADMIN_USERNAME и ADMIN_PASSWORD")

    driver.get(BASE_URL + "/login")

    driver.find_element(By.NAME, "username").send_keys(ADMIN_USERNAME)
    driver.find_element(By.NAME, "password").send_keys(ADMIN_PASSWORD)

    # Отмечаем обязательные чекбоксы согласия
    for name in ("agree_privacy", "agree_personal_data"):
        cb = driver.find_element(By.NAME, name)
        if not cb.is_selected():
            cb.click()

    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".topnav"))
    )

# Тест 1. Логин с правильным паролем
def test_e2e_login_success(browser):
    login_as_admin(browser)

    assert "/login" not in browser.current_url
    assert "Заявки" in browser.page_source

# Тест 2. Логин с неправильным паролем
def test_e2e_login_wrong_password(browser):
    browser.get(BASE_URL + "/login")

    browser.find_element(By.NAME, "username").send_keys(
        ADMIN_USERNAME or "unknown_user"
    )
    browser.find_element(By.NAME, "password").send_keys(
        "WrongPassword123!"
    )
    browser.find_element(
        By.CSS_SELECTOR, "button[type='submit']"
    ).click()

    error = WebDriverWait(browser, 10).until(
        EC.visibility_of_element_located(
            (By.CSS_SELECTOR, ".flash.danger")
        )
    )

    assert "Неверный логин или пароль" in error.text
    assert "/login" in browser.current_url


# Тест 3. Создаем тикет
def test_e2e_create_ticket(browser):
    login_as_admin(browser)
    browser.get(BASE_URL + "/tickets/new")

    unique_title = "E2E проверка " + uuid.uuid4().hex[:8]
    description = "Автоматизированная проверка создания заявки."

    title_input = browser.find_element(By.NAME, "title")
    title_input.send_keys(unique_title)

    browser.find_element(
        By.NAME, "description"
    ).send_keys(description)

    form = title_input.find_element(
        By.XPATH, "./ancestor::form[1]"
    )

    submit_button = form.find_element(
        By.CSS_SELECTOR, "button[type='submit']"
    )
    submit_button.click()

    wait_for_body_text(browser, unique_title)

    assert unique_title in browser.page_source

# Тест 4. Ищем тикет
def test_e2e_ticket_search(browser):
    login_as_admin(browser)
    browser.get(BASE_URL + "/tickets/new")
    unique_title = "Поиск E2E " + uuid.uuid4().hex[:8]

    title_input = browser.find_element(By.NAME, "title")
    title_input.send_keys(unique_title)

    browser.find_element(
        By.NAME, "description"
    ).send_keys("Заявка для проверки поиска.")

    create_form = title_input.find_element(
        By.XPATH, "./ancestor::form[1]"
    )

    create_form.find_element(
        By.CSS_SELECTOR, "button[type='submit']"
    ).click()

    WebDriverWait(browser, 15).until(
        EC.text_to_be_present_in_element(
            (By.TAG_NAME, "body"),
            unique_title
        )
    )

    search_input = browser.find_element(By.NAME, "q")
    search_input.clear()
    search_input.send_keys(unique_title)
    search_form = search_input.find_element(
        By.XPATH, "./ancestor::form[1]"
    )

    search_form.find_element(
        By.CSS_SELECTOR, "button[type='submit']"
    ).click()

    WebDriverWait(browser, 15).until(
        EC.text_to_be_present_in_element(
            (By.TAG_NAME, "body"),
            unique_title
        )
    )

    assert unique_title in browser.page_source


# Тест 5. Заходим на страницу с отчетами
def test_e2e_reports_page(browser):
    login_as_admin(browser)

    browser.get(BASE_URL + "/reports")

    heading = WebDriverWait(browser, 10).until(
        EC.presence_of_element_located((By.TAG_NAME, "h1"))
    )

    assert "Отчётность" in heading.text
    assert "Всего заявок" in browser.page_source

# Тест 6. Создание клиента
def test_e2e_create_client(browser):
    login_as_admin(browser)
    browser.get(BASE_URL + "/clients")

    unique_name = "E2E Клиент " + uuid.uuid4().hex[:8]

    name_input = browser.find_element(By.NAME, "name")
    name_input.send_keys(unique_name)

    form = name_input.find_element(By.XPATH, "./ancestor::form[1]")
    form.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    WebDriverWait(browser, 10).until(
        EC.text_to_be_present_in_element((By.TAG_NAME, "body"), unique_name)
    )
    assert unique_name in browser.page_source


# Тест 7. Создание сотрудника
def test_e2e_create_employee(browser):
    login_as_admin(browser)
    browser.get(BASE_URL + "/employees")

    unique_name = "E2E Сотрудник " + uuid.uuid4().hex[:8]

    name_input = browser.find_element(By.NAME, "name")
    name_input.send_keys(unique_name)
    browser.find_element(By.NAME, "job").send_keys("Тестировщик")

    form = name_input.find_element(By.XPATH, "./ancestor::form[1]")
    form.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    WebDriverWait(browser, 10).until(
        EC.text_to_be_present_in_element((By.TAG_NAME, "body"), unique_name)
    )
    assert unique_name in browser.page_source


# Тест 8. Создание оборудования
def test_e2e_create_equipment(browser):
    login_as_admin(browser)
    browser.get(BASE_URL + "/equipment")

    unique_name = "E2E Оборудование " + uuid.uuid4().hex[:8]
    unique_number = "INV-" + uuid.uuid4().hex[:8].upper()

    name_input = browser.find_element(By.NAME, "name")
    name_input.send_keys(unique_name)
    browser.find_element(By.NAME, "number").send_keys(unique_number)
    browser.find_element(By.NAME, "cost").send_keys("1234.56")

    form = name_input.find_element(By.XPATH, "./ancestor::form[1]")
    form.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    WebDriverWait(browser, 10).until(
        EC.text_to_be_present_in_element((By.TAG_NAME, "body"), unique_name)
    )
    assert unique_name in browser.page_source
    assert unique_number in browser.page_source


# Тест 9. Создание пользователя
def test_e2e_create_user(browser):
    login_as_admin(browser)
    browser.get(BASE_URL + "/users")

    unique_username = "e2euser_" + uuid.uuid4().hex[:8]
    password = "TestPass123!"

    username_input = browser.find_element(By.NAME, "username")
    username_input.send_keys(unique_username)
    browser.find_element(By.NAME, "password").send_keys(password)

    form = username_input.find_element(By.XPATH, "./ancestor::form[1]")
    form.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    WebDriverWait(browser, 10).until(
        EC.text_to_be_present_in_element((By.TAG_NAME, "body"), unique_username)
    )
    assert unique_username in browser.page_source

# Тест 10. Фильтрация заявок по статусу
def test_e2e_filter_tickets_by_status(browser):
    login_as_admin(browser)

    browser.get(BASE_URL + "/tickets/new")
    unique_title = "E2E фильтр " + uuid.uuid4().hex[:8]

    title_input = browser.find_element(By.NAME, "title")
    title_input.send_keys(unique_title)
    browser.find_element(By.NAME, "description").send_keys(
        "Заявка для проверки фильтра."
    )

    form = title_input.find_element(By.XPATH, "./ancestor::form[1]")
    form.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    wait_for_body_text(browser, unique_title)

    browser.get(BASE_URL + "/")

    q_input = browser.find_element(By.NAME, "q")
    q_input.clear()
    q_input.send_keys(unique_title)

    status_select = Select(browser.find_element(By.NAME, "status"))
    status_select.select_by_visible_text("Открыта")

    browser.find_element(
        By.CSS_SELECTOR, "form.filters button[type='submit']"
    ).click()

    wait_for_body_text(browser, unique_title)
    assert unique_title in browser.page_source


# Тест 11. Просмотр деталей заявки
def test_e2e_ticket_detail_view(browser):
    login_as_admin(browser)

    browser.get(BASE_URL + "/tickets/new")
    unique_title = "E2E детали " + uuid.uuid4().hex[:8]
    description = "Описание для проверки деталей заявки."

    title_input = browser.find_element(By.NAME, "title")
    title_input.send_keys(unique_title)
    browser.find_element(By.NAME, "description").send_keys(description)

    form = title_input.find_element(By.XPATH, "./ancestor::form[1]")
    form.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    wait_for_body_text(browser, unique_title)

    browser.find_element(By.LINK_TEXT, unique_title).click()

    WebDriverWait(browser, 10).until(
        EC.presence_of_element_located((By.TAG_NAME, "h2"))
    )

    h2 = browser.find_element(By.TAG_NAME, "h2")
    assert unique_title in h2.text
    assert description in browser.page_source


# Тест 12. Выход из системы
def test_e2e_logout(browser):
    login_as_admin(browser)

    browser.find_element(
        By.CSS_SELECTOR, "form[action*='/logout'] button"
    ).click()

    WebDriverWait(browser, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".flash.success"))
    )

    assert "Вы вышли из системы" in browser.page_source
    assert "/login" in browser.current_url

# Тест 13. Нельзя войти без согласия
def test_e2e_login_blocked_without_consent(browser):
    browser.get(BASE_URL + "/login")

    browser.find_element(By.NAME, "username").send_keys(
        ADMIN_USERNAME or "unknown_user"
    )
    browser.find_element(By.NAME, "password").send_keys(
        ADMIN_PASSWORD or "WrongPassword123!"
    )

    privacy = browser.find_element(By.NAME, "agree_privacy")
    personal = browser.find_element(By.NAME, "agree_personal_data")

    assert not privacy.is_selected()
    assert not personal.is_selected()

    # Оба чекбокса обязательны
    browser.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    # Остаёмся на странице логина, в систему не попали
    assert "/login" in browser.current_url
    assert "Заявки" not in browser.page_source
    assert ".topnav" not in browser.page_source

# Тест 14. Успешный вход после отметки обоих чекбоксов
def test_e2e_login_with_consent(browser):
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        pytest.skip("В .env не заданы ADMIN_USERNAME и ADMIN_PASSWORD")

    browser.get(BASE_URL + "/login")

    browser.find_element(By.NAME, "username").send_keys(ADMIN_USERNAME)
    browser.find_element(By.NAME, "password").send_keys(ADMIN_PASSWORD)

    # Отмечаем только первый чекбокс — форма всё ещё не должна отправиться
    privacy = browser.find_element(By.NAME, "agree_privacy")
    if not privacy.is_selected():
        privacy.click()

    browser.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    assert "/login" in browser.current_url
    assert "Заявки" not in browser.page_source

    # Отмечаем второй чекбокс — теперь вход должен пройти
    personal = browser.find_element(By.NAME, "agree_personal_data")
    if not personal.is_selected():
        personal.click()

    browser.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    WebDriverWait(browser, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".topnav"))
    )

    assert "/login" not in browser.current_url
    assert "Заявки" in browser.page_source