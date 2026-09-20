import os
import json
import requests
import redis
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
REDIS_URL = os.environ["REDIS_URL"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Подключение к Render Key Value
db = redis.from_url(REDIS_URL, decode_responses=True)


def tg(method, data=None):
    """Запрос к Telegram Bot API."""
    try:
        r = requests.post(
            f"{TELEGRAM_API}/{method}",
            json=data or {},
            timeout=20
        )
        print(f"Telegram {method}: {r.status_code} {r.text[:500]}")
        return r.json()
    except Exception as e:
        print("Telegram error:", e)
        return None


def send_message(chat_id, text):
    return tg("sendMessage", {
        "chat_id": chat_id,
        "text": text
    })


def send_video(chat_id, file_id, caption=None):
    data = {
        "chat_id": chat_id,
        "video": file_id
    }

    if caption:
        data["caption"] = caption

    return tg("sendVideo", data)


def normalize_tags(text):
    """
    Поддерживает:
    коты, смешное, мемы
    или
    коты смешное мемы
    """
    text = text.strip().lower()

    if "," in text:
        tags = [x.strip() for x in text.split(",")]
    else:
        tags = [x.strip() for x in text.split()]

    # Убираем # и пустые значения
    tags = [
        tag.lstrip("#")
        for tag in tags
        if tag.strip()
    ]

    # Убираем повторы
    return list(dict.fromkeys(tags))


def save_video(chat_id, file_id, tags):
    """
    Сохраняем видео и создаём индекс по тегам.
    """

    video_id = db.incr("next_video_id")

    video = {
        "id": video_id,
        "file_id": file_id,
        "tags": tags
    }

    db.set(
        f"video:{video_id}",
        json.dumps(video, ensure_ascii=False)
    )

    db.sadd("videos", video_id)

    for tag in tags:
        db.sadd(f"tag:{tag}", video_id)

    return video_id


def get_video(video_id):
    raw = db.get(f"video:{video_id}")

    if not raw:
        return None

    return json.loads(raw)


def search_videos(tags):
    if not tags:
        return []

    sets = [f"tag:{tag}" for tag in tags]

    # Если введено несколько тегов,
    # ищем видео, где присутствуют ВСЕ эти теги.
    ids = db.sinter(*sets)

    videos = []

    for video_id in ids:
        video = get_video(video_id)

        if video:
            videos.append(video)

    return videos


@app.route("/", methods=["GET"])
def home():
    try:
        db.ping()
        database = "connected"
    except Exception as e:
        database = f"error: {e}"

    return f"Vlad Media Library Bot is running. Database: {database}"


@app.route("/setup", methods=["GET"])
def setup():
    webhook_url = request.url_root.rstrip("/") + "/webhook"

    response = requests.get(
        f"{TELEGRAM_API}/setWebhook",
        params={
            "url": webhook_url,
            "drop_pending_updates": True
        },
        timeout=20
    )

    return jsonify(response.json())


@app.route("/checkbot", methods=["GET"])
def checkbot():
    response = requests.get(
        f"{TELEGRAM_API}/getMe",
        timeout=20
    )

    return jsonify(response.json())


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}

    print("UPDATE:", json.dumps(update, ensure_ascii=False)[:3000])

    message = update.get("message")

    if not message:
        return "OK", 200

    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()

    # ------------------------
    # /start
    # ------------------------

    if text == "/start":
        send_message(
            chat_id,
            "🎬 Vlad Media Library\n\n"
            "Перешли мне видео — я предложу добавить к нему теги.\n\n"
            "После этого видео можно будет найти командой:\n"
            "/search коты\n\n"
            "Несколько тегов:\n"
            "/search коты смешное\n\n"
            "Другие команды:\n"
            "/count — количество видео\n"
            "/tags — список тегов\n"
            "/cancel — отменить добавление"
        )

        return "OK", 200

    # ------------------------
    # /cancel
    # ------------------------

    if text == "/cancel":
        db.delete(f"pending:{chat_id}")

        send_message(
            chat_id,
            "❌ Добавление отменено."
        )

        return "OK", 200

    # ------------------------
    # /count
    # ------------------------

    if text == "/count":
        count = db.scard("videos")

        send_message(
            chat_id,
            f"🎬 В библиотеке видео: {count}"
        )

        return "OK", 200

    # ------------------------
    # /tags
    # ------------------------

    if text == "/tags":
        tags = []

        for key in db.scan_iter("tag:*"):
            tag = key[4:]
            count = db.scard(key)

            tags.append((tag, count))

        tags.sort(key=lambda x: (-x[1], x[0]))

        if not tags:
            send_message(
                chat_id,
                "Пока тегов нет."
            )
        else:
            lines = [
                f"#{tag} — {count}"
                for tag, count in tags[:100]
            ]

            send_message(
                chat_id,
                "🏷 Теги:\n\n" + "\n".join(lines)
            )

        return "OK", 200

    # ------------------------
    # /search
    # ------------------------

    if text.startswith("/search"):
        query = text[len("/search"):].strip()

        if not query:
            send_message(
                chat_id,
                "Напиши теги после команды.\n\n"
                "Например:\n"
                "/search коты\n\n"
                "или:\n"
                "/search коты смешное"
            )

            return "OK", 200

        tags = normalize_tags(query)

        videos = search_videos(tags)

        if not videos:
            send_message(
                chat_id,
                "🔎 Ничего не найдено."
            )

            return "OK", 200

        send_message(
            chat_id,
            f"🔎 Найдено видео: {len(videos)}"
        )

        # Чтобы случайно не отправить сотни видео
        for video in videos[:20]:

            caption = (
                f"🎬 #{video['id']}\n"
                f"🏷 " +
                ", ".join(
                    "#" + tag
                    for tag in video["tags"]
                )
            )

            send_video(
                chat_id,
                video["file_id"],
                caption
            )

        if len(videos) > 20:
            send_message(
                chat_id,
                f"Показаны первые 20 из {len(videos)}."
            )

        return "OK", 200

    # ------------------------
    # Получили новое видео
    # ------------------------

    video = message.get("video")

    # Иногда видео отправляют как документ
    document = message.get("document")

    file_id = None

    if video:
        file_id = video.get("file_id")

    elif document:
        mime = document.get("mime_type", "")

        if mime.startswith("video/"):
            file_id = document.get("file_id")

    if file_id:

        # Временно сохраняем file_id,
        # пока пользователь вводит теги.
        db.set(
            f"pending:{chat_id}",
            file_id,
            ex=3600
        )

        send_message(
            chat_id,
            "✅ Видео получил!\n\n"
            "Теперь напиши теги для него через запятую.\n\n"
            "Например:\n"
            "коты, смешное, мемы\n\n"
            "Или отправь /cancel для отмены."
        )

        return "OK", 200

    # ------------------------
    # Пользователь вводит теги
    # ------------------------

    pending_file_id = db.get(
        f"pending:{chat_id}"
    )

    if pending_file_id and text:

        tags = normalize_tags(text)

        if not tags:
            send_message(
                chat_id,
                "Не смог распознать теги. Попробуй ещё раз."
            )

            return "OK", 200

        video_id = save_video(
            chat_id,
            pending_file_id,
            tags
        )

        db.delete(
            f"pending:{chat_id}"
        )

        formatted_tags = ", ".join(
            "#" + tag
            for tag in tags
        )

        send_message(
            chat_id,
            f"✅ Видео сохранено!\n\n"
            f"🎬 Номер: #{video_id}\n"
            f"🏷 {formatted_tags}"
        )

        return "OK", 200

    # ------------------------
    # Обычный текст
    # ------------------------

    if text:
        send_message(
            chat_id,
            "Я не понял команду.\n\n"
            "Для поиска используй:\n"
            "/search тег\n\n"
            "Или просто перешли мне видео."
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
