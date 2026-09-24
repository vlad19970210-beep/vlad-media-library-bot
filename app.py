import os
import json
import random
import requests
import redis
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
REDIS_URL = os.environ["REDIS_URL"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Твой Telegram-канал
CHANNEL_ID = -1003148826053

# Максимум результатов за один поиск
SEARCH_LIMIT = 50

# Лимит Redis для расчёта /storage
REDIS_LIMIT_MB = 50

db = redis.from_url(
    REDIS_URL,
    decode_responses=True
)


# =========================================================
# TELEGRAM
# =========================================================

def tg(method, data=None):
    try:
        response = requests.post(
            f"{TELEGRAM_API}/{method}",
            json=data or {},
            timeout=20
        )

        print(
            f"Telegram {method}: "
            f"{response.status_code} "
            f"{response.text[:500]}",
            flush=True
        )

        return response.json()

    except Exception as e:
        print("Telegram error:", e, flush=True)
        return None


def send_message(chat_id, text):
    return tg(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def copy_message(chat_id, from_chat_id, message_id):
    return tg(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id
        }
    )


def send_media(chat_id, item):
    """
    Отправляем материал по сохранённому Telegram file_id.
    """

    media_type = item.get("media_type")
    file_id = item.get("file_id")

    if not file_id:
        send_message(
            chat_id,
            "⚠️ У материала отсутствует file_id."
        )
        return None

    result = None

    if media_type == "photo":
        result = tg(
            "sendPhoto",
            {
                "chat_id": chat_id,
                "photo": file_id
            }
        )

    elif media_type == "image_document":
        result = tg(
            "sendDocument",
            {
                "chat_id": chat_id,
                "document": file_id
            }
        )

    elif media_type == "video":
        result = tg(
            "sendVideo",
            {
                "chat_id": chat_id,
                "video": file_id
            }
        )

    elif media_type == "video_document":
        result = tg(
            "sendDocument",
            {
                "chat_id": chat_id,
                "document": file_id
            }
        )

    elif media_type == "animation":
        result = tg(
            "sendAnimation",
            {
                "chat_id": chat_id,
                "animation": file_id
            }
        )

    else:
        send_message(
            chat_id,
            "⚠️ Неизвестный тип материала."
        )
        return None

    # Если отправка по file_id не сработала,
    # пробуем исходный пост канала.
    if (
        not result
        or result.get("ok") is not True
    ):
        if (
            item.get("source") == "channel"
            and item.get("message_id")
        ):
            return copy_message(
                chat_id,
                CHANNEL_ID,
                item["message_id"]
            )

    return result


# =========================================================
# TAGS
# =========================================================

def normalize_tags(text):
    text = text.strip().lower()

    if "," in text:
        tags = [
            x.strip().lstrip("#")
            for x in text.split(",")
        ]
    else:
        tags = [
            x.strip().lstrip("#")
            for x in text.split()
        ]

    tags = [
        tag
        for tag in tags
        if tag
    ]

    return list(dict.fromkeys(tags))


# =========================================================
# OWNER
# =========================================================

def get_owner():
    owner = db.get("owner_chat_id")

    if owner:
        return int(owner)

    return None


# =========================================================
# MEDIA DETECTION
# =========================================================

def detect_media(message):
    """
    Определяем тип присланного материала.
    """

    # Обычная фотография
    photos = message.get("photo")

    if photos:
        photo = photos[-1]

        return (
            "photo",
            photo.get("file_id")
        )

    # Обычное видео
    video = message.get("video")

    if video:
        return (
            "video",
            video.get("file_id")
        )

    # GIF / animation
    animation = message.get("animation")

    if animation:
        return (
            "animation",
            animation.get("file_id")
        )

    # Файл / документ
    document = message.get("document")

    if document:
        mime = document.get(
            "mime_type",
            ""
        )

        if mime.startswith("video/"):
            return (
                "video_document",
                document.get("file_id")
            )

        if mime.startswith("image/"):
            return (
                "image_document",
                document.get("file_id")
            )

    return None, None


# =========================================================
# DATABASE
# =========================================================

def save_item(item, tags):
    item_id = item["item_id"]

    saved = {
        "item_id": item_id,
        "source": item["source"],
        "message_id": item.get("message_id"),
        "source_chat_id": item.get("source_chat_id"),
        "file_id": item["file_id"],
        "media_type": item["media_type"],
        "tags": tags
    }

    db.set(
        f"media:{item_id}",
        json.dumps(
            saved,
            ensure_ascii=False
        )
    )

    db.sadd(
        "media_items",
        item_id
    )

    for tag in tags:
        db.sadd(
            f"tag:{tag}",
            item_id
        )


def get_item(item_id):
    # Современная схема
    raw = db.get(
        f"media:{item_id}"
    )

    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return None

    # Совместимость со старыми video:* записями
    if str(item_id).isdigit():
        legacy_raw = db.get(
            f"video:{item_id}"
        )

        if legacy_raw:
            try:
                old = json.loads(
                    legacy_raw
                )

                return {
                    "item_id": str(item_id),
                    "source": "legacy",
                    "message_id": None,
                    "source_chat_id": None,
                    "file_id": old.get("file_id"),
                    "media_type": "video",
                    "tags": old.get("tags", [])
                }

            except Exception:
                return None

    return None


# =========================================================
# QUEUE
# =========================================================

def queue_contains(item_id):
    for raw in db.lrange(
        "media_queue",
        0,
        -1
    ):
        try:
            item = json.loads(raw)

            if item.get("item_id") == item_id:
                return True

        except Exception:
            pass

    return False


def add_to_queue(item):
    item_id = item["item_id"]

    if db.exists(
        f"media:{item_id}"
    ):
        return False

    if queue_contains(item_id):
        return False

    was_empty = (
        db.llen("media_queue") == 0
    )

    db.rpush(
        "media_queue",
        json.dumps(
            item,
            ensure_ascii=False
        )
    )

    return was_empty


def current_pending():
    raw = db.lindex(
        "media_queue",
        0
    )

    if not raw:
        return None

    try:
        return json.loads(raw)

    except Exception:
        return None


def ask_for_next_tags():
    owner = get_owner()

    if not owner:
        return

    item = current_pending()

    if not item:
        send_message(
            owner,
            "✅ Очередь пуста."
        )
        return

    queue_size = db.llen(
        "media_queue"
    )

    media_name = {
        "photo": "📷 Фото",
        "image_document": "📷 Изображение",
        "video": "🎬 Видео",
        "video_document": "🎬 Видео",
        "animation": "🎞 GIF"
    }.get(
        item["media_type"],
        "📁 Материал"
    )

    if item.get("source") == "channel":
        source_name = "из канала"
    else:
        source_name = "импортировано через бота"

    send_message(
        owner,
        f"{media_name} — {source_name}\n\n"
        f"В очереди: {queue_size}\n\n"
        "Напиши теги через запятую.\n\n"
        "Например:\n"
        "отпуск, море, избранное\n\n"
        "/skip — отложить\n"
        "/delete — не добавлять"
    )

    send_media(
        owner,
        item
    )


# =========================================================
# SEARCH
# =========================================================

def search_items(tags):
    """
    Находим материалы со ВСЕМИ указанными тегами
    и случайно перемешиваем результаты.
    """

    if not tags:
        return []

    keys = [
        f"tag:{tag}"
        for tag in tags
    ]

    ids = db.sinter(*keys)

    items = []

    for item_id in ids:
        item = get_item(item_id)

        if item:
            items.append(item)

    # Новое случайное перемешивание
    # при каждом поиске
    random.shuffle(items)

    return items


# =========================================================
# STORAGE
# =========================================================

def get_storage_info():
    """
    Получаем использование памяти Redis
    и статистику каталога.
    """

    try:
        info = db.info("memory")

        used_bytes = int(
            info.get(
                "used_memory",
                0
            )
        )

        used_mb = (
            used_bytes
            / 1024
            / 1024
        )

        percent = (
            used_mb
            / REDIS_LIMIT_MB
            * 100
        )

        total_items = db.scard(
            "media_items"
        )

        legacy_items = db.scard(
            "videos"
        )

        queue_items = db.llen(
            "media_queue"
        )

        tag_count = sum(
            1
            for _ in db.scan_iter(
                "tag:*"
            )
        )

        # Считаем память, занятую непосредственно
        # нашими записями и индексами.
        catalog_bytes = 0

        patterns = [
            "media:*",
            "video:*",
            "tag:*"
        ]

        checked_keys = set()

        for pattern in patterns:
            for key in db.scan_iter(pattern):
                if key in checked_keys:
                    continue

                checked_keys.add(key)

                try:
                    catalog_bytes += (
                        db.memory_usage(key)
                        or 0
                    )
                except Exception:
                    pass

        # Служебные ключи
        service_keys = [
            "media_items",
            "videos",
            "media_queue",
            "owner_chat_id",
            "next_import_id"
        ]

        for key in service_keys:
            if key in checked_keys:
                continue

            try:
                catalog_bytes += (
                    db.memory_usage(key)
                    or 0
                )
            except Exception:
                pass

        # Учитываем новые + старые записи
        # только для оценки среднего размера.
        count_for_estimate = (
            total_items
            + legacy_items
        )

        estimated_left = None
        avg_bytes = None

        if (
            count_for_estimate >= 20
            and catalog_bytes > 0
        ):
            avg_bytes = (
                catalog_bytes
                / count_for_estimate
            )

            limit_bytes = (
                REDIS_LIMIT_MB
                * 1024
                * 1024
            )

            free_bytes = max(
                0,
                limit_bytes - used_bytes
            )

            if avg_bytes > 0:
                estimated_left = int(
                    free_bytes
                    / avg_bytes
                )

        return {
            "used_bytes": used_bytes,
            "used_mb": used_mb,
            "limit_mb": REDIS_LIMIT_MB,
            "percent": percent,
            "total_items": total_items,
            "legacy_items": legacy_items,
            "queue_items": queue_items,
            "tag_count": tag_count,
            "catalog_bytes": catalog_bytes,
            "avg_bytes": avg_bytes,
            "estimated_left": estimated_left
        }

    except Exception as e:
        print(
            "Storage info error:",
            e,
            flush=True
        )

        return None


# =========================================================
# WEB
# =========================================================

@app.route("/", methods=["GET"])
def home():
    try:
        db.ping()
        database = "connected"

    except Exception as e:
        database = f"error: {e}"

    return (
        "Vlad Media Library Bot is running. "
        f"Database: {database}"
    )


@app.route("/setup", methods=["GET"])
def setup():
    webhook_url = (
        request.url_root.rstrip("/")
        + "/webhook"
    )

    response = requests.get(
        f"{TELEGRAM_API}/setWebhook",
        params={
            "url": webhook_url
        },
        timeout=20
    )

    return jsonify(
        response.json()
    )


@app.route("/checkbot", methods=["GET"])
def checkbot():
    response = requests.get(
        f"{TELEGRAM_API}/getMe",
        timeout=20
    )

    return jsonify(
        response.json()
    )


# =========================================================
# WEBHOOK
# =========================================================

@app.route("/webhook", methods=["POST"])
def webhook():
    update = (
        request.get_json(
            silent=True
        )
        or {}
    )

    print(
        "UPDATE:",
        json.dumps(
            update,
            ensure_ascii=False
        )[:4000],
        flush=True
    )

    # =====================================================
    # НОВЫЙ ПОСТ В КАНАЛЕ
    # =====================================================

    post = update.get(
        "channel_post"
    )

    if post:
        channel_id = (
            post.get(
                "chat",
                {}
            ).get("id")
        )

        if channel_id != CHANNEL_ID:
            return "OK", 200

        media_type, file_id = detect_media(
            post
        )

        if not file_id:
            return "OK", 200

        message_id = post[
            "message_id"
        ]

        item = {
            "item_id": f"channel:{message_id}",
            "source": "channel",
            "source_chat_id": CHANNEL_ID,
            "message_id": message_id,
            "file_id": file_id,
            "media_type": media_type
        }

        was_empty = add_to_queue(
            item
        )

        if was_empty:
            ask_for_next_tags()

        return "OK", 200


    # =====================================================
    # ЛИЧНЫЕ СООБЩЕНИЯ
    # =====================================================

    message = update.get(
        "message"
    )

    if not message:
        return "OK", 200

    chat = message.get(
        "chat",
        {}
    )

    if chat.get("type") != "private":
        return "OK", 200

    chat_id = chat["id"]

    text = (
        message.get("text")
        or ""
    ).strip()


    # =====================================================
    # START
    # =====================================================

    if text == "/start":
        owner = get_owner()

        if owner is None:
            db.set(
                "owner_chat_id",
                chat_id
            )

        elif owner != chat_id:
            send_message(
                chat_id,
                "⛔ Это личная медиатека."
            )

            return "OK", 200

        send_message(
            chat_id,
            "🎬📷 Vlad Media Library\n\n"
            "НОВЫЕ МАТЕРИАЛЫ:\n"
            "пересылай фото или видео в канал.\n\n"
            "СТАРЫЕ МАТЕРИАЛЫ:\n"
            "пересылай фото или видео сюда, "
            "в личку боту.\n\n"
            "Я попрошу теги и добавлю материал "
            "в каталог.\n\n"
            f"ПОИСК:\n"
            f"до {SEARCH_LIMIT} случайных "
            "материалов за запрос.\n\n"
            "Команды:\n"
            "/search тег — фото + видео\n"
            "/photo тег — только фото\n"
            "/video тег — только видео\n"
            "/tags — все теги\n"
            "/count — статистика\n"
            "/storage — память базы\n"
            "/queue — очередь\n"
            "/next — текущий материал\n"
            "/skip — отложить\n"
            "/delete — убрать из очереди"
        )

        if db.llen("media_queue") > 0:
            ask_for_next_tags()

        return "OK", 200


    # =====================================================
    # ПРОВЕРКА ВЛАДЕЛЬЦА
    # =====================================================

    owner = get_owner()

    if owner != chat_id:
        send_message(
            chat_id,
            "⛔ Нет доступа."
        )

        return "OK", 200


    # =====================================================
    # ИМПОРТ ФОТО/ВИДЕО ЧЕРЕЗ ЛИЧКУ БОТА
    # =====================================================

    media_type, file_id = detect_media(
        message
    )

    if file_id:
        import_id = db.incr(
            "next_import_id"
        )

        item = {
            "item_id": f"import:{import_id}",
            "source": "import",
            "source_chat_id": chat_id,
            "message_id": message.get(
                "message_id"
            ),
            "file_id": file_id,
            "media_type": media_type
        }

        was_empty = add_to_queue(
            item
        )

        if was_empty:
            ask_for_next_tags()

        else:
            queue_size = db.llen(
                "media_queue"
            )

            send_message(
                chat_id,
                "✅ Добавил в очередь.\n"
                f"Сейчас в очереди: {queue_size}"
            )

        return "OK", 200


    # =====================================================
    # COUNT
    # =====================================================

    if text == "/count":
        new_ids = db.smembers(
            "media_items"
        )

        legacy_ids = db.smembers(
            "videos"
        )

        photos = 0
        videos = 0

        for item_id in new_ids:
            item = get_item(item_id)

            if not item:
                continue

            media_type = item.get(
                "media_type"
            )

            if media_type in (
                "photo",
                "image_document"
            ):
                photos += 1

            elif media_type in (
                "video",
                "video_document",
                "animation"
            ):
                videos += 1

        # Старые записи video:* считаем видео
        videos += len(legacy_ids)

        total = (
            len(new_ids)
            + len(legacy_ids)
        )

        queue = db.llen(
            "media_queue"
        )

        send_message(
            chat_id,
            f"📚 Всего: {total}\n\n"
            f"📷 Фото: {photos}\n"
            f"🎬 Видео: {videos}\n"
            f"⏳ Без тегов: {queue}"
        )

        return "OK", 200


    # =====================================================
    # STORAGE
    # =====================================================

    if text == "/storage":
        storage = get_storage_info()

        if not storage:
            send_message(
                chat_id,
                "⚠️ Не удалось получить "
                "информацию о хранилище."
            )

            return "OK", 200

        used_mb = storage[
            "used_mb"
        ]

        limit_mb = storage[
            "limit_mb"
        ]

        percent = storage[
            "percent"
        ]

        total_items = (
            storage["total_items"]
            + storage["legacy_items"]
        )

        queue_items = storage[
            "queue_items"
        ]

        tag_count = storage[
            "tag_count"
        ]

        estimated_left = storage[
            "estimated_left"
        ]

        # Индикатор заполнения
        blocks = 10

        filled = round(
            percent
            / 100
            * blocks
        )

        filled = max(
            0,
            min(
                blocks,
                filled
            )
        )

        bar = (
            "🟩" * filled
            + "⬜" * (
                blocks - filled
            )
        )

        message_text = (
            "💾 Хранилище медиатеки\n\n"
            f"{bar}\n\n"
            f"Использовано Redis: "
            f"{used_mb:.2f} МБ\n"
            f"Расчётный лимит: "
            f"{limit_mb} МБ\n"
            f"Заполнено: "
            f"{percent:.1f}%\n\n"
            f"📚 Материалов: "
            f"{total_items}\n"
            f"🏷 Тегов: "
            f"{tag_count}\n"
            f"⏳ В очереди: "
            f"{queue_items}"
        )

        if estimated_left is not None:
            left_formatted = (
                f"{estimated_left:,}"
                .replace(",", " ")
            )

            message_text += (
                "\n\n"
                "📊 При текущем среднем "
                "расходе базы:\n"
                f"≈ ещё {left_formatted} "
                "материалов"
            )

        else:
            message_text += (
                "\n\n"
                "📊 Точный прогноз количества "
                "оставшихся материалов появится, "
                "когда в базе будет достаточно "
                "данных для оценки."
            )

        send_message(
            chat_id,
            message_text
        )

        return "OK", 200


    # =====================================================
    # QUEUE
    # =====================================================

    if text == "/queue":
        queue = db.llen(
            "media_queue"
        )

        send_message(
            chat_id,
            f"⏳ В очереди: {queue}"
        )

        return "OK", 200


    # =====================================================
    # NEXT
    # =====================================================

    if text == "/next":
        if db.llen("media_queue") == 0:
            send_message(
                chat_id,
                "✅ Очередь пустая."
            )

        else:
            ask_for_next_tags()

        return "OK", 200


    # =====================================================
    # SKIP
    # =====================================================

    if text == "/skip":
        raw = db.lpop(
            "media_queue"
        )

        if raw:
            db.rpush(
                "media_queue",
                raw
            )

            send_message(
                chat_id,
                "⏭ Перенёс материал "
                "в конец очереди."
            )

            ask_for_next_tags()

        else:
            send_message(
                chat_id,
                "Очередь пустая."
            )

        return "OK", 200


    # =====================================================
    # DELETE
    # =====================================================

    if text == "/delete":
        raw = db.lpop(
            "media_queue"
        )

        if raw:
            send_message(
                chat_id,
                "🗑 Убрал материал из очереди.\n\n"
                "Само фото/видео не удалено."
            )

            if db.llen("media_queue") > 0:
                ask_for_next_tags()

        else:
            send_message(
                chat_id,
                "Очередь пустая."
            )

        return "OK", 200


    # =====================================================
    # TAGS
    # =====================================================

    if text == "/tags":
        tags = []

        for key in db.scan_iter(
            "tag:*"
        ):
            tag = key[4:]

            count = db.scard(
                key
            )

            tags.append(
                (
                    tag,
                    count
                )
            )

        tags.sort(
            key=lambda x: (
                -x[1],
                x[0]
            )
        )

        if not tags:
            send_message(
                chat_id,
                "Пока тегов нет."
            )

        else:
            lines = [
                f"#{tag} — {count}"
                for tag, count
                in tags[:100]
            ]

            send_message(
                chat_id,
                "🏷 Теги:\n\n"
                + "\n".join(lines)
            )

        return "OK", 200


    # =====================================================
    # SEARCH
    # =====================================================

    search_mode = None
    query = None

    if text.startswith("/search"):
        search_mode = "all"

        query = text[
            len("/search"):
        ].strip()

    elif text.startswith("/photo"):
        search_mode = "photo"

        query = text[
            len("/photo"):
        ].strip()

    elif text.startswith("/video"):
        search_mode = "video"

        query = text[
            len("/video"):
        ].strip()


    if search_mode:
        if not query:
            send_message(
                chat_id,
                "Укажи тег.\n\n"
                "Например:\n"
                "/search море\n"
                "/photo море\n"
                "/video море"
            )

            return "OK", 200

        tags = normalize_tags(
            query
        )

        # Находим и перемешиваем результаты
        items = search_items(
            tags
        )

        # Только фотографии
        if search_mode == "photo":
            items = [
                item
                for item in items
                if item.get("media_type")
                in (
                    "photo",
                    "image_document"
                )
            ]

        # Только видео
        elif search_mode == "video":
            items = [
                item
                for item in items
                if item.get("media_type")
                in (
                    "video",
                    "video_document",
                    "animation"
                )
            ]

        if not items:
            send_message(
                chat_id,
                "🔎 Ничего не найдено."
            )

            return "OK", 200

        total_found = len(items)

        # Случайные 50 максимум
        selected_items = items[
            :SEARCH_LIMIT
        ]

        send_message(
            chat_id,
            f"🎲 Найдено: {total_found}\n"
            f"Показываю: "
            f"{len(selected_items)} "
            "в случайном порядке."
        )

        for item in selected_items:
            send_media(
                chat_id,
                item
            )

            item_tags = item.get(
                "tags",
                []
            )

            if item_tags:
                formatted_tags = ", ".join(
                    "#" + tag
                    for tag in item_tags
                )

                send_message(
                    chat_id,
                    f"🏷 {formatted_tags}"
                )

        if total_found > SEARCH_LIMIT:
            send_message(
                chat_id,
                f"🎲 Показаны случайные "
                f"{SEARCH_LIMIT} из "
                f"{total_found}.\n\n"
                "Повтори тот же поиск — "
                "бот заново перемешает "
                "всю подборку."
            )

        return "OK", 200


    # =====================================================
    # ВВОД ТЕГОВ
    # =====================================================

    pending = current_pending()

    if pending and text:
        tags = normalize_tags(
            text
        )

        if not tags:
            send_message(
                chat_id,
                "Не смог распознать теги."
            )

            return "OK", 200

        save_item(
            pending,
            tags
        )

        db.lpop(
            "media_queue"
        )

        formatted_tags = ", ".join(
            "#" + tag
            for tag in tags
        )

        if pending.get(
            "media_type"
        ) in (
            "photo",
            "image_document"
        ):
            media_name = "Фото"

        else:
            media_name = "Видео"

        send_message(
            chat_id,
            f"✅ {media_name} сохранено!\n\n"
            f"🏷 {formatted_tags}"
        )

        if db.llen("media_queue") > 0:
            ask_for_next_tags()

        else:
            send_message(
                chat_id,
                "🎉 Очередь полностью разобрана."
            )

        return "OK", 200


    # =====================================================
    # UNKNOWN
    # =====================================================

    if text:
        send_message(
            chat_id,
            "Для поиска используй:\n"
            "/search тег\n\n"
            "Или перешли старое "
            "фото/видео прямо мне."
        )

    return "OK", 200


if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
