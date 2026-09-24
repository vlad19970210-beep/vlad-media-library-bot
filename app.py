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

# Сколько случайных результатов показывать за один поиск
SEARCH_LIMIT = 50

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
        print(
            "Telegram error:",
            e,
            flush=True
        )
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

    Благодаря этому после добавления в каталог
    исходный пост в канале можно удалить.

    Если file_id не сработает, пробуем скопировать
    исходный пост из канала.
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

    # ФОТО
    if media_type == "photo":
        result = tg(
            "sendPhoto",
            {
                "chat_id": chat_id,
                "photo": file_id
            }
        )

    # ВИДЕО
    elif media_type == "video":
        result = tg(
            "sendVideo",
            {
                "chat_id": chat_id,
                "video": file_id
            }
        )

    # ВИДЕО КАК ДОКУМЕНТ
    elif media_type == "video_document":
        result = tg(
            "sendDocument",
            {
                "chat_id": chat_id,
                "document": file_id
            }
        )

    # GIF
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

    # Резервный вариант
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

    # Убираем повторяющиеся теги
    return list(dict.fromkeys(tags))


# =========================================================
# OWNER
# =========================================================

def get_owner():
    owner = db.get(
        "owner_chat_id"
    )

    if owner:
        return int(owner)

    return None


# =========================================================
# MEDIA DETECTION
# =========================================================

def detect_media(message):
    """
    Определяем фото, видео или GIF.
    """

    # ФОТО
    photos = message.get("photo")

    if photos:
        # Последний элемент —
        # самая большая версия фотографии
        photo = photos[-1]

        return (
            "photo",
            photo.get("file_id")
        )

    # ВИДЕО
    video = message.get("video")

    if video:
        return (
            "video",
            video.get("file_id")
        )

    # GIF
    animation = message.get("animation")

    if animation:
        return (
            "animation",
            animation.get("file_id")
        )

    # ДОКУМЕНТ
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
                "photo",
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

    # Создаём индекс тегов
    for tag in tags:
        db.sadd(
            f"tag:{tag}",
            item_id
        )


def get_item(item_id):
    raw = db.get(
        f"media:{item_id}"
    )

    if not raw:
        return None

    try:
        return json.loads(raw)

    except Exception:
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

    # Уже сохранён
    if db.exists(
        f"media:{item_id}"
    ):
        return False

    # Уже ждёт тегов
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

    # Показываем материал, которому сейчас задаём теги
    send_media(
        owner,
        item
    )


# =========================================================
# SEARCH
# =========================================================

def search_items(tags):
    """
    Находим материалы, содержащие ВСЕ указанные теги.

    После поиска весь список случайно перемешивается.
    """

    if not tags:
        return []

    keys = [
        f"tag:{tag}"
        for tag in tags
    ]

    ids = db.sinter(
        *keys
    )

    items = []

    for item_id in ids:
        item = get_item(
            item_id
        )

        if item:
            items.append(item)

    # Настоящее случайное перемешивание
    # при каждом новом запросе
    random.shuffle(items)

    return items


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

        # Игнорируем другие каналы
        if channel_id != CHANNEL_ID:
            return "OK", 200

        media_type, file_id = (
            detect_media(post)
        )

        # Обычный текстовый пост
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
    # ЛИЧНЫЕ СООБЩЕНИЯ БОТУ
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

    # Работаем только в личке
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
            "ПОИСК:\n"
            f"за один запрос показываю до "
            f"{SEARCH_LIMIT} случайных материалов.\n\n"
            "Команды:\n"
            "/search тег — фото + видео\n"
            "/photo тег — только фото\n"
            "/video тег — только видео\n"
            "/tags — все теги\n"
            "/count — статистика\n"
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
    # ИМПОРТ СТАРОГО ФОТО/ВИДЕО ЧЕРЕЗ БОТА
    # =====================================================

    media_type, file_id = (
        detect_media(message)
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
        total = db.scard(
            "media_items"
        )

        photos = 0
        videos = 0

        for item_id in db.smembers(
            "media_items"
        ):
            item = get_item(
                item_id
            )

            if not item:
                continue

            media_type = item.get(
                "media_type"
            )

            if media_type == "photo":
                photos += 1

            elif media_type in (
                "video",
                "video_document",
                "animation"
            ):
                videos += 1

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

        # Здесь список уже случайно перемешан
        items = search_items(
            tags
        )

        # Только фото
        if search_mode == "photo":
            items = [
                item
                for item in items
                if item.get("media_type")
                == "photo"
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

        # Берём максимум 50 из уже перемешанного списка
        selected_items = items[
            :SEARCH_LIMIT
        ]

        send_message(
            chat_id,
            f"🎲 Найдено: {total_found}\n"
            f"Показываю: {len(selected_items)} "
            f"в случайном порядке."
        )

        for item in selected_items:
            send_media(
                chat_id,
                item
            )

            formatted_tags = (
                ", ".join(
                    "#" + tag
                    for tag
                    in item["tags"]
                )
            )

            send_message(
                chat_id,
                f"🏷 {formatted_tags}"
            )

        if total_found > SEARCH_LIMIT:
            send_message(
                chat_id,
                f"🎲 Показаны случайные "
                f"{SEARCH_LIMIT} из {total_found}.\n\n"
                "Повтори тот же поиск, "
                "чтобы получить новую случайную выборку."
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

        formatted_tags = (
            ", ".join(
                "#" + tag
                for tag in tags
            )
        )

        if pending.get("media_type") == "photo":
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
