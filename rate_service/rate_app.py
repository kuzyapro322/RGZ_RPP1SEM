# Импорты
# flask — для пункта 2.3: реализации внешнего сервиса курса валют

from flask import Flask, jsonify, request


# ==============================
# Пункт 2.3 — Создание внешнего сервиса
# ==============================

app = Flask(__name__)


# ==============================
# Пункт 2.3 — Статические курсы валют
# ==============================

RATES = {
    "USD": 90.50,
    "EUR": 98.20,
}


# ==============================
# Пункт 2.3 — Endpoint GET /rate
# ==============================

@app.get("/rate")
def get_rate():
    try:
        currency = request.args.get("currency", "").upper()

        if currency not in RATES:
            return jsonify({"message": "UNKNOWN CURRENCY"}), 400

        return jsonify({"rate": RATES[currency]}), 200
    except Exception:
        return jsonify({"message": "UNEXPECTED ERROR"}), 500


# ==============================
# Пункт 2.3 — Запуск внешнего сервиса
# ==============================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
