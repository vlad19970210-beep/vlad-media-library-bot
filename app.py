import os
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


@app.route("/", methods=["GET"])
def home():
    return "Vlad Media Library Bot is running", 200


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
