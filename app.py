import os
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


@app.route("/", methods=["GET"])
def home():
    return "Vlad Media Library Bot is running", 200


@app.route("/setup", methods=["GET"])
def setup():
    webhook_url = "https://vlad-media-library-bot.onrender.com/webhook"

    response = requests.get(
        f"{TELEGRAM_API}/setWebhook",
        params={
            "url": webhook_url,
            "allowed_updates": ["channel_post"]
        }
    )

    return jsonify(response.json())


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}

    post = update.get("channel_post")

    if post:
        chat = post.get("chat", {})
        message_id = post.get("message_id")
        caption = post.get("caption", "")
        text = post.get("text", "")

        media_type = "unknown"
        file_id = None

        if post.get("video"):
            media_type = "video"
            file_id = post["video"].get("file_id")

        elif post.get("photo"):
            media_type = "photo"
            photos = post["photo"]
            if photos:
                file_id = photos[-1].get("file_id")

        elif post.get("document"):
            media_type = "document"
            file_id = post["document"].get("file_id")

        elif post.get("audio"):
            media_type = "audio"
            file_id = post["audio"].get("file_id")

        elif post.get("animation"):
            media_type = "animation"
            file_id = post["animation"].get("file_id")

        elif text:
            media_type = "text"

        print(
            "MEDIA:",
            "chat_id=", chat.get("id"),
            "message_id=", message_id,
            "type=", media_type,
            "file_id=", file_id,
            "caption=", caption,
            "text=", text,
            flush=True
        )

    return "OK", 200
