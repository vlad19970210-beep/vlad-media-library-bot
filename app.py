import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

WEBHOOK_URL = "https://vlad-media-library-bot.onrender.com/webhook"


# Главная страница
@app.route("/", methods=["GET"])
def home():
    return "Vlad Media Library Bot is running", 200


# Проверка токена и самого бота
@app.route("/checkbot", methods=["GET"])
def checkbot():
    response = requests.get(
        f"{TELEGRAM_API}/getMe",
        timeout=20
    )
    return jsonify(response.json())


# Проверка текущего webhook
@app.route("/webhookinfo", methods=["GET"])
def webhookinfo():
    response = requests.get(
        f"{TELEGRAM_API}/getWebhookInfo",
        timeout=20
    )
    return jsonify(response.json())


# Установка webhook
@app.route("/setup", methods=["GET"])
def setup():
    response = requests.get(
        f"{TELEGRAM_API}/setWebhook",
        params={"url": WEBHOOK_URL},
        timeout=20
    )
    return jsonify(response.json())


# Получение сообщений от Telegram
@app.route("/webhook", methods=["POST"])
def webhook():

    update = request.get_json(silent=True) or {}

    # Сообщение, отправленное боту лично
    message = update.get("message")

    # Публикация в канале
    channel_post = update.get("channel_post")

    data = channel_post or message

    if data:
        chat = data.get("chat", {})
        chat_id = chat.get("id")
        message_id = data.get("message_id")

        text = data.get("text", "")
        caption = data.get("caption", "")

        media_type = None
        file_id = None

        # Видео
        if data.get("video"):
            media_type = "video"
            file_id = data["video"].get("file_id")

        # Документ / видео, отправленное как файл
        elif data.get("document"):
            media_type = "document"
            file_id = data["document"].get("file_id")

        # Анимация
        elif data.get("animation"):
            media_type = "animation"
            file_id = data["animation"].get("file_id")

        # Аудио
        elif data.get("audio"):
            media_type = "audio"
            file_id = data["audio"].get("file_id")

        # Фото
        elif data.get("photo"):
            media_type = "photo"

            photos = data["photo"]

            if photos:
                file_id = photos[-1].get("file_id")

        print(
            "MEDIA:",
            "chat_id=", chat_id,
            "message_id=", message_id,
            "type=", media_type,
            "file_id=", file_id,
            "caption=", caption,
            "text=", text,
            flush=True
        )

        # Если пользователь прислал видео лично боту
        if message and media_type:

            requests.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text":
                        "✅ Видео получил!\n\n"
                        "Теперь напиши теги для него.\n"
                        "Например:\n"
                        "география, вулканы, 7 класс"
                },
                timeout=20
            )

        # Обычная команда /start
        elif message and text == "/start":

            requests.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text":
                        "👋 Vlad Media Library работает!\n\n"
                        "Пришли мне видео, и я предложу указать для него теги."
                },
                timeout=20
            )

    return "OK", 200
