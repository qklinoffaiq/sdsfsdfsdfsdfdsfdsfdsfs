from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import vk_api
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.upload import VkUpload


# ---------------------------- НАСТРОЙКИ ----------------------------
group_token = "vk1.a.QBG637PbpalZlGyTBfrthxc2htux8xoPaII8M5o6Uaxx0RP6U0Fk7O8oMEjxh6ude5smg6ctIE0zI5HUdpfzOxvOaIcZ2JlBGjYOgcZo2ZQuTanBCH_gDCfwJ-ek5YttN_qgfrq9OBHrpMz-mlxojnOpE53QplEXnqhkzKt5WS30-BKeDVfp5OFUeU-H9FW4XxwOJWnIabnIz4JEvu-6Lw"
group_id = 238456927
cd_min = 10
interval_sec = 0.01
admin_ids = []
dev_ids = [574393629]
OWNER_ID = 574393629

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data.json"
USERS_DB_PATH = BASE_DIR / "users_db.json"
ADS_PATH = BASE_DIR / "ads.json"

SUBSCRIPTION_CHANNEL_ID = 238456927
SUBSCRIPTION_CHANNEL_LINK = "https://vk.com/club238456927"

# Ссылка на чат для кнопки "Присоединиться к нам!"
JOIN_CHAT_LINK = "https://vk.me/join/Bp/rS5GCao5gcWOOKtuX/qA7Uc4x0acv78g="

# ---------------------------- ЛОГГЕР ----------------------------
def get_logger(name: str = "vk_bot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    log_path = BASE_DIR / "bot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(formatter)
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    return logger


logger = get_logger()

# ---------------------------- РАБОТА С JSON ----------------------------
def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


def ensure_json(path: Path, default: Any) -> None:
    if not path.exists():
        write_json(path, default)


# ---------------------------- ГЛОБАЛЬНЫЕ ДАННЫЕ ----------------------------
runtime_data = read_json(DATA_PATH, {"chat_ids": [], "admin_chat": None, "message_text": "", "photo_path": None})
chat_ids = runtime_data.get("chat_ids", [])
admin_chat = runtime_data.get("admin_chat")
message_text = runtime_data.get("message_text", "")
message_photo_path = runtime_data.get("photo_path")

users_db = read_json(USERS_DB_PATH, {})
ads_data = read_json(ADS_PATH, {})

# ---------------------------- VK API ----------------------------
vk_session = vk_api.VkApi(token=group_token)
vk = vk_session.get_api()
longpoll = VkBotLongPoll(vk_session, group_id)
vk_upload = VkUpload(vk_session)

# ---------------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------------------------
def random_id() -> int:
    return int(time.time() * 1000000) % (2**31 - 1)


def save_runtime_data() -> None:
    runtime_data["chat_ids"] = chat_ids
    runtime_data["admin_chat"] = admin_chat
    runtime_data["message_text"] = message_text
    runtime_data["photo_path"] = message_photo_path
    write_json(DATA_PATH, runtime_data)


def save_users() -> None:
    write_json(USERS_DB_PATH, users_db)


def save_ads() -> None:
    write_json(ADS_PATH, ads_data)


def get_role(user_id: int) -> str | None:
    if user_id in dev_ids:
        return "dev"
    if user_id in admin_ids:
        return "admin"
    return users_db.get(str(user_id), {}).get("role", "user")


def has_permission(user_id: int, level: str) -> bool:
    role = get_role(user_id)
    if level == "dev":
        return role == "dev"
    if level == "admin":
        return role in {"admin", "dev"}
    return False


def update_user_stats(user_id: int, action: str) -> None:
    user = users_db.setdefault(str(user_id), {"role": "user", "osn_photo_count": 0, "osn_text_count": 0, "total_messages": 0, "last_message": "", "stats": {}})
    stats = user.setdefault("stats", {})
    stats[action] = stats.get(action, 0) + 1
    stats["last_activity"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if action == "osn_photo":
        user["osn_photo_count"] = user.get("osn_photo_count", 0) + 1
    elif action == "osn_text":
        user["osn_text_count"] = user.get("osn_text_count", 0) + 1
    elif action == "command":
        user["total_messages"] = user.get("total_messages", 0) + 1
    user["last_message"] = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {action}"
    save_users()


def get_user_display_name(user_id: int) -> str:
    try:
        user = vk.users.get(user_ids=user_id)[0]
        return f"{user['first_name']} {user['last_name']}"
    except Exception:
        return f"Пользователь {user_id}"


def render_user_stats_detailed(user_id: int) -> str:
    user = users_db.get(str(user_id), {})
    stats = user.get("stats", {})
    total = int(user.get("total_messages", stats.get("command", 0)) or 0)
    role_map = {"user": "Пользователь", "admin": "Администратор", "dev": "Разработчик"}
    return (
        f"👤 Информация о пользователе:\n\n"
        f"🔹 Имя: {get_user_display_name(user_id)}\n"
        f"🔹 Роль: {role_map.get(user.get('role', 'user'), 'Пользователь')}\n"
        f"🔹 Изменения текста/фото: {int(user.get('osn_text_count', 0) or 0) + int(user.get('osn_photo_count', 0) or 0)}\n"
        f"🔹 Всего сообщений для бота: {total}\n"
        f"🔹 Последнее сообщение: {user.get('last_message', 'Неизвестно')}"
    )


def render_staff_detailed() -> str:
    devs = []
    admins = []
    try:
        owner = vk.users.get(user_ids=OWNER_ID)[0]
        devs.append(f"• [id{OWNER_ID}|{owner['first_name']} {owner['last_name']}]")
    except Exception:
        devs.append(f"• [id{OWNER_ID}|Разработчик]")
    for uid, data in users_db.items():
        if data.get("role") == "admin":
            try:
                info = vk.users.get(user_ids=int(uid))[0]
                admins.append(f"• [id{uid}|{info['first_name']} {info['last_name']}]")
            except Exception:
                admins.append(f"• [id{uid}|Администратор]")
    return "🔧 Список персонала бота:\n\nРазработчик:\n" + "\n".join(devs) + "\n\nАдминистраторы:\n" + ("\n".join(admins) if admins else "Нет назначенных администраторов")


def render_runtime_info() -> str:
    return (
        "ℹ️ Текущие настройки\n\n"
        f"Чатов в рассылке: {len(chat_ids)}\n"
        f"Админ-чат: {admin_chat or 'не задан'}\n"
        f"Интервал между сообщениями: {interval_sec}\n"
        f"Авторассылка каждые: {cd_min} мин.\n"
        f"Основной текст: {len(message_text or '')} символов"
    )


def render_additional_texts_list() -> str:
    return "Дополнительные тексты не используются (функция отключена)."


def remove_chat_from_broadcast_list(chat_id: int, reason: str) -> None:
    if chat_id == admin_chat:
        return
    if chat_id in chat_ids:
        chat_ids.remove(chat_id)
        save_runtime_data()
        logger.info(f"Чат {chat_id} удалён из рассылки: {reason}")


def is_subscribed(user_id: int) -> bool:
    try:
        return bool(vk.groups.isMember(group_id=group_id, user_id=user_id))
    except Exception:
        return True


def send_message(chat_id: int, text: str, attachment: str | None = None, keyboard: str | None = None) -> Any:
    params = {"peer_id": chat_id, "message": text or " ", "random_id": random_id()}
    if attachment:
        params["attachment"] = attachment
    if keyboard:
        params["keyboard"] = keyboard
    try:
        return vk.messages.send(**params)
    except Exception as e:
        err = str(e).lower()
        if "kicked" in err or "restricted" in err or "access denied" in err or "code 983" in err:
            remove_chat_from_broadcast_list(chat_id, err)
            return None
        logger.error(f"Ошибка отправки в чат {chat_id}: {e}")
        return None


def safe_send_pm(user_id: int, text: str, keyboard: str | None = None) -> Any:
    try:
        params = {"user_id": user_id, "message": text or " ", "random_id": random_id()}
        if keyboard:
            params["keyboard"] = keyboard
        return vk.messages.send(**params)
    except Exception as e:
        if "[901]" in str(e):
            logger.warning(f"Не удалось отправить ЛС {user_id}: пользователь запретил сообщения.")
        else:
            logger.error(f"Ошибка ЛС {user_id}: {e}")
        return None


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def upload_message_photo() -> str | None:
    if not message_photo_path:
        return None
    if isinstance(message_photo_path, str) and message_photo_path.startswith(("photo", "doc", "video")):
        return message_photo_path
    photo_path = Path(message_photo_path)
    if not photo_path.is_absolute():
        photo_path = BASE_DIR / photo_path
    if not photo_path.exists():
        logger.warning(f"Фото не найдено: {photo_path}")
        return None
    try:
        photo = vk_upload.photo_messages(str(photo_path))[0]
        return f"photo{photo['owner_id']}_{photo['id']}"
    except Exception as e:
        logger.error(f"Ошибка загрузки фото: {e}")
        return None


def save_main_photo_from_message(message: dict[str, Any]) -> tuple[bool, str]:
    global message_photo_path
    attachments = message.get("attachments") or []
    if not attachments:
        return False, "Прикрепите фото к сообщению с командой .редоснфото"

    for att in attachments:
        if att.get("type") == "photo":
            photo = att["photo"]
            sizes = photo.get("sizes") or []
            if sizes:
                best = max(sizes, key=lambda x: x.get("width", 0) * x.get("height", 0))
                url = best.get("url")
                if url:
                    photos_dir = BASE_DIR / "photos"
                    photos_dir.mkdir(parents=True, exist_ok=True)
                    target = photos_dir / "main_photo.jpg"
                    try:
                        with urlopen(url) as resp:
                            target.write_bytes(resp.read())
                        message_photo_path = "photos/main_photo.jpg"
                        save_runtime_data()
                        return True, "✅ Основное фото бота успешно обновлено."
                    except Exception as e:
                        return False, f"Не удалось сохранить фото: {e}"
    return False, "Не удалось обработать фото."


# ---------------------------- КНОПКА "ПРИСОЕДИНИТЬСЯ К НАМ!" ----------------------------
def build_join_keyboard() -> str:
    kb = VkKeyboard(inline=True)
    kb.add_openlink_button(label="🔗 Присоединиться к нам!", link=JOIN_CHAT_LINK)
    return kb.get_keyboard()


def handle_join_info(chat_id: int) -> None:
    send_message(
        chat_id,
        "🔹 Присоединяйтесь к нашему чату, чтобы быть в курсе всех событий!\n\nНажмите на кнопку ниже.",
        keyboard=build_join_keyboard(),
    )


# ---------------------------- РАССЫЛКА ----------------------------
def get_active_random_orders() -> list[dict[str, Any]]:
    current_ads = read_json(ADS_PATH, {})
    active: list[dict[str, Any]] = []
    for key, ad in current_ads.items():
        if not isinstance(ad, dict):
            continue
        if key in {"users", "active_ad"}:
            continue
        if is_user_mirror_ad_key(key, ad):
            continue
        if ad.get("status") not in {"approved", "active"}:
            continue
        expires_at = ad.get("expires_at")
        if expires_at and parse_dt(expires_at) < datetime.now():
            continue
        active.append(ad)
    random.shuffle(active)
    return active


def is_user_mirror_ad_key(key: str, ad_data: dict[str, Any]) -> bool:
    user_id = ad_data.get("user_id")
    return str(key).isdigit() and user_id is not None and str(key) == str(user_id)


def send_broadcast_to_chat(chat_id: int) -> str | None:
    logger.info(f"Рассылка в чат {chat_id}")
    for ad in get_active_random_orders():
        res = send_message(chat_id, ad.get("text", ""), attachment=ad.get("photo"))
        if res == "access_error":
            return "access_error"
        time.sleep(interval_sec)

    if message_text:
        res = send_message(chat_id, message_text, attachment=upload_message_photo(), keyboard=build_join_keyboard())
        if res == "access_error":
            return "access_error"
        time.sleep(interval_sec)

    main_sale = ads_data.get("main_text_sale")
    if main_sale and main_sale.get("status") == "active":
        expires_at = main_sale.get("expires_at")
        if not expires_at or parse_dt(expires_at) > datetime.now():
            res = send_message(chat_id, main_sale.get("text", ""), attachment=main_sale.get("photo"))
            if res == "access_error":
                return "access_error"
            time.sleep(interval_sec)
    return None


def broadcast_message(notify_chat_id: int | None = None) -> None:
    target = [cid for cid in chat_ids if cid != admin_chat and len(str(cid)) == 10 and str(cid).startswith("2")]
    logger.info(f"Рассылка запущена, чатов: {len(target)}")
    if not target:
        if notify_chat_id:
            send_message(notify_chat_id, "⚠️ Список чатов пуст.")
        return
    sent = 0
    interrupted = False
    for cid in target:
        if send_broadcast_to_chat(cid) == "access_error":
            interrupted = True
            break
        sent += 1
    if notify_chat_id:
        msg = "✅ Рассылка завершена." if not interrupted else f"⚠️ Остановлена. Обработано: {sent}"
        send_message(notify_chat_id, msg)


def broadcast_gzov(notify_chat_id: int | None = None) -> None:
    target = [cid for cid in chat_ids if cid != admin_chat and len(str(cid)) == 10 and str(cid).startswith("2")]
    logger.info(f"GZOV запущен, чатов: {len(target)}")
    if not target:
        if notify_chat_id:
            send_message(notify_chat_id, "⚠️ Список чатов пуст.")
        return
    sent = 0
    interrupted = False
    for cid in target:
        res = send_message(cid, message_text, attachment=upload_message_photo(), keyboard=build_join_keyboard())
        if res == "access_error":
            interrupted = True
            break
        sent += 1
        time.sleep(interval_sec)
    if notify_chat_id:
        msg = "✅ GZOV завершён." if not interrupted else f"⚠️ Остановлен. Обработано: {sent}"
        send_message(notify_chat_id, msg)


# ---------------------------- КОМАНДЫ АДМИНИСТРИРОВАНИЯ ----------------------------
def handle_admin_text_command(chat_id: int, user_id: int, text: str, message: dict[str, Any]) -> bool:
    if not has_permission(user_id, "admin"):
        return False

    if text == ".список":
        total = len(chat_ids)
        lst = "\n".join(str(cid) for cid in chat_ids if cid != admin_chat)
        send_message(chat_id, f"Количество чатов для рассылки: {total}\nСписок чатов:\n{lst}")
        update_user_stats(user_id, "command")
        return True

    if text == ".ид":
        send_message(chat_id, f"✅ ID этой беседы: {chat_id}")
        update_user_stats(user_id, "command")
        return True

    if text == ".инфо":
        send_message(chat_id, render_runtime_info())
        update_user_stats(user_id, "command")
        return True

    if text == ".инфо_о_нас":
        handle_join_info(chat_id)
        update_user_stats(user_id, "command")
        return True

    if text == ".уст":
        if admin_chat is None:
            send_message(chat_id, "Администратор не установлен.")
            return True
        if chat_id != admin_chat:
            send_message(chat_id, "❌ Команда доступна только из админ-чата.")
            return True
        if len(str(chat_id)) == 10 and str(chat_id).startswith("2"):
            if chat_id not in chat_ids:
                chat_ids.append(chat_id)
                save_runtime_data()
                send_message(chat_id, "Чат добавлен в рассылку.")
            else:
                send_message(chat_id, "Чат уже в рассылке.")
        else:
            send_message(chat_id, "Это не беседа.")
        update_user_stats(user_id, "command")
        return True

    if text == ".инфочат":
        send_message(
            chat_id,
            f"📋 Информация о чате:\nID чата: {chat_id}\nID отправителя: {user_id}\nАдмин-чат: {admin_chat}\nВерсия бота: 3.0 (с кнопкой «Присоединиться»)",
        )
        update_user_stats(user_id, "command")
        return True

    if text.startswith(".добид"):
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "Использование: .добид [число]")
            return True
        try:
            count = int(parts[1])
            if count <= 0:
                raise ValueError
        except ValueError:
            send_message(chat_id, "Число должно быть положительным.")
            return True
        bot_chats = [cid for cid in chat_ids if str(cid).startswith("2") and len(str(cid)) == 10]
        next_id = max(bot_chats, default=2000000000) + 1
        for _ in range(count):
            chat_ids.append(next_id)
            next_id += 1
        save_runtime_data()
        send_message(chat_id, f"✅ Добавлено {count} чатов.")
        update_user_stats(user_id, "command")
        return True

    if text.startswith(".делид"):
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "Использование: .делид [число]")
            return True
        try:
            count = int(parts[1])
            if count <= 0:
                raise ValueError
        except ValueError:
            send_message(chat_id, "Число должно быть положительным.")
            return True
        bot_chats = [cid for cid in chat_ids if str(cid).startswith("2") and len(str(cid)) == 10]
        removed = 0
        for _ in range(min(count, len(bot_chats))):
            if bot_chats:
                c = bot_chats.pop()
                if c in chat_ids:
                    chat_ids.remove(c)
                    removed += 1
        if removed:
            save_runtime_data()
        send_message(chat_id, f"✅ Удалено {removed} чатов.")
        update_user_stats(user_id, "command")
        return True

    if text == ".тест":
        if chat_id != admin_chat:
            send_message(chat_id, "❌ Команда доступна только в админ-чате.")
            update_user_stats(user_id, "command")
            return True
        result = send_broadcast_to_chat(chat_id)
        if result == "access_error":
            send_message(chat_id, "⚠️ Ошибка доступа при тестовой рассылке.")
        else:
            send_message(chat_id, "✅ Тестовая рассылка завершена.")
        update_user_stats(user_id, "command")
        return True

    if text == ".рассылка":
        if chat_id != admin_chat:
            send_message(chat_id, "❌ Команда доступна только в админ-чате.")
            return True
        t = threading.Thread(target=broadcast_message, kwargs={"notify_chat_id": chat_id}, daemon=True)
        t.start()
        send_message(chat_id, "✅ Рассылка запущена.")
        update_user_stats(user_id, "command")
        return True

    if text == ".gzov" and has_permission(user_id, "dev"):
        if chat_id != admin_chat:
            send_message(chat_id, "❌ Команда доступна только в админ-чате.")
            return True
        t = threading.Thread(target=broadcast_gzov, kwargs={"notify_chat_id": chat_id}, daemon=True)
        t.start()
        send_message(chat_id, "✅ GZOV запущен.")
        update_user_stats(user_id, "command")
        return True

    return False


def handle_dev_command(chat_id: int, user_id: int, text: str, message: dict[str, Any]) -> bool:
    global message_text, message_photo_path
    if not has_permission(user_id, "dev"):
        return False

    if text.startswith(".редоснтекст"):
        parts = text.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message(chat_id, "Неверный формат. Используйте: .редоснтекст [текст]")
            return True
        message_text = parts[1].strip()
        save_runtime_data()
        send_message(chat_id, "✅ Основной текст обновлён.", attachment=upload_message_photo())
        update_user_stats(user_id, "osn_text")
        return True

    if text == ".редоснфото":
        ok, res = save_main_photo_from_message(message)
        send_message(chat_id, res)
        if ok:
            update_user_stats(user_id, "osn_photo")
        return True

    if text.startswith(".настройки"):
        parts = text.split()
        if len(parts) != 3:
            send_message(chat_id, "Использование: .настройки [cd_min|interval_sec] [число]")
            return True
        key, val = parts[1], parts[2]
        try:
            value = float(val)
        except ValueError:
            send_message(chat_id, "Значение должно быть числом.")
            return True
        if key == "cd_min":
            if value < 1 or value > 1440:
                send_message(chat_id, "cd_min от 1 до 1440.")
                return True
            global cd_min
            cd_min = int(value)
            send_message(chat_id, f"✅ cd_min = {cd_min} мин")
        elif key == "interval_sec":
            if value < 0 or value > 60:
                send_message(chat_id, "interval_sec от 0 до 60.")
                return True
            global interval_sec
            interval_sec = value
            send_message(chat_id, f"✅ interval_sec = {interval_sec} сек")
        else:
            send_message(chat_id, "Доступные ключи: cd_min, interval_sec")
        update_user_stats(user_id, "command")
        return True

    if text == ".админчат":
        global admin_chat
        if admin_chat == chat_id:
            send_message(chat_id, "⚠️ Этот чат уже административный.")
        else:
            admin_chat = chat_id
            save_runtime_data()
            send_message(chat_id, "Административный чат установлен.")
        return True

    if text.startswith(".админ"):
        target_id = extract_target_user(message, text)
        if not target_id:
            send_message(chat_id, "❌ Укажите пользователя (ответом, @ или ссылкой).")
            return True
        if target_id == user_id:
            send_message(chat_id, "❌ Нельзя изменить свои права через эту команду.")
            return True
        user_entry = users_db.setdefault(str(target_id), {"role": "user"})
        if user_entry.get("role") == "admin":
            user_entry["role"] = "user"
            save_users()
            send_message(chat_id, f"❌ Права администратора сняты у {target_id}.")
        else:
            user_entry["role"] = "admin"
            save_users()
            send_message(chat_id, f"✅ {target_id} назначен администратором.")
        update_user_stats(user_id, "command")
        return True

    if text.startswith(".разраб") and user_id == OWNER_ID:
        target_id = extract_target_user(message, text)
        if not target_id:
            send_message(chat_id, "❌ Укажите пользователя.")
            return True
        user_entry = users_db.setdefault(str(target_id), {"role": "user"})
        if user_entry.get("role") == "dev":
            user_entry["role"] = "user"
            save_users()
            send_message(chat_id, f"❌ Права разработчика сняты у {target_id}.")
        else:
            user_entry["role"] = "dev"
            save_users()
            send_message(chat_id, f"✅ {target_id} назначен разработчиком.")
        return True

    if text == ".стафф":
        send_message(chat_id, render_staff_detailed())
        return True

    if text == ".доходы":
        send_message(chat_id, "📊 Статистика доходов недоступна (функция заказов удалена).")
        return True

    return False


def extract_target_user(message: dict[str, Any], text: str) -> int | None:
    reply = message.get("reply_message")
    if isinstance(reply, dict) and reply.get("from_id"):
        return int(reply["from_id"])
    for part in text.split():
        m = re.match(r"\[id(\d+)\|", part)
        if m:
            return int(m.group(1))
        if part.startswith("id") and part[2:].isdigit():
            return int(part[2:])
    return None


def handle_admin_order_action(chat_id: int, user_id: int, payload: dict[str, Any]) -> bool:
    command = payload.get("command")
    if command in ("view_order", "approve_order", "reject_order", "delete_user_order", "show_check_order", "exit_order_view", "history_page", "support_open_chat", "support_close"):
        send_message(chat_id, "❌ Функция управления заказами удалена.")
        return True
    return False


def parse_message_payload(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload.strip():
        try:
            return json.loads(payload)
        except Exception:
            return {}
    return {}


# ---------------------------- АВТОРАССЫЛКА ----------------------------
last_broadcast_time = time.time()
auto_broadcast_thread: threading.Thread | None = None
expiration_thread: threading.Thread | None = None


def process_expiring_ads() -> None:
    global ads_data
    ads_data = read_json(ADS_PATH, {})
    changed = False
    now = datetime.now()
    for key, ad in list(ads_data.items()):
        if key in {"users", "active_ad", "main_text_sale"}:
            continue
        if not isinstance(ad, dict):
            continue
        if ad.get("status") not in {"approved", "active"}:
            continue
        expires = parse_dt(ad.get("expires_at"))
        if not expires:
            continue
        if expires <= now:
            user_id = ad.get("user_id")
            safe_send_pm(user_id, f"❌ Срок рекламы истёк.\nКод: {ad.get('order_code', 'без кода')}\nОбъявление удалено.")
            ads_data.pop(key, None)
            changed = True
    if changed:
        save_ads()


def expiration_monitor() -> None:
    while True:
        try:
            process_expiring_ads()
        except Exception as e:
            logger.error(f"Ошибка мониторинга истечения: {e}")
        time.sleep(60)


def auto_broadcast_loop() -> None:
    global last_broadcast_time
    while True:
        try:
            if time.time() - last_broadcast_time >= cd_min * 60:
                threading.Thread(target=broadcast_message, daemon=True).start()
                last_broadcast_time = time.time()
        except Exception as e:
            logger.error(f"Ошибка авторассылки: {e}")
        time.sleep(5)


def get_help_text(role: str | None) -> str:
    user_commands = "📋 Основные команды:\n🔹 .пинг — проверить работу бота\n🔹 .стата — статистика пользователя\n"
    admin_commands = (
        "🔸 Административные команды:\n"
        "🔹 .рассылка — запустить рассылку\n"
        "🔹 .список — показать количество чатов\n"
        "🔹 .ид — узнать ID текущего чата\n"
        "🔹 .инфо — показать текущие настройки\n"
        "🔹 .хелп — показать это сообщение\n"
        "🔹 .тест — отправить тестовое сообщение\n"
        "🔹 .допсписок — показать список дополнительных текстов\n"
        "🔹 .уст — добавить текущий чат в рассылку\n"
        "🔹 .инфочат — информация о чате\n"
        "🔹 .добид [число] — добавить ID чатов\n"
        "🔹 .делид [число] — удалить ID чатов\n"
        "🔹 .инфо_о_нас — показать кнопку «Присоединиться»\n"
    )
    dev_commands = (
        "🔧 Разработчик:\n"
        "🔹 .админ [id/@] — выдать/снять администратора\n"
        "🔹 .разраб [id/@] — выдать/снять разработчика\n"
        "🔹 .настройки [cd_min|interval_sec] [число] — изменить тайминги\n"
        "🔹 .редоснтекст [текст] — изменить основной текст\n"
        "🔹 .редоснфото — изменить основное фото\n"
        "🔹 .gzov — разослать только основной текст и кнопку\n"
        "🔹 .стафф — список персонала\n"
        "🔹 .админчат — установить текущий чат как админский\n"
    )
    full = user_commands
    if role in {"admin", "dev"}:
        full += "\n\n" + admin_commands
    if role == "dev":
        full += "\n\n" + dev_commands
    return full


# ---------------------------- ОСНОВНАЯ ЛОГИКА ----------------------------
def main() -> None:
    global auto_broadcast_thread, expiration_thread, last_broadcast_time
    logger.info("Бот запущен (версия с кнопкой «Присоединиться к нам!»)")

    if expiration_thread is None or not expiration_thread.is_alive():
        expiration_thread = threading.Thread(target=expiration_monitor, daemon=True)
        expiration_thread.start()
    if auto_broadcast_thread is None or not auto_broadcast_thread.is_alive():
        auto_broadcast_thread = threading.Thread(target=auto_broadcast_loop, daemon=True)
        auto_broadcast_thread.start()

    for event in longpoll.listen():
        if event.type != VkBotEventType.MESSAGE_NEW:
            continue

        message = event.obj.message
        chat_id = message["peer_id"]
        user_id = message["from_id"]
        text = (message.get("text") or "").strip()
        payload = parse_message_payload(message)

        # ЛИЧНЫЕ СООБЩЕНИЯ – отключаем полностью
        if chat_id == user_id:
            safe_send_pm(user_id, "🤖 Бот работает только в беседах.")
            continue

        # Проверка подписки (если требуется)
        if not has_permission(user_id, "admin") and not is_subscribed(user_id):
            send_message(chat_id, f"⚠️ Подпишитесь на нашу группу для использования команд:\n{SUBSCRIPTION_CHANNEL_LINK}")
            continue

        # Команда .инфо_о_нас (также может прийти как callback, но у нас её нет в кнопках)
        if text == ".инфо_о_нас":
            handle_join_info(chat_id)
            update_user_stats(user_id, "command")
            continue

        # Обработка команд
        if text == ".пинг":
            if chat_id != admin_chat:
                send_message(chat_id, "❌ Команда доступна только в админ-чате.")
                continue
            t0 = time.time()
            send_message(chat_id, "Пинг...")
            t1 = time.time()
            send_message(chat_id, f"Пинг: {int((t1 - t0) * 1000)}ms")
            update_user_stats(user_id, "command")
            continue

        if text.startswith(".стата"):
            target = extract_target_user(message, text) or user_id
            send_message(chat_id, render_user_stats_detailed(target))
            update_user_stats(user_id, "command")
            continue

        if text == ".хелп":
            if chat_id != admin_chat:
                send_message(chat_id, "❌ Команда доступна только в админ-чате.")
                continue
            role = get_role(user_id)
            send_message(chat_id, get_help_text(role))
            update_user_stats(user_id, "command")
            continue

        if text == ".допсписок":
            if chat_id != admin_chat or not has_permission(user_id, "admin"):
                send_message(chat_id, "❌ Нет прав.")
                continue
            send_message(chat_id, render_additional_texts_list())
            update_user_stats(user_id, "command")
            continue

        # Административные команды
        if handle_admin_text_command(chat_id, user_id, text, message):
            continue
        # Команды разработчика
        if handle_dev_command(chat_id, user_id, text, message):
            continue
        # Заглушка для любых callback-команд, связанных с заказами
        if handle_admin_order_action(chat_id, user_id, payload):
            continue

        # Неизвестная команда – игнорируем
        if text and not text.startswith("."):
            continue
        if text and text.startswith("."):
            send_message(chat_id, "❌ Неизвестная команда.")


if __name__ == "__main__":
    main()