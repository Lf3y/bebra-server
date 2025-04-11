from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
import uuid
import datetime
import logging
import threading
import time
import pytz
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://bebra_data_user:jz7fi1LQElfbWi0WbSnPhQJ0rBDzxsqU@dpg-cvht59t2ng1s739v1ug0-a/bebra_data')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Логирование
logging.basicConfig(level=logging.DEBUG)

# Модель для хранения ключей
class Key(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(36), unique=True, nullable=False)
    expiration_time = db.Column(db.DateTime, nullable=False)
    user_hwid = db.Column(db.String(100), nullable=True)
    creator_name = db.Column(db.String(100), nullable=True)  # Добавлено
    duration = db.Column(db.Integer, nullable=True)  # Добавлено, в секундах

    def __repr__(self):
        return f'<Key {self.key}>'

# Создание базы данных (если еще не создана)
with app.app_context():
    Key.__table__.drop(db.engine)
    db.create_all()

def generate_unique_key(expiration_duration, creator_name):
    key = str(uuid.uuid4())
    expiration_time = datetime.datetime.utcnow()  # Пока просто текущее, потом обновим при активации
    new_key = Key(
        key=key,
        expiration_time=expiration_time,
        creator_name=creator_name,
        duration=int(expiration_duration.total_seconds())
    )
    db.session.add(new_key)
    db.session.commit()
    return key, None

# Функция для проверки ключа и HWID
def verify_key_with_hwid(key, hwid):
    stored_key = Key.query.filter_by(key=key).first()

    if not stored_key:
        return False, "Invalid key"  # Ключ не найден

    if stored_key.expiration_time < datetime.datetime.utcnow():
        return False, "Key expired"  # Ключ истёк

    if stored_key.user_hwid:  # Если ключ уже привязан к HWID
        if stored_key.user_hwid != hwid:
            return False, "Key is already used on another device"  # HWID не совпадает
    else:  # Привязываем ключ к текущему HWID
        stored_key.user_hwid = hwid
        db.session.commit()

    return True, "Key is valid"  # Ключ действителен

@app.route('/generate_key', methods=['POST'])
def generate_key():
    try:
        duration_code = str(request.json.get('duration', '1'))  # По умолчанию — 1 (день)
        creator_name = request.json.get('creator', 'unknown')

        # Установка времени по коду
        duration_map = {
            '1': datetime.timedelta(days=1, hours=3),
            '2': datetime.timedelta(weeks=1, hours=3),
            '3': datetime.timedelta(weeks=4, hours=3),    # 1 месяц
            '4': datetime.timedelta(weeks=12, hours=3),   # 3 месяца
            '5': datetime.timedelta(weeks=24, hours=3),   # 6 месяцев
            '6': datetime.timedelta(weeks=52, hours=3),   # 1 год
            '7': datetime.timedelta(days=365*999)         # Навсегда (примерно)
        }

        expiration_duration = duration_map.get(duration_code)

        if not expiration_duration:
            return jsonify({"error": "Invalid duration code. Use 1-7."}), 400

        key, _ = generate_unique_key(expiration_duration, creator_name)

        return jsonify({
            "key": key,
            "duration_seconds": expiration_duration.total_seconds(),
            "human_readable": duration_code
        }), 200

    except Exception as e:
        logging.error(f"Error generating key: {e}")
        return jsonify({"error": "Internal server error"}), 500

def verify_key_with_hwid(key, hwid):
    stored_key = Key.query.filter_by(key=key).first()

    if not stored_key:
        return False, "Invalid key"

    # Первый вход: устанавливаем expiration_time
    if stored_key.user_hwid is None:
        stored_key.user_hwid = hwid
        stored_key.expiration_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=stored_key.duration)
        db.session.commit()

    # Проверяем срок действия
    if stored_key.expiration_time < datetime.datetime.utcnow():
        return False, "Key expired"

    if stored_key.user_hwid != hwid:
        return False, "Key is already used on another device"

    return True, "Key is valid"

@app.route('/keys', methods=['GET'])
def get_keys():
    try:
        keys = Key.query.all()
        key_list = [{
            "id": key.id,
            "key": key.key,
            "expiration_time": key.expiration_time.strftime('%Y-%m-%d %H:%M:%S') if key.expiration_time else "не установлено",
            "hwid": key.user_hwid or "Ключ не привязан",
            "creator": key.creator_name or "не указано",
            "duration_seconds": key.duration
        } for key in keys]
        return jsonify(key_list)
    except Exception as e:
        logging.error(f"Error fetching keys: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/delete_key/<int:key_id>', methods=['DELETE'])
def delete_key(key_id):
    try:
        key = Key.query.get(key_id)
        if key:
            db.session.delete(key)
            db.session.commit()
            return jsonify({"message": "Key deleted successfully"})
        else:
            return jsonify({"error": "Key not found"}), 404
    except Exception as e:
        logging.error(f"Error deleting key: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/delete_all_keys', methods=['DELETE'])
def delete_all_keys():
    try:
        Key.query.delete()
        db.session.commit()
        return jsonify({"message": "All keys deleted successfully"})
    except Exception as e:
        logging.error(f"Error deleting all keys: {e}")
        return jsonify({"error": "Internal server error"}), 500

def delete_expired_keys():
    print("Фоновый процесс удаления ключей запущен!")  # Для отладки
    while True:
        with app.app_context():
            tz = pytz.timezone("Europe/Moscow")  # Укажите нужный часовой пояс
            now = datetime.datetime.now(tz)
            print(f"Текущее время: {now}")  # Логируем текущее время

            expired_keys = Key.query.filter(Key.expiration_time <= now).all()
            if expired_keys:
                for key in expired_keys:
                    db.session.delete(key)
                db.session.flush()  # Очистка перед коммитом
                db.session.commit()
                print(f"Удалено {len(expired_keys)} ключей")
            else:
                print("Нет просроченных ключей")


@app.route('/delete_expired_keys', methods=['POST'])
def delete_expired_keys_request():
    """Удаляет все просроченные ключи при вызове запроса."""
    try:
        with app.app_context():
            tz_moscow = pytz.timezone("Europe/Moscow")
            now_moscow = datetime.datetime.now(tz_moscow) + datetime.timedelta(hours=3)  # Текущее время в Москве
            
            logging.debug(f"Удаление просроченных ключей. Текущее время в Москве: {now_moscow}")

            expired_keys = Key.query.filter(Key.expiration_time <= now_moscow).all()
            logging.debug(f"Найдено {len(expired_keys)} просроченных ключей")

            if expired_keys:
                for key in expired_keys:
                    db.session.delete(key)
                db.session.commit()
                return jsonify({"message": f"Удалено {len(expired_keys)} просроченных ключей"}), 200
            else:
                return jsonify({"message": "Нет просроченных ключей"}), 200
    except Exception as e:
        logging.error(f"Ошибка при удалении просроченных ключей: {e}")
        return jsonify({"error": "Ошибка сервера"}), 500
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
