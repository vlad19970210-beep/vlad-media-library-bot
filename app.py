import os
import json
import requests
import redis
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
REDIS_URL = os.environ["REDIS_URL"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Канал-медиатека
CHANNEL_ID = -1003148826053

db = redis.from_url(
    REDIS_URL,
    decode_responses=True
)


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


def copy_channel_post(chat_id, message_id):
    """
    Показывает найденный пост пользователю,
    не загружая видео повторно.
    """
    return tg(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": CHANNEL_ID,
            "message_id": message_id
        }
    )


def normalize_tags(text):
    text = text.strip().lower()

    if "," in text:
        tags = [
            tag.strip().lstrip("#")
            for tag in text.split(",")
        ]
    else:
        tags = [
            tag.strip().lstrip("#")
            for tag in text.split()
        ]

    tags = [tag for tag in tags if tag]

    return list(dict.fromkeys(tags))


def get_owner():
    owner = db.get("owner_chat_id")

    if owner:
        return int(owner)

    return None


def save_item(channel_message_id, file_id, media_type, tags):
    item = {
        "message_id": channel_message_id,
        "file_id": file_id,
        "media_type": media_type,
        "tags": tags
    }

    db.set(
        f"item:{channel_message_id}",
        json.dumps(
            item,
            ensure_ascii=False
        )
    )

    db.sadd(
        "library_items",
        channel_message_id
    )

    for tag in tags:
        db.sadd(
            f"tag:{tag}",
            channel_message_id
        )


def get_item(message_id):
    raw = db.get(
        f"item:{message_id}"
    )

    if not raw:
        return None

    return json.loads(raw)


def get_queue():
    return db.lrange(
        "tag_queue",
        0,
        -1
    )


def current_pending():
    raw = db.lindex(
        "tag_queue",
        0
    )

    if not raw:
        return None

    return json.loads(raw)


def ask_for_next_tags():
    owner = get_owner()

    if not owner:
        return

    pending = current_pending()

    if not pending:
        send_message(
            owner,
            "✅ Очередь разобрана. "
            "Новых видео без тегов нет."
        )
        return

    queue_size = db.llen("tag_queue")

    message_id = pending["message_id"]

    send_message(
        owner,
        "🎬 Новое видео из канала.\n\n"
        f"В очереди: {queue_size}\n"
        f"Message ID: {message_id}\n\n"
        "Напиши теги через запятую.\n\n"
        "Например:\n"
        "блондинка, соло, избранное\n\n"
        "Команды:\n"
        "/skip — пропустить пока\n"
        "/delete — убрать из каталога"
    )

    # Показываем копию видео,
    # чтобы было понятно, что именно размечаем.
    copy_channel_post(
        owner,
        message_id
    )


def search_items(tags):
    if not tags:
        return []

    keys = [
        f"tag:{tag}"
        for tag in tags
    ]

    ids = db.sinter(*keys)

    items = []

    for message_id in ids:
        item = get_item(message_id)

        if item:
            items.append(item)

    items.sort(
        key=lambda x: x["message_id"],
        reverse=True
    )

    return items


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


@app.route("/checkbot", methods=["GET"])
def checkbot():
    response = requests.get(
        f"{TELEGRAM_API}/getMe",
        timeout=20
    )

    return jsonify(
        response.json()
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
        )[:3000],
        flush=True
    )

    # =====================================
    # НОВЫЙ ПОСТ В КАНАЛЕ
    # =====================================

    post = update.get(
        "channel_post"
    )

    if post:
        chat_id = post["chat"]["id"]

        # Игнорируем другие каналы
        if chat_id != CHANNEL_ID:
            return "OK", 200

        message_id = post["message_id"]

        media_type = None
        file_id = None

        if post.get("video"):
            media_type = "video"
            file_id = (
                post["video"]
                .get("file_id")
            )

        elif post.get("document"):
            document = post["document"]

            mime = document.get(
                "mime_type",
                ""
            )

            if mime.startswith(
                "video/"
            ):
                media_type = (
                    "video_document"
                )

                file_id = document.get(
                    "file_id"
                )

        elif post.get("animation"):
            media_type = "animation"

            file_id = (
                post["animation"]
                .get("file_id")
            )

        # Нас интересуют только медиа
        if media_type and file_id:

            pending = {
                "message_id": message_id,
                "file_id": file_id,
                "media_type": media_type
            }

            # Не добавляем один пост
            # в очередь повторно
            queue_ids = []

            for raw in get_queue():
                try:
                    q = json.loads(raw)
                    queue_ids.append(
                        q["message_id"]
                    )
                except Exception:
                    pass

            if (
                message_id
                not in queue_ids
                and not db.exists(
                    f"item:{message_id}"
                )
            ):
                was_empty = (
                    db.llen(
                        "tag_queue"
                    )
                    == 0
                )

                db.rpush(
                    "tag_queue",
                    json.dumps(
                        pending,
                        ensure_ascii=False
                    )
                )

                # Если очередь до этого была пустой,
                # сразу спрашиваем теги.
                if was_empty:
                    ask_for_next_tags()

        return "OK", 200

    # =====================================
    # ЛИЧНЫЕ СООБЩЕНИЯ БОТУ
    # =====================================

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

    # =====================================
    # /start
    # =====================================

    if text == "/start":

        # Первый пользователь,
        # запустивший бота, становится владельцем.
        owner = get_owner()

        if owner is None:
            db.set(
                "owner_chat_id",
                chat_id
            )

        elif owner != chat_id:
            send_message(
                chat_id,
                "⛔ Этот бот является "
                "личной медиатекой."
            )

            return "OK", 200

        send_message(
            chat_id,
            "🎬 Vlad Media Library\n\n"
            "Теперь просто пересылай видео "
            "в свой канал.\n\n"
            "Я увижу новый пост и попрошу "
            "тебя указать теги.\n\n"
            "Команды:\n"
            "/search тег — поиск\n"
            "/tags — все теги\n"
            "/count — размер каталога\n"
            "/queue — очередь без тегов\n"
            "/next — показать текущее видео"
        )

        # Если видео уже накопились
        if db.llen("tag_queue") > 0:
            ask_for_next_tags()

        return "OK", 200

    # Запрещаем управление посторонним
    owner = get_owner()

    if owner != chat_id:
        send_message(
            chat_id,
            "⛔ Нет доступа."
        )

        return "OK", 200

    # =====================================
    # /count
    # =====================================

    if text == "/count":
        count = db.scard(
            "library_items"
        )

        queue = db.llen(
            "tag_queue"
        )

        send_message(
            chat_id,
            f"🎬 В каталоге: {count}\n"
            f"⏳ Без тегов: {queue}"
        )

        return "OK", 200

    # =====================================
    # /queue
    # =====================================

    if text == "/queue":
        queue = db.llen(
            "tag_queue"
        )

        send_message(
            chat_id,
            f"⏳ Видео без тегов: {queue}"
        )

        return "OK", 200

    # =====================================
    # /next
    # =====================================

    if text == "/next":
        if db.llen(
            "tag_queue"
        ) == 0:

            send_message(
                chat_id,
                "✅ Очередь пустая."
            )

        else:
            ask_for_next_tags()

        return "OK", 200

    # =====================================
    # /skip
    # =====================================

    if text == "/skip":
        raw = db.lpop(
            "tag_queue"
        )

        if raw:
            # Переносим текущее видео
            # в конец очереди
            db.rpush(
                "tag_queue",
                raw
            )

            send_message(
                chat_id,
                "⏭ Перенёс видео "
                "в конец очереди."
            )

            ask_for_next_tags()

        else:
            send_message(
                chat_id,
                "Очередь пустая."
            )

        return "OK", 200

    # =====================================
    # /delete
    # =====================================

    if text == "/delete":
        raw = db.lpop(
            "tag_queue"
        )

        if raw:
            send_message(
                chat_id,
                "🗑 Убрал это видео "
                "из очереди каталога.\n"
                "Сам пост в канале "
                "не удалён."
            )

            if db.llen(
                "tag_queue"
            ) > 0:
                ask_for_next_tags()

        else:
            send_message(
                chat_id,
                "Очередь пустая."
            )

        return "OK", 200

    # =====================================
    # /tags
    # =====================================

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

    # =====================================
    # /search
    # =====================================

    if text.startswith(
        "/search"
    ):
        query = text[
            len("/search"):
        ].strip()

        if not query:
            send_message(
                chat_id,
                "Например:\n"
                "/search блондинка\n\n"
                "Несколько тегов:\n"
                "/search блондинка соло"
            )

            return "OK", 200

        tags = normalize_tags(
            query
        )

        items = search_items(
            tags
        )

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

        # Показываем первые 20 результатов
        for item in items[:20]:

            copy_channel_post(
                chat_id,
                item["message_id"]
            )

            formatted = ", ".join(
                "#" + tag
                for tag
                in item["tags"]
            )

            send_message(
                chat_id,
                f"🏷 {formatted}"
            )

        if len(items) > 20:
            send_message(
                chat_id,
                "Показаны первые "
                f"20 из {len(items)}."
            )

        return "OK", 200

    # =====================================
    # ВВОД ТЕГОВ ДЛЯ ТЕКУЩЕГО ВИДЕО
    # =====================================

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
            pending["message_id"],
            pending["file_id"],
            pending["media_type"],
            tags
        )

        # Убираем размеченный элемент
        db.lpop(
            "tag_queue"
        )

        formatted = ", ".join(
            "#" + tag
            for tag in tags
        )

        send_message(
            chat_id,
            "✅ Добавлено в каталог!\n\n"
            f"🏷 {formatted}"
        )

        # Автоматически переходим
        # к следующему видео
        if db.llen(
            "tag_queue"
        ) > 0:

            ask_for_next_tags()

        else:
            send_message(
                chat_id,
                "🎉 Очередь полностью разобрана."
            )

        return "OK", 200

    # =====================================
    # ПРОЧИЙ ТЕКСТ
    # =====================================

    send_message(
        chat_id,
        "Перешли видео в канал "
        "или используй /search."
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
