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
    key = db.Column(db.String(36), unique=True, nullable=False)  # Уникальный ключ
    expiration_time = db.Column(db.DateTime, nullable=False)  # Время истечения
    user_hwid = db.Column(db.String(100), nullable=True)  # HWID пользователя

    def __repr__(self):
        return f'<Key {self.key}>'

# Создание базы данных (если еще не создана)
with app.app_context():
    db.create_all()

# Функция для генерации уникального ключа
def generate_unique_key(expiration_duration):
    key = str(uuid.uuid4())  # Генерация уникального ключа
    expiration_time = datetime.datetime.utcnow() + expiration_duration
    new_key = Key(key=key, expiration_time=expiration_time)
    db.session.add(new_key)
    db.session.commit()
    return key, expiration_time

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
        duration = request.json.get('duration', 'day')  # По умолчанию - день

        if duration == 'day':
            expiration_duration = datetime.timedelta(days=1, hours=3)
        elif duration == 'week':
            expiration_duration = datetime.timedelta(weeks=1, hours=3)
        elif duration == 'month':
            expiration_duration = datetime.timedelta(weeks=4, hours=3)
        elif duration == '30sec':
            expiration_duration = datetime.timedelta(seconds=10, hours=3)  # Генерация на 30 секунд
        else:
            return jsonify({"error": "Invalid duration. Choose 'day', 'week', or 'month'."}), 400

        key, expiration_time = generate_unique_key(expiration_duration)
        
        response = {
            "key": key,
            "expiration_time": expiration_time.strftime('%Y-%m-%d %H:%M:%S')
        }

        logging.debug(f"Generated key: {response}")
        return jsonify(response)
    
    except Exception as e:
        logging.error(f"Error generating key: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/verify_key', methods=['POST'])
def verify_key():
    try:
        key = request.json.get('key')
        hwid = request.json.get('hwid')

        if not key or not hwid:
            return jsonify({"error": "Key and HWID are required"}), 400

        is_valid, message = verify_key_with_hwid(key, hwid)

        if is_valid:
            return jsonify({"valid": True, "message": message}), 200
        else:
            return jsonify({"valid": False, "error": message}), 401
    except Exception as e:
        logging.error(f"Error verifying key: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/keys', methods=['GET'])
def get_keys():
    try:
        keys = Key.query.all()
        key_list = [{"id": key.id, "key": key.key, "expiration_time": key.expiration_time.strftime('%Y-%m-%d %H:%M:%S'), "hwid": key.user_hwid} for key in keys]
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
            now_moscow = datetime.datetime.now(tz_moscow)  # Текущее время в Москве
            
            logging.debug(f"Удаление просроченных ключей. Текущее время в Москве: {now_moscow}")

            expired_keys = Key.query.filter(Key.expiration_time <= now_moscow).all()
            print(Key.expiration_time)
            print(now_moscow)
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
