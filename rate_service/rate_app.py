# Импорты
# os — для пункта 2.3: чтения статических курсов из .env/Docker-окружения
# decimal.Decimal — для пункта 2.3: проверки, что курс является положительным числом
# flask — для пункта 2.3: реализации внешнего сервиса курса валют

import os
from decimal import Decimal, InvalidOperation

from flask import Flask, jsonify, request


# ==============================
# Пункт 2.3 — Создание внешнего сервиса
# ==============================

app = Flask(__name__)


# ==============================
# Пункт 2.3 — Статические курсы валют
# ==============================

def load_rate(env_name, default_value):
    # Курс можно менять через .env, но сервис все равно проверяет, что это число больше 0.
    try:
        rate = Decimal(os.getenv(env_name, default_value))
    except InvalidOperation:
        raise RuntimeError(f"{env_name} должен быть числом.")

    if rate <= 0:
        raise RuntimeError(f"{env_name} должен быть больше 0.")

    return rate


RATES = {
    # В учебном задании курс разрешено задать статически.
    "USD": load_rate("USD_RATE", "90.50"),
    "EUR": load_rate("EUR_RATE", "98.20"),
}


# ==============================
# Пункт 2.3 — Endpoint GET /rate
# ==============================

@app.get("/rate")
def get_rate():
    try:
        # currency приходит из адресной строки: /rate?currency=USD.
        currency = request.args.get("currency", "").upper()

        # Если валюта не USD и не EUR, возвращаем ошибку по условию задания.
        if currency not in RATES:
            return jsonify({"message": "UNKNOWN CURRENCY"}), 400

        # Успешный ответ содержит только курс выбранной валюты.
        return jsonify({"rate": float(RATES[currency])}), 200
    except Exception:
        return jsonify({"message": "UNEXPECTED ERROR"}), 500


# ==============================
# Пункт 2.3 — Запуск внешнего сервиса
# ==============================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
