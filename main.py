from __future__ import annotations

import re
import threading
import time
from urllib.request import urlopen
from datetime import datetime
from pathlib import Path
from typing import Any

import vk_api
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.upload import VkUpload

from config import MESSAGE_CONFIG, admin_ids, cd_min, dev_ids, group_id, group_token, interval_sec
from utils.db import ensure_json_file, read_json, write_json_atomic
from utils.logger import get_logger

logger = get_logger()
OWNER_ID = 574393629
BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data.json"
USERS_DB_PATH = BASE_DIR / "users_db.json"

ensure_json_file(DATA_PATH, MESSAGE_CONFIG)
ensure_json_file(USERS_DB_PATH, {})

runtime_data = read_json(DATA_PATH, MESSAGE_CONFIG)
chat_ids = runtime_data.get("chat_ids", MESSAGE_CONFIG.get("chat_ids", []))
admin_chat = runtime_data.get("admin_chat", MESSAGE_CONFIG.get("admin_chat"))
message_text = runtime_data.get("message_text", MESSAGE_CONFIG.get("text", ""))
message_photo_path = runtime_data.get("photo_path", MESSAGE_CONFIG.get("photo_path"))

vk_session = vk_api.VkApi(token=group_token)
vk = vk_session.get_api()
longpoll = VkBotLongPoll(vk_session, group_id)
vk_upload = VkUpload(vk_session)

broadcast_thread: threading.Thread | None = None
broadcast_lock = threading.Lock()
uploaded_message_photo: str | None = None
auto_broadcast_thread: threading.Thread | None = None
last_broadcast_time = time.time()

# ---------- вспомогательные функции ----------
def generate_random_id() -> int:
    return int(time.time() * 1000000) % (2**31 - 1)

def load_users() -> dict[str, Any]:
    return read_json(USERS_DB_PATH, {})

def save_users(data: dict[str, Any]) -> None:
    write_json_atomic(USERS_DB_PATH, data)

def get_role(user_id: int) -> str | None:
    if user_id in dev_ids:
        return "dev"
    if user_id in admin_ids:
        return "admin"
    users = load_users()
    return users.get(str(user_id), {}).get("role")

def has_permission(user_id: int, level: str) -> bool:
    role = get_role(user_id)
    if level == "dev":
        return role == "dev"
    if level == "admin":
        return role in {"admin", "dev"}
    return False

def update_user_stats(user_id: int, action: str) -> None:
    users = load_users()
    user = users.setdefault(
        str(user_id),
        {
            "role": "user",
            "osn_photo_count": 0,
            "osn_text_count": 0,
            "total_messages": 0,
            "last_message": "",
            "stats": {},
        },
    )
    stats = user.setdefault("stats", {})
    stats[action] = int(stats.get(action, 0) or 0) + 1
    stats["last_activity"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if action == "osn_photo":
        user["osn_photo_count"] = int(user.get("osn_photo_count", 0) or 0) + 1
    elif action == "osn_text":
        user["osn_text_count"] = int(user.get("osn_text_count", 0) or 0) + 1
    elif action == "command":
        user["total_messages"] = int(user.get("total_messages", 0) or 0) + 1
    user["last_message"] = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {action}"
    save_users(users)

def save_runtime_data() -> None:
    runtime_data["chat_ids"] = chat_ids
    runtime_data["admin_chat"] = admin_chat
    runtime_data["message_text"] = message_text
    runtime_data["photo_path"] = message_photo_path
    write_json_atomic(DATA_PATH, runtime_data)

def remove_chat_from_broadcast_list(chat_id: int, reason: str) -> None:
    if chat_id == admin_chat:
        return
    if chat_id in chat_ids:
        chat_ids.remove(chat_id)
        save_runtime_data()
        logger.info(f"Chat {chat_id}: удалён из списка рассылки ({reason})")

def build_join_keyboard() -> str:
    """Создаёт inline-клавиатуру с кнопкой-ссылкой на чат."""
    keyboard = VkKeyboard(inline=True)
    keyboard.add_openlink_button(
        label="🔗 Присоединиться к нам!",
        link="https://vk.me/join/Bp/rS5GCao5gcWOOKtuX/qA7Uc4x0acv78g=",
    )
    return keyboard.get_keyboard()

def get_help_text(role: str | None) -> str:
    user_commands = (
        "📋 Основные команды:\n"
        "🔹 .пинг — проверить, работает ли бот\n"
        "🔹 .стата — посмотреть свою статистику\n"
    )
    admin_commands = (
        "🔸 Административные команды:\n"
        "🔹 .рассылка — запустить рассылку\n"
        "🔹 .список — показать количество чатов\n"
        "🔹 .ид — узнать ID текущего чата\n"
        "🔹 .инфо — показать текущие настройки\n"
        "🔹 .хелп — показать это сообщение\n"
        "🔹 .тест — отправить тестовое сообщение\n"
        "🔹 .уст — добавить текущий чат в рассылку\n"
        "🔹 .инфочат — получить информацию о чате\n"
        "🔹 .добид [число] — добавить указанное количество ID в список\n"
        "🔹 .делид [число] — удалить указанное количество ID с конца списка\n"
    )
    dev_commands = (
        "🔧 Команды разработчика:\n"
        "🔹 .админ [id/@] — выдать или снять права администратора\n"
        "🔹 .разраб [id/@/ответ] — выдать или снять права разработчика\n"
        "🔹 .настройки [cd_min|interval_sec] [число] — изменить тайминги\n"
        "🔹 .редоснтекст [текст] — изменить основной текст рассылки\n"
        "🔹 .редоснфото — изменить основное фото рассылки\n"
        "🔹 .gzov — разослать только основное сообщение бота во все чаты\n"
        "🔹 .стафф — показать состав персонала\n"
        "🔹 .админчат — установить текущий чат как административный\n"
        "🔹 .доходы — посмотреть статистику доходов (упрощённо)\n"
    )
    full_text = user_commands
    if role in {"admin", "dev"}:
        full_text += "\n\n" + admin_commands
    if role == "dev":
        full_text += "\n\n" + dev_commands
    return full_text

def get_user_display_name(user_id: int) -> str:
    try:
        user_info = vk.users.get(user_ids=user_id)[0]
        return f"{user_info['first_name']} {user_info['last_name']}"
    except Exception:
        return f"Пользователь {user_id}"

def render_user_stats_detailed(user_id: int) -> str:
    users = load_users()
    user = users.get(str(user_id), {})
    stats = user.get("stats", {})
    total_commands = int(user.get("total_messages", stats.get("command", 0)) or 0)
    role_names = {"user": "Пользователь", "admin": "Администратор", "dev": "Разработчик"}
    role_display = role_names.get(user.get("role", "user"), "Пользователь")
    return (
        f"👤 Информация о пользователе:\n\n"
        f"🔹 Имя: {get_user_display_name(user_id)}\n"
        f"🔹 Роль: {role_display}\n"
        f"🔹 Изменения текста/фото: {int(user.get('osn_text_count', 0) or 0) + int(user.get('osn_photo_count', 0) or 0)}\n"
        f"🔹 Всего сообщений для бота: {total_commands}\n"
        f"🔹 Последнее сообщение: {user.get('last_message', 'Неизвестно') or 'Неизвестно'}"
    )

def render_staff_detailed() -> str:
    users = load_users()
    devs = []
    admins = []
    try:
        owner_info = vk.users.get(user_ids=OWNER_ID)[0]
        owner_name = f"{owner_info['first_name']} {owner_info['last_name']}"
        devs.append(f"• [id{OWNER_ID}|{owner_name}]")
    except Exception:
        devs.append(f"• [id{OWNER_ID}|Разработчик]")
    for uid, data in users.items():
        if data.get("role") == "admin":
            try:
                info = vk.users.get(user_ids=int(uid))[0]
                name = f"{info['first_name']} {info['last_name']}"
                admins.append(f"• [id{uid}|{name}]")
            except Exception:
                admins.append(f"• [id{uid}|Администратор]")
    return "🔧 Список персонала бота:\n\nРазработчик:\n" + "\n".join(devs) + "\n\nАдминистраторы:\n" + ("\n".join(admins) if admins else "Нет назначенных администраторов")

def render_runtime_info_legacy() -> str:
    return (
        "📊 ИНФОРМАЦИЯ О НАСТРОЙКАХ\n\n"
        f"⏱️ Интервал между рассылками: *{cd_min}* минут\n"
        f"⚡ Интервал отправки сообщений: *{interval_sec}* секунд\n\n"
        "📝 ТЕКСТ РАССЫЛКИ:\n" +
        (message_text if message_text else "<пусто>")
    )

def save_config_value(name: str, value_repr: str) -> None:
    config_path = BASE_DIR / "config.py"
    lines = config_path.read_text(encoding="utf-8").splitlines()
    updated = False
    new_lines: list[str] = []
    for line in lines:
        if line.startswith(f"{name} ="):
            new_lines.append(f"{name} = {value_repr}")
            updated = True
        else:
            new_lines.append(line)
    if updated:
        config_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

def send_message(chat_id: int, text: str, attachment: str | None = None, keyboard: str | None = None) -> Any:
    params = {"peer_id": chat_id, "message": text or " ", "random_id": generate_random_id()}
    if attachment:
        params["attachment"] = attachment
    if keyboard:
        params["keyboard"] = keyboard
    try:
        return vk.messages.send(**params)
    except Exception as exc:
        error_text = str(exc)
        error_lower = error_text.lower()
        if "the user was kicked out of the conversation" in error_lower:
            remove_chat_from_broadcast_list(chat_id, "участник исключён из беседы")
            return None
        if "you are restricted to write to a chat" in error_lower or "code 983" in error_lower:
            remove_chat_from_broadcast_list(chat_id, "ограничение на запись")
            return None
        if "ошибка доступа к чату" in error_lower or "you don't have access to this chat" in error_lower or "access denied" in error_lower:
            logger.warning(f"Chat {chat_id}: ошибка доступа. Рассылка приостановлена.")
            return "access_error"
        logger.error(f"Ошибка отправки в чат {chat_id}: {exc}")
        return None

def upload_message_photo() -> str | None:
    """Принудительно загружает фото в VK при каждом вызове."""
    logger.info(f"upload_message_photo: начинаем загрузку, message_photo_path={message_photo_path}")
    
    if not message_photo_path:
        logger.warning("message_photo_path пуст")
        return None
    
    # Если уже строка вида photo123_456 — возвращаем как есть
    if isinstance(message_photo_path, str) and message_photo_path.startswith(("photo", "doc", "video")):
        logger.info(f"attachment уже готов: {message_photo_path}")
        return message_photo_path
    
    # Строим полный путь
    photo_path = Path(message_photo_path)
    if not photo_path.is_absolute():
        photo_path = BASE_DIR / photo_path
    
    logger.info(f"Проверяем файл: {photo_path.resolve()}")
    
    if not photo_path.exists():
        logger.error(f"Файл НЕ СУЩЕСТВУЕТ: {photo_path.resolve()}")
        return None
    
    if not photo_path.is_file():
        logger.error(f"Это не файл: {photo_path.resolve()}")
        return None
    
    try:
        logger.info("Пытаемся загрузить фото в VK...")
        photo = vk_upload.photo_messages(str(photo_path))[0]
        attachment = f"photo{photo['owner_id']}_{photo['id']}"
        logger.info(f"Фото успешно загружено: {attachment}")
        return attachment
    except Exception as e:
        logger.error(f"Ошибка загрузки фото: {type(e).__name__}: {e}")
        return None

def save_main_photo_from_message(message: dict[str, Any]) -> tuple[bool, str]:
    global message_photo_path, uploaded_message_photo
    attachments = message.get("attachments") or []
    if not attachments:
        return False, "Прикрепите фото к сообщению с командой .редоснфото"
    attachment_ref = extract_attachment_reference(message)
    if attachment_ref and attachment_ref.startswith("photo"):
        message_photo_path = attachment_ref
        uploaded_message_photo = attachment_ref
        save_runtime_data()
        return True, "✅ Основное фото бота успешно обновлено."
    photo_attachment = None
    for item in attachments:
        if item.get("type") == "photo":
            photo_attachment = item["photo"]
            break
    if not photo_attachment:
        return False, "Поддерживается только фото."

    sizes = photo_attachment.get("sizes") or []
    if not sizes:
        return False, "Не удалось получить размеры фото."
    best = max(sizes, key=lambda x: x.get("width", 0) * x.get("height", 0))
    url = best.get("url")
    if not url:
        return False, "Не удалось получить ссылку на фото."

    photos_dir = BASE_DIR / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    target = photos_dir / "main_photo.jpg"
    try:
        with urlopen(url) as response:
            target.write_bytes(response.read())
    except Exception as exc:
        return False, f"Не удалось сохранить фото: {exc}"

    message_photo_path = "photos/main_photo.jpg"
    uploaded_message_photo = None
    save_runtime_data()
    return True, "✅ Основное фото бота успешно обновлено."

def extract_attachment_reference(message: dict[str, Any]) -> str | None:
    attachments = message.get("attachments") or []
    if not attachments:
        return None
    raw = attachments[0]
    kind = raw.get("type")
    if kind not in {"photo", "video", "doc"}:
        return None
    item = raw[kind]
    reference = f"{kind}{item['owner_id']}_{item['id']}"
    if item.get("access_key"):
        reference = f"{reference}_{item['access_key']}"
    return reference

def send_broadcast_to_chat(chat_id: int) -> None:
    """Отправляет основное сообщение бота (текст + фото + кнопка)."""
    logger.info(f"Рассылка: отправка в чат {chat_id}")
    if message_text:
        result = send_message(
            chat_id,
            message_text,
            attachment=upload_message_photo(),
            keyboard=build_join_keyboard(),
        )
        if result == "access_error":
            logger.warning(f"Рассылка остановлена: потерян доступ к чату {chat_id}")
            return "access_error"
        time.sleep(interval_sec)
    return None

def broadcast_message(notify_chat_id: int | None = None) -> None:
    target_chats = [chat_id for chat_id in chat_ids if admin_chat is not None and chat_id != admin_chat and len(str(chat_id)) == 10 and str(chat_id).startswith("2")]
    logger.info(f"Рассылка запущена. Целевых чатов: {len(target_chats)}")
    if not target_chats:
        if notify_chat_id:
            send_message(notify_chat_id, "⚠️ Рассылка не запущена: список чатов пуст.")
        return
    sent_count = 0
    try:
        interrupted = False
        for chat_id in target_chats:
            result = send_broadcast_to_chat(chat_id)
            if result == "access_error":
                interrupted = True
                break
            sent_count += 1
        if interrupted:
            logger.warning(f"Рассылка приостановлена. Обработано чатов: {sent_count}")
            if notify_chat_id:
                send_message(notify_chat_id, f"⚠️ Рассылка остановлена из-за ошибки доступа.\nОбработано чатов: {sent_count}")
        else:
            logger.info(f"Рассылка завершена. Обработано чатов: {sent_count}")
            if notify_chat_id:
                send_message(notify_chat_id, f"✅ Рассылка завершена.\nОбработано чатов: {sent_count}")
    except Exception as exc:
        logger.exception(f"Критическая ошибка рассылки: {exc}")
        if notify_chat_id:
            send_message(notify_chat_id, f"❌ Ошибка рассылки: {exc}")

def start_broadcast(notify_chat_id: int | None = None) -> None:
    global broadcast_thread
    with broadcast_lock:
        if broadcast_thread and broadcast_thread.is_alive():
            logger.info("Попытка повторно запустить уже активную рассылку.")
            return
        logger.info("Создаю поток рассылки.")
        broadcast_thread = threading.Thread(target=broadcast_message, kwargs={"notify_chat_id": notify_chat_id}, daemon=True)
        broadcast_thread.start()

def send_gzov_to_chat(chat_id: int) -> Any:
    logger.info(f"GZOV: отправка в чат {chat_id}")
    return send_message(
        chat_id,
        message_text,
        attachment=upload_message_photo(),
        keyboard=build_join_keyboard(),
    )

def broadcast_gzov(notify_chat_id: int | None = None) -> None:
    target_chats = [chat_id for chat_id in chat_ids if admin_chat is not None and chat_id != admin_chat and len(str(chat_id)) == 10 and str(chat_id).startswith("2")]
    logger.info(f"GZOV запущен. Целевых чатов: {len(target_chats)}")
    if not target_chats:
        if notify_chat_id:
            send_message(notify_chat_id, "⚠️ GZOV не запущен: список чатов пуст.")
        return
    sent_count = 0
    try:
        interrupted = False
        for chat_id in target_chats:
            result = send_gzov_to_chat(chat_id)
            if result == "access_error":
                interrupted = True
                break
            sent_count += 1
            time.sleep(interval_sec)
        if interrupted:
            logger.warning(f"GZOV остановлен. Обработано чатов: {sent_count}")
            if notify_chat_id:
                send_message(notify_chat_id, f"⚠️ GZOV остановлен из-за ошибки доступа.\nОбработано чатов: {sent_count}")
        else:
            logger.info(f"GZOV завершён. Обработано чатов: {sent_count}")
            if notify_chat_id:
                send_message(notify_chat_id, f"✅ GZOV завершён.\nОбработано чатов: {sent_count}")
    except Exception as exc:
        logger.exception(f"Критическая ошибка GZOV: {exc}")
        if notify_chat_id:
            send_message(notify_chat_id, f"❌ Ошибка GZOV: {exc}")

def start_gzov(notify_chat_id: int | None = None) -> None:
    thread = threading.Thread(target=broadcast_gzov, kwargs={"notify_chat_id": notify_chat_id}, daemon=True)
    thread.start()

def auto_broadcast_loop() -> None:
    global last_broadcast_time
    while True:
        try:
            if time.time() - last_broadcast_time >= cd_min * 60:
                start_broadcast()
                last_broadcast_time = time.time()
        except Exception as exc:
            logger.error(f"Ошибка авторассылки по таймеру: {exc}")
        time.sleep(5)

def handle_group_info(chat_id: int) -> None:
    send_message(chat_id, "ℹ️ ИНФОРМАЦИЯ О НАС\n\nМы занимаемся продвижением каналов.\n📊 150+ чатов для рассылки")

def resolve_user_id(token: str) -> int | None:
    token = token.strip()
    mention_match = re.match(r"\[id(\d+)\|", token)
    if mention_match:
        return int(mention_match.group(1))
    direct_id = re.match(r"id(\d+)$", token, re.IGNORECASE)
    if direct_id:
        return int(direct_id.group(1))
    if token.startswith("@"):
        token = token[1:]
    try:
        user = vk.users.get(user_ids=token)
        if user:
            return int(user[0]["id"])
    except Exception as exc:
        logger.warning(f"Не удалось разрешить пользователя {token}: {exc}")
    return None

def extract_target_user(message: dict[str, Any], text: str) -> int | None:
    reply_message = message.get("reply_message")
    if isinstance(reply_message, dict) and reply_message.get("from_id"):
        return int(reply_message["from_id"])
    for raw_part in (text or "").replace("\n", " ").split():
        part = raw_part.strip(",")
        mention_match = re.match(r"\[id(\d+)\|", part)
        if mention_match:
            return int(mention_match.group(1))
        if part.startswith("@"):
            resolved = resolve_user_id(part[1:])
            if resolved:
                return resolved
        direct_id = re.match(r"id(\d+)$", part, re.IGNORECASE)
        if direct_id:
            return int(direct_id.group(1))
        if "vk.com/" in part or "vk.ru/" in part:
            tail = part.rstrip("/").split("/")[-1]
            resolved = resolve_user_id(tail)
            if resolved:
                return resolved
    return None

# ---------- основной цикл ----------
def main() -> None:
    global admin_chat, message_text, cd_min, interval_sec, auto_broadcast_thread, last_broadcast_time
    logger.info("Бот запущен (только рассылка основного сообщения с кнопкой)")
    last_broadcast_time = time.time()
    if auto_broadcast_thread is None or not auto_broadcast_thread.is_alive():
        auto_broadcast_thread = threading.Thread(target=auto_broadcast_loop, daemon=True)
        auto_broadcast_thread.start()

    for event in longpoll.listen():
        if event.type != VkBotEventType.MESSAGE_NEW:
            continue
        message = event.obj.message
        chat_id = message.get("peer_id")
        user_id = message.get("from_id")
        text = (message.get("text") or "").strip()

        # Игнорируем личные сообщения
        if chat_id == user_id:
            continue

        # Административные команды доступны только в админ-чате
        if chat_id != admin_chat and not has_permission(user_id, "admin"):
            continue

        # ---------- команды ----------
        if text == ".пинг":
            start_time = time.time()
            send_message(chat_id, "Проверка пинга...")
            end_time = time.time()
            ping_time = int((end_time - start_time) * 1000)
            send_message(chat_id, f"Пинг: {ping_time}ms")
            update_user_stats(user_id, "command")
            continue

        if text.startswith(".стата"):
            target_user_id = extract_target_user(message, text) or user_id
            send_message(chat_id, render_user_stats_detailed(target_user_id))
            update_user_stats(user_id, "command")
            continue

        if text == ".хелп":
            role = get_role(user_id)
            send_message(chat_id, get_help_text(role))
            update_user_stats(user_id, "command")
            continue

        if text == ".список":
            total_chats = len(chat_ids)
            chat_list = "\n".join(str(cid) for cid in chat_ids if cid != admin_chat)
            send_message(chat_id, f"Количество чатов для рассылки: {total_chats}\nСписок чатов:\n{chat_list}")
            update_user_stats(user_id, "command")
            continue

        if text == ".ид":
            send_message(chat_id, f"✅ ID этой беседы: {chat_id}")
            update_user_stats(user_id, "command")
            continue

        if text == ".инфо":
            send_message(chat_id, render_runtime_info_legacy())
            update_user_stats(user_id, "command")
            continue

        if text == ".списокрекламы":
            send_message(chat_id, "Активных рекламных объявлений нет (система заказов удалена).")
            update_user_stats(user_id, "command")
            continue

        if text == ".уст":
            if len(str(chat_id)) == 10 and str(chat_id).startswith("2"):
                if chat_id not in chat_ids:
                    chat_ids.append(chat_id)
                    save_runtime_data()
                    send_message(chat_id, "Этот чат добавлен в список для рассылки сообщений.")
                else:
                    send_message(chat_id, "❌ Этот чат уже в списке рассылки.")
            else:
                send_message(chat_id, "Невозможно добавить этот чат: это не беседа.")
            update_user_stats(user_id, "command")
            continue

        if text == ".инфочат":
            send_message(chat_id, f"📋 Информация о чате:\n\n🔹 ID чата: {chat_id}\n🔹 ID отправителя: {user_id}\n🔹 Текущий административный чат: {admin_chat if admin_chat else 'Не установлен'}")
            update_user_stats(user_id, "command")
            continue

        if text.startswith(".добид"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "Использование: .добид [число]")
                continue
            try:
                count = int(parts[1])
                if count <= 0:
                    raise ValueError
            except ValueError:
                send_message(chat_id, "Количество должно быть положительным числом.")
                continue
            bot_chat_ids = [cid for cid in chat_ids if str(cid).startswith("2") and len(str(cid)) == 10]
            next_chat_id = max(bot_chat_ids, default=2000000000) + 1
            for _ in range(count):
                chat_ids.append(next_chat_id)
                next_chat_id += 1
            save_runtime_data()
            send_message(chat_id, f"✅ Добавлено {count} чатов в список.")
            update_user_stats(user_id, "command")
            continue

        if text.startswith(".делид"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "Использование: .делид [число]")
                continue
            try:
                count = int(parts[1])
                if count <= 0:
                    raise ValueError
            except ValueError:
                send_message(chat_id, "Количество должно быть положительным числом.")
                continue
            bot_chat_ids = [cid for cid in chat_ids if str(cid).startswith("2") and len(str(cid)) == 10]
            removed = 0
            for _ in range(min(count, len(bot_chat_ids))):
                if bot_chat_ids:
                    chat_to_remove = bot_chat_ids.pop()
                    if chat_to_remove in chat_ids:
                        chat_ids.remove(chat_to_remove)
                        removed += 1
            if removed:
                save_runtime_data()
            send_message(chat_id, f"✅ Удалено {removed} чатов с конца списка.")
            update_user_stats(user_id, "command")
            continue

        if text == ".доходы" and has_permission(user_id, "dev"):
            send_message(chat_id, "📊 Статистика доходов разработчика\n\n(система заказов удалена)")
            update_user_stats(user_id, "command")
            continue

        if text == ".тест":
            send_broadcast_to_chat(chat_id)
            update_user_stats(user_id, "command")
            continue

        if text == ".рассылка":
            start_broadcast(notify_chat_id=chat_id)
            last_broadcast_time = time.time()
            send_message(chat_id, "✅ Рассылка запущена и таймер сброшен.")
            update_user_stats(user_id, "command")
            continue

        if text == ".gzov" and has_permission(user_id, "dev"):
            start_gzov(notify_chat_id=chat_id)
            send_message(chat_id, "✅ GZOV запущен.")
            update_user_stats(user_id, "command")
            continue

        if text == ".инфо_о_нас":
            handle_group_info(chat_id)
            continue

        if text.startswith(".редоснтекст") and has_permission(user_id, "dev"):
            if chat_id != admin_chat:
                send_message(chat_id, "❌ Эта команда доступна только в админ-чате.")
                continue
            if not has_permission(user_id, "dev"):
                send_message(chat_id, "❌ У вас нет прав на выполнение этой команды.")
                continue
            parts = text.split(" ", 1)
            if len(parts) < 2 or not parts[1].strip():
                send_message(chat_id, "Неверный формат команды. Используйте: .редоснтекст [текст]")
                continue
            message_text = parts[1].strip()
            save_runtime_data()
            send_message(chat_id, message_text, attachment=upload_message_photo())
            update_user_stats(user_id, "osn_text")
            continue

        if text == ".админчат" and has_permission(user_id, "dev"):
            if admin_chat == chat_id:
                send_message(chat_id, "⚠️ Этот чат уже является административным.")
            else:
                admin_chat = chat_id
                save_runtime_data()
                send_message(chat_id, "Административный чат установлен.")
            continue

        if text == ".стафф" and user_id == OWNER_ID:
            send_message(chat_id, render_staff_detailed())
            continue

        if text == ".редоснфото":
            if user_id == OWNER_ID:
                if chat_id != admin_chat:
                    send_message(chat_id, "❌ Эта команда доступна только в админ-чате.")
                    continue
                ok, result_text = save_main_photo_from_message(message)
                send_message(chat_id, result_text if ok else ("❌ Прикрепите именно фото." if "Поддерживается только фото" in result_text else result_text))
                if ok:
                    update_user_stats(user_id, "osn_photo")
            else:
                send_message(chat_id, "❌ У вас нет прав на выполнение этой команды.")
            continue

        if text.startswith(".настройки") and has_permission(user_id, "dev"):
            parts = text.split()
            if len(parts) != 3:
                send_message(chat_id, "❌ Неверный формат. Используйте: .настройки [cd_min|interval_sec] [число]")
                continue
            key = parts[1]
            try:
                value = float(parts[2])
            except ValueError:
                send_message(chat_id, "❌ Значение должно быть числом.")
                continue
            if key == "cd_min":
                if value < 1 or value > 1440:
                    send_message(chat_id, "❌ КД должно быть от 1 до 1440 минут.")
                    continue
                cd_min = int(value)
                save_config_value("cd_min", str(cd_min))
                send_message(chat_id, f"✅ Установлено: cd_min = {cd_min} мин")
            elif key == "interval_sec":
                if value < 0 or value > 60:
                    send_message(chat_id, "❌ Интервал должен быть от 0 до 60 секунд.")
                    continue
                interval_sec = float(value)
                save_config_value("interval_sec", str(interval_sec))
                send_message(chat_id, f"✅ Установлено: interval_sec = {interval_sec} сек")
            else:
                send_message(chat_id, "❌ Доступные ключи: cd_min, interval_sec")
                continue
            update_user_stats(user_id, "command")
            continue

        if text.startswith(".админ") and has_permission(user_id, "dev"):
            target_user_id = extract_target_user(message, text)
            if not target_user_id:
                send_message(chat_id, "❌ Укажите пользователя: ответом, @ или ссылкой.")
                continue
            if user_id == target_user_id:
                send_message(chat_id, "❌ Нельзя снимать права администратора с себя через эту команду.")
                continue
            users = load_users()
            user_entry = users.setdefault(str(target_user_id), {"role": "user", "osn_photo_count": 0, "osn_text_count": 0, "total_messages": 0, "last_message": ""})
            current_role = user_entry.get("role")
            if current_role == "admin":
                user_entry["role"] = "user"
                save_users(users)
                send_message(chat_id, f"❌ Права администратора сняты у пользователя {target_user_id}.")
            else:
                user_entry["role"] = "admin"
                save_users(users)
                send_message(chat_id, f"✅ Пользователь {target_user_id} назначен администратором.")
            update_user_stats(user_id, "command")
            continue

        if text.startswith(".разраб") and user_id == OWNER_ID:
            target_user_id = extract_target_user(message, text)
            if not target_user_id:
                send_message(chat_id, "❌ Укажите пользователя: ответом, @ или ссылкой.")
                continue
            users = load_users()
            user_key = str(target_user_id)
            current_role = users.get(user_key, {}).get("role", "user")
            if current_role == "dev":
                users.setdefault(user_key, {"role": "user", "osn_photo_count": 0, "osn_text_count": 0, "total_messages": 0, "last_message": ""})
                users[user_key]["role"] = "user"
                save_users(users)
                try:
                    user_info = vk.users.get(user_ids=target_user_id)[0]
                    full_name = f"{user_info['first_name']} {user_info['last_name']}"
                    send_message(chat_id, f"❌ Права разработчика сняты у [id{target_user_id}|{full_name}]")
                except Exception as e:
                    send_message(chat_id, f"❌ Права разработчика сняты у пользователя {target_user_id}. Произошла ошибка при получении имени: {e}")
            else:
                users.setdefault(user_key, {"role": "user", "osn_photo_count": 0, "osn_text_count": 0, "total_messages": 0, "last_message": ""})
                users[user_key]["role"] = "dev"
                save_users(users)
                try:
                    user_info = vk.users.get(user_ids=target_user_id)[0]
                    full_name = f"{user_info['first_name']} {user_info['last_name']}"
                    send_message(chat_id, f"✅ [id{target_user_id}|{full_name}] назначен(а) разработчиком.")
                except Exception as e:
                    send_message(chat_id, f"✅ Пользователь {target_user_id} назначен разработчиком. Произошла ошибка при получении имени: {e}")
            continue

if __name__ == "__main__":
    main()