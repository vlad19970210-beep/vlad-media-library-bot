import os
import json
import requests
import redis
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
REDIS_URL = os.environ["REDIS_URL"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Твой канал
CHANNEL_ID = -1003148826053

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
    Показываем материал пользователю.
    Для постов из канала копируем исходный пост.
    Для старых импортированных материалов используем file_id.
    """

    if item.get("source") == "channel":
        return copy_message(
            chat_id,
            CHANNEL_ID,
            item["message_id"]
        )

    media_type = item.get("media_type")
    file_id = item.get("file_id")

    if media_type == "photo":
        return tg(
            "sendPhoto",
            {
                "chat_id": chat_id,
                "photo": file_id
            }
        )

    if media_type in ("video", "video_document"):
        return tg(
            "sendVideo",
            {
                "chat_id": chat_id,
                "video": file_id
            }
        )

    if media_type == "animation":
        return tg(
            "sendAnimation",
            {
                "chat_id": chat_id,
                "animation": file_id
            }
        )


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
    Определяем тип фотографии/видео.
    Возвращает:
    media_type, file_id
    """

    # ФОТО
    photos = message.get("photo")

    if photos:
        # Telegram присылает несколько размеров одной фотографии.
        # Последний обычно самый большой.
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

    # GIF / animation
    animation = message.get("animation")

    if animation:
        return (
            "animation",
            animation.get("file_id")
        )

    # Файл
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
        "video": "🎬 Видео",
        "video_document": "🎬 Видео",
        "animation": "🎞 GIF"
    }.get(
        item["media_type"],
        "📁 Материал"
    )

    source_name = (
        "из канала"
        if item["source"] == "channel"
        else "старый материал"
    )

    send_message(
        owner,
        f"{media_name} — {source_name}\n\n"
        f"В очереди: {queue_size}\n\n"
        "Напиши теги через запятую.\n\n"
        "Например:\n"
        "отпуск, море, избранное\n\n"
        "/skip — отложить\n"
        "/delete — не добавлять в каталог"
    )

    send_media(
        owner,
        item
    )


# =========================================================
# SEARCH
# =========================================================

def search_items(tags):
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
            post.get("chat", {})
            .get("id")
        )

        if channel_id != CHANNEL_ID:
            return "OK", 200

        media_type, file_id = (
            detect_media(post)
        )

        if not file_id:
            return "OK", 200

        message_id = post["message_id"]

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
    # ЛИЧКА С БОТОМ
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
            "НОВЫЕ материалы:\n"
            "пересылай фото или видео "
            "сразу в канал.\n\n"
            "СТАРЫЕ материалы:\n"
            "перешли фото или видео "
            "сюда, в личку боту.\n\n"
            "Я поставлю их в очередь "
            "и попрошу теги.\n\n"
            "Команды:\n"
            "/search тег — поиск\n"
            "/photo тег — только фото\n"
            "/video тег — только видео\n"
            "/tags — список тегов\n"
            "/count — статистика\n"
            "/queue — очередь\n"
            "/next — текущий материал"
        )

        if db.llen(
            "media_queue"
        ) > 0:
            ask_for_next_tags()

        return "OK", 200

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

            if item.get(
                "media_type"
            ) == "photo":
                photos += 1

            elif item.get(
                "media_type"
            ) in (
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

        if db.llen(
            "media_queue"
        ) == 0:

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
                "⏭ Перенёс в конец очереди."
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
                "🗑 Убрал из очереди.\n"
                "Сам пост/файл не удалён."
            )

            if db.llen(
                "media_queue"
            ) > 0:
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
                (tag, count)
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
    # SEARCH FUNCTIONS
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

        items = search_items(
            tags
        )

        # Фильтр фото/видео
        if search_mode == "photo":

            items = [
                item
                for item in items
                if item.get(
                    "media_type"
                ) == "photo"
            ]

        elif search_mode == "video":

            items = [
                item
                for item in items
                if item.get(
                    "media_type"
                ) in (
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

        send_message(
            chat_id,
            f"🔎 Найдено: {len(items)}"
        )

        for item in items[:20]:

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

        if len(items) > 20:

            send_message(
                chat_id,
                f"Показаны первые 20 "
                f"из {len(items)}."
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

        media_name = (
            "Фото"
            if pending["media_type"]
            == "photo"
            else "Видео"
        )

        send_message(
            chat_id,
            f"✅ {media_name} сохранено!\n\n"
            f"🏷 {formatted_tags}"
        )

        if db.llen(
            "media_queue"
        ) > 0:

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
            "Или перешли мне старое "
            "фото/видео для добавления."
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
