import os
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


@app.route("/setup", methods=["GET"])
def setup():
    webhook_url = "https://vlad-media-library-bot.onrender.com/webhook"
    response = requests.get(
        f"{TELEGRAM_API}/setWebhook",
        params={"url": webhook_url}
    )
    return jsonify(response.json())


@app.route("/", methods=["GET"])
def home():
    return "Vlad Media Library Bot is running"


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}

    # Новая публикация в канале
    post = update.get("channel_post")

    if post:
        chat = post.get("chat", {})
        message_id = post.get("message_id")
        caption = post.get("caption", "")
        text = post.get("text", "")

        print(
            "NEW CHANNEL POST:",
            chat.get("id"),
            message_id,
            caption or text
        )

    return jsonify({"ok": True})
