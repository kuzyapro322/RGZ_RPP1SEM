# Импорты
# os — для общей части: чтения настроек Docker-окружения и скрытых ключей
# re — для пункта 2.1.2: нормализации и проверки логина
# datetime.date — для пункта 2.1.3: сохранения даты операции
# decimal.Decimal — для пунктов 2.1.4 и 7: аккуратной работы с денежными суммами и курсами
# functools.wraps — для общей части: декоратора проверки авторизации
# flask — для общей части: маршрутов, шаблонов, сессий и HTTP-ответов
# flask_sqlalchemy.SQLAlchemy — для пункта 2.1.1: работы с PostgreSQL через ORM
# requests — для пункта 2.1.4: обращения к внешнему сервису курса валют
# sqlalchemy.text — для пункта 2.1.1: добавления ограничений на уровне PostgreSQL
# werkzeug.security — для пункта 2.1.2: хэширования и проверки пароля

import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

import requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash


# ==============================
# Пункт 2.1.1 — Настройка Flask и PostgreSQL
# ==============================

app = Flask(__name__)
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY должен быть задан в .env или переменных окружения.")

app.config["SECRET_KEY"] = secret_key
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://finance_user:finance_password@localhost:5432/finance_db",
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# db — объект для работы с PostgreSQL: через него объявляются таблицы и выполняются запросы.
db = SQLAlchemy(app)


# ==============================
# Пункт 2.1.2 — Правила логина и пароля
# ==============================

LOGIN_PATTERN = re.compile(r"^[a-z0-9_]{3,80}$")


def normalize_login(value):
    # Логин приводим к единому виду: убираем пробелы по краям и переводим в нижний регистр.
    return str(value or "").strip().lower()


def validate_login(name):
    # В БД не попадут разные варианты одного логина вроде Ivan, ivan и " ivan ".
    if not LOGIN_PATTERN.fullmatch(name):
        return "Логин должен содержать 3-80 символов: латинские буквы, цифры или нижнее подчеркивание."
    return None


def validate_password(password):
    # Минимальная сложность пароля: не короче 6 символов, есть буква и цифра.
    if len(password) < 6:
        return "Пароль должен быть не короче 6 символов."
    if not any(char.isalpha() for char in password):
        return "Пароль должен содержать хотя бы одну букву."
    if not any(char.isdigit() for char in password):
        return "Пароль должен содержать хотя бы одну цифру."
    return None


# ==============================
# Пункт 2.1.1 — Модель пользователя
# ==============================

class User(db.Model):
    # Таблица users хранит учетные записи пользователей приложения.
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    operations = db.relationship(
        "Operation",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# ==============================
# Пункт 2.1.1 — Модель финансовой операции
# ==============================

class Operation(db.Model):
    # Таблица operations хранит доходные и расходные операции конкретного пользователя.
    __tablename__ = "operations"
    __table_args__ = (
        db.CheckConstraint(
            "type_operation IN ('income', 'expense')",
            name="operation_type_check",
        ),
        db.CheckConstraint('"sum" > 0', name="operation_sum_positive_check"),
    )

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    sum = db.Column(db.Numeric(12, 2), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type_operation = db.Column(db.String(20), nullable=False)

    user = db.relationship("User", back_populates="operations")


# ==============================
# Общая часть — Создание таблиц и правил БД при старте
# ==============================

def ensure_database_rules():
    # db.create_all() создает таблицы, но не меняет старые ограничения в уже созданной БД.
    # Поэтому отдельно добавляем CHECK и ON DELETE CASCADE, чтобы правила были именно в PostgreSQL.
    db.session.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'operation_type_check'
            ) THEN
                ALTER TABLE operations
                ADD CONSTRAINT operation_type_check
                CHECK (type_operation IN ('income', 'expense'));
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'operation_sum_positive_check'
            ) THEN
                ALTER TABLE operations
                ADD CONSTRAINT operation_sum_positive_check
                CHECK ("sum" > 0);
            END IF;
        END $$;
    """))

    db.session.execute(text("""
        DO $$
        DECLARE
            fk_name text;
            delete_rule char;
        BEGIN
            SELECT conname, confdeltype
            INTO fk_name, delete_rule
            FROM pg_constraint
            WHERE conrelid = 'operations'::regclass
              AND confrelid = 'users'::regclass
              AND contype = 'f'
            LIMIT 1;

            IF fk_name IS NULL THEN
                ALTER TABLE operations
                ADD CONSTRAINT operations_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            ELSIF delete_rule <> 'c' THEN
                EXECUTE format('ALTER TABLE operations DROP CONSTRAINT %I', fk_name);
                ALTER TABLE operations
                ADD CONSTRAINT operations_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END $$;
    """))
    db.session.commit()


with app.app_context():
    # При старте контейнера создаем таблицы и проверяем ограничения в PostgreSQL.
    db.create_all()
    ensure_database_rules()


# ==============================
# Общая часть — Проверка авторизации
# ==============================

def login_required(handler):
    # Декоратор защищает страницы: без user_id в session пользователь отправляется на вход.
    @wraps(handler)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return handler(*args, **kwargs)

    return wrapper


# ==============================
# Общая часть — Получение текущего пользователя
# ==============================

def current_user():
    # Текущий пользователь определяется по user_id, который был записан в session при входе.
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


# ==============================
# Пункт 2.1.4 — Получение курса валют
# ==============================

def get_currency_rate(currency):
    # RUB не нужно запрашивать во внешнем сервисе, потому что операции хранятся в рублях.
    if currency == "RUB":
        return Decimal("1")

    # Для USD/EUR отправляем HTTP-запрос в отдельный Flask-сервис rate_service.
    rate_service_url = os.getenv("RATE_SERVICE_URL", "http://localhost:5001/rate")
    response = requests.get(rate_service_url, params={"currency": currency}, timeout=5)
    response.raise_for_status()

    data = response.json()
    try:
        rate = Decimal(str(data["rate"]))
    except (KeyError, InvalidOperation):
        raise ValueError("Внешний сервис вернул некорректный курс валюты.")

    if rate <= 0:
        raise ValueError("Курс валюты должен быть больше 0.")

    return rate


# ==============================
# Пункт 2.1.4 — Конвертация суммы из рублей
# ==============================

def convert_from_rub(value, rate):
    # Делим рублевую сумму на курс и округляем как денежное значение до копеек.
    converted = Decimal(value) / rate
    return converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ==============================
# Общая часть — Главная страница
# ==============================

@app.get("/")
def index():
    # Главная страница только перенаправляет пользователя в нужный раздел.
    if current_user():
        return redirect(url_for("operations"))
    return redirect(url_for("login"))


# ==============================
# Пункт 2.1.2 — Регистрация пользователя
# ==============================

@app.route("/reg", methods=["GET", "POST"])
def register():
    # GET показывает форму регистрации, POST создает пользователя.
    if request.method == "GET":
        return render_template("register.html")

    # Поддерживаем и JSON-запрос из задания, и обычную HTML-форму из Jinja-шаблона.
    data = request.get_json(silent=True) or request.form
    name = normalize_login(data.get("name", ""))
    password = str(data.get("password", "")).strip()

    if not name or not password:
        message = "Введите логин и пароль."
        if request.is_json:
            return jsonify({"message": message}), 400
        return render_template("register.html", error=message), 400

    validation_error = validate_login(name) or validate_password(password)
    if validation_error:
        if request.is_json:
            return jsonify({"message": validation_error}), 400
        return render_template("register.html", error=validation_error), 400

    existing_user = User.query.filter_by(name=name).first()
    if existing_user:
        message = "Пользователь с таким логином уже зарегистрирован."
        if request.is_json:
            return jsonify({"message": message}), 409
        return render_template("register.html", error=message), 409

    # Пароль сохраняется не открытым текстом, а только как безопасный хэш.
    user = User(name=name, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()

    session["user_id"] = user.id
    if request.is_json:
        return jsonify({"message": "Пользователь зарегистрирован", "user_id": user.id}), 201
    return redirect(url_for("operations"))


# ==============================
# Общая часть — Авторизация пользователя
# ==============================

@app.route("/login", methods=["GET", "POST"])
def login():
    # GET показывает форму входа, POST проверяет логин и пароль.
    if request.method == "GET":
        return render_template("login.html")

    name = normalize_login(request.form.get("name", ""))
    password = request.form.get("password", "").strip()
    user = User.query.filter_by(name=name).first()

    # check_password_hash сравнивает введенный пароль с хэшем из базы данных.
    if not user or not check_password_hash(user.password_hash, password):
        return render_template("login.html", error="Неверный логин или пароль."), 401

    session["user_id"] = user.id
    return redirect(url_for("operations"))


# ==============================
# Общая часть — Выход из аккаунта
# ==============================

@app.post("/logout")
def logout():
    # Очистка session удаляет признак авторизации пользователя.
    session.clear()
    return redirect(url_for("login"))


# ==============================
# Пункт 2.1.3 — Добавление новой операции
# ==============================

@app.route("/add_operation", methods=["GET", "POST"])
@login_required
def add_operation():
    # GET показывает форму добавления операции, POST сохраняет операцию в PostgreSQL.
    if request.method == "GET":
        return render_template("add_operation.html")

    data = request.get_json(silent=True) or request.form
    user = current_user()

    try:
        # Приводим данные формы к нужным типам: строка типа, Decimal для суммы, date для даты.
        operation_type = str(data.get("type_operation", "")).strip().lower()
        operation_sum = Decimal(str(data.get("sum", "0"))).quantize(Decimal("0.01"))
        operation_date = date.fromisoformat(str(data.get("date", "")))

        if operation_type not in ("income", "expense"):
            raise ValueError("Некорректный тип операции.")
        if operation_sum <= 0:
            raise ValueError("Сумма должна быть больше 0.")

        # В БД сохраняется рублевая сумма; конвертация выполняется позже при просмотре.
        operation = Operation(
            date=operation_date,
            sum=operation_sum,
            user_id=user.id,
            type_operation=operation_type,
        )
        db.session.add(operation)
        db.session.commit()

        if request.is_json:
            return jsonify({"message": "Операция добавлена", "operation_id": operation.id}), 200
        return render_template("add_operation.html", success="Операция успешно добавлена.")
    except Exception as error:
        # Если при сохранении возникла ошибка, откатываем транзакцию, чтобы не оставить мусор в БД.
        db.session.rollback()
        if request.is_json:
            return jsonify({"message": str(error)}), 400
        return render_template("add_operation.html", error=str(error)), 400


# ==============================
# Пункт 2.1.4 — Просмотр операций пользователя
# ==============================

@app.get("/operations")
@login_required
def operations():
    try:
        # Валюта приходит из query-параметра: /operations?currency=USD.
        currency = request.args.get("currency", "RUB").upper()
        if currency not in ("RUB", "USD", "EUR"):
            return render_template("operations.html", error="Неизвестная валюта."), 400

        # Получаем только операции текущего пользователя, чужие операции не показываются.
        rate = get_currency_rate(currency)
        user_operations = (
            Operation.query
            .filter_by(user_id=session["user_id"])
            .order_by(Operation.date.desc(), Operation.id.desc())
            .all()
        )

        # Готовим список для шаблона: рядом с рублевой суммой добавляем сумму в выбранной валюте.
        converted_operations = []
        for operation in user_operations:
            converted_operations.append({
                "id": operation.id,
                "date": operation.date,
                "type_operation": operation.type_operation,
                "sum_rub": operation.sum,
                "converted_sum": convert_from_rub(operation.sum, rate),
            })

        return render_template(
            "operations.html",
            operations=converted_operations,
            currency=currency,
            user=current_user(),
        )
    except Exception:
        return render_template("operations.html", error="Ошибка при получении операций."), 500


# ==============================
# Вариант 7 — Удаление операции
# ==============================

@app.post("/delete_operation/<int:operation_id>")
@login_required
def delete_operation(operation_id):
    # Вариант 7: ищем операцию по id и одновременно проверяем, что она принадлежит текущему пользователю.
    operation = Operation.query.filter_by(
        id=operation_id,
        user_id=session["user_id"],
    ).first()

    if not operation:
        return render_template(
            "operations.html",
            error="Операция не найдена или не принадлежит текущему пользователю.",
        ), 404

    # Если запись найдена, удаляем ее из PostgreSQL и сохраняем изменение.
    db.session.delete(operation)
    db.session.commit()

    return redirect(url_for("operations", deleted="1"))


# ==============================
# Общая часть — Запуск приложения
# ==============================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
