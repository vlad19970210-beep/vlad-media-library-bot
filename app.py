import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Пока храним данные в памяти.
# Позже подключим постоянную базу данных.
OWNER_CHAT_ID = None

# message_id видео -> данные каталога
library = {}

# chat_id -> какое видео сейчас размечаем
user_state = {}


def telegram(method, data=None):
    url = f"{TELEGRAM_API}/{method}"
    response = requests.post(url, json=data or {}, timeout=20)
    return response.json()


def send_message(chat_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:
        data["reply_markup"] = {
            "inline_keyboard": keyboard
        }

    return telegram("sendMessage", data)


def answer_callback(callback_id):
    return telegram(
        "answerCallbackQuery",
        {"callback_query_id": callback_id}
    )


def category_keyboard():
    return [
        [
            {"text": "🎮 Игры", "callback_data": "cat:Игры"},
            {"text": "🎬 Кино", "callback_data": "cat:Кино"}
        ],
        [
            {"text": "🌍 География", "callback_data": "cat:География"},
            {"text": "📚 Полезное", "callback_data": "cat:Полезное"}
        ],
        [
            {"text": "😂 Юмор", "callback_data": "cat:Юмор"},
            {"text": "📁 Другое", "callback_data": "cat:Другое"}
        ],
        [
            {"text": "⏭ Без категории", "callback_data": "cat:Без категории"}
        ]
    ]


def tags_keyboard():
    return [
        [
            {"text": "🎮 STALKER", "callback_data": "tag:STALKER"},
            {"text": "⚔️ Battlefield", "callback_data": "tag:Battlefield"}
        ],
        [
            {"text": "🏰 Europa Universalis", "callback_data": "tag:Europa Universalis"},
            {"text": "🌐 HOI4", "callback_data": "tag:HOI4"}
        ],
        [
            {"text": "📖 Гайд", "callback_data": "tag:Гайд"},
            {"text": "⭐ Избранное", "callback_data": "tag:Избранное"}
        ],
        [
            {"text": "➕ Свой тег", "callback_data": "newtag"},
            {"text": "✅ Готово", "callback_data": "done"}
        ]
    ]


@app.route("/", methods=["GET"])
def home():
    return "Vlad Media Library Bot is running", 200


@app.route("/setup", methods=["GET"])
def setup():
    webhook_url = "https://vlad-media-library-bot.onrender.com/webhook"

    response = requests.get(
        f"{TELEGRAM_API}/setWebhook",
        params={"url": webhook_url},
        timeout=20
    )

    return jsonify(response.json())


@app.route("/webhook", methods=["POST"])
def webhook():
    global OWNER_CHAT_ID

    update = request.get_json(silent=True) or {}

    # --------------------------------
    # ЛИЧНЫЕ СООБЩЕНИЯ БОТУ
    # --------------------------------

    message = update.get("message")

    if message:
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        text = message.get("text", "").strip()

        if chat_type == "private":

            # Запоминаем владельца
            if OWNER_CHAT_ID is None:
                OWNER_CHAT_ID = chat_id
                print(f"OWNER CHAT ID: {OWNER_CHAT_ID}", flush=True)

            # /start
            if text == "/start":
                send_message(
                    chat_id,
                    "👋 Медиатека запущена!\n\n"
                    "Перешли видео в свой канал, "
                    "и я предложу добавить его в каталог."
                )
                return "OK", 200

            # Пользователь вводит собственный тег
            state = user_state.get(chat_id)

            if state and state.get("waiting_custom_tag") and text:
                video_id = state["video_id"]

                if video_id in library:
                    library[video_id]["tags"].append(text)

                    state["waiting_custom_tag"] = False

                    tags = ", ".join(library[video_id]["tags"])

                    send_message(
                        chat_id,
                        f"🏷 Добавил тег: {text}\n\n"
                        f"Текущие теги: {tags}",
                        tags_keyboard()
                    )

                return "OK", 200

            send_message(
                chat_id,
                "Бот работает 👍\n\n"
                "Добавь видео в канал, и я предложу "
                "выбрать для него категорию."
            )

            return "OK", 200

    # --------------------------------
    # НОВЫЙ ПОСТ В КАНАЛЕ
    # --------------------------------

    post = update.get("channel_post")

    if post:
        chat = post.get("chat", {})
        channel_id = chat.get("id")
        message_id = post.get("message_id")
        caption = post.get("caption", "")
        text = post.get("text", "")

        video = post.get("video")
        document = post.get("document")

        media_type = None
        file_id = None

        if video:
            media_type = "video"
            file_id = video.get("file_id")

        elif document:
            mime = document.get("mime_type", "")

            if mime.startswith("video/"):
                media_type = "video_document"
                file_id = document.get("file_id")

        print(
            "MEDIA:",
            "chat_id=", channel_id,
            "message_id=", message_id,
            "type=", media_type,
            "file_id=", file_id,
            "caption=", caption,
            "text=", text,
            flush=True
        )

        if media_type and file_id:

            library[message_id] = {
                "channel_id": channel_id,
                "message_id": message_id,
                "file_id": file_id,
                "type": media_type,
                "caption": caption,
                "category": None,
                "tags": []
            }

            if OWNER_CHAT_ID:
                user_state[OWNER_CHAT_ID] = {
                    "video_id": message_id,
                    "waiting_custom_tag": False
                }

                send_message(
                    OWNER_CHAT_ID,
                    "🎬 Новое видео!\n\n"
                    f"ID: {message_id}\n"
                    f"Описание: {caption or 'нет'}\n\n"
                    "Куда его добавить?",
                    category_keyboard()
                )

            else:
                print(
                    "OWNER_CHAT_ID неизвестен. "
                    "Напиши боту /start в личку.",
                    flush=True
                )

        return "OK", 200

    # --------------------------------
    # НАЖАТИЯ НА КНОПКИ
    # --------------------------------

    callback = update.get("callback_query")

    if callback:
        callback_id = callback.get("id")
        data = callback.get("data", "")

        callback_message = callback.get("message", {})
        chat_id = callback_message.get("chat", {}).get("id")

        answer_callback(callback_id)

        state = user_state.get(chat_id)

        if not state:
            send_message(chat_id, "Не нашёл видео для разметки.")
            return "OK", 200

        video_id = state["video_id"]

        if video_id not in library:
            send_message(chat_id, "Видео уже недоступно в текущей сессии.")
            return "OK", 200

        # Выбрана категория
        if data.startswith("cat:"):
            category = data.split(":", 1)[1]

            library[video_id]["category"] = category

            send_message(
                chat_id,
                f"📁 Категория: {category}\n\n"
                "Теперь выбери один или несколько тегов.\n"
                "Можно нажимать несколько кнопок.",
                tags_keyboard()
            )

        # Выбран тег
        elif data.startswith("tag:"):
            tag = data.split(":", 1)[1]

            if tag not in library[video_id]["tags"]:
                library[video_id]["tags"].append(tag)

            tags = ", ".join(library[video_id]["tags"])

            send_message(
                chat_id,
                f"🏷 Теги: {tags}\n\n"
                "Можно выбрать ещё или нажать «Готово».",
                tags_keyboard()
            )

        # Новый собственный тег
        elif data == "newtag":
            state["waiting_custom_tag"] = True

            send_message(
                chat_id,
                "✏️ Напиши мне название нового тега одним сообщением."
            )

        # Завершение
        elif data == "done":

            item = library[video_id]

            category = item["category"] or "Без категории"
            tags = ", ".join(item["tags"]) or "без тегов"

            send_message(
                chat_id,
                "✅ Сохранено в медиатеку!\n\n"
                f"📁 Категория: {category}\n"
                f"🏷 Теги: {tags}\n"
                f"🎬 Message ID: {video_id}"
            )

            print(
                "SAVED:",
                json.dumps(item, ensure_ascii=False),
                flush=True
            )

            user_state.pop(chat_id, None)

        return "OK", 200

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
