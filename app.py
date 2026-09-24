import os
import json
import random
import requests
import redis

from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify


app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
REDIS_URL = os.environ["REDIS_URL"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Твой Telegram-канал
CHANNEL_ID = -1003148826053

# Сколько результатов показываем
SEARCH_LIMIT = 50

# Для отображения /storage
REDIS_LIMIT_MB = 50

# Автоматический бэкап раз в 7 дней,
# если каталог используется/пополняется
AUTO_BACKUP_DAYS = 7


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
            timeout=30
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


def copy_message(
    chat_id,
    from_chat_id,
    message_id
):

    return tg(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id
        }
    )


def send_document_bytes(
    chat_id,
    filename,
    content,
    caption=None
):

    try:

        files = {
            "document": (
                filename,
                content,
                "application/json"
            )
        }

        data = {
            "chat_id": chat_id
        }

        if caption:
            data["caption"] = caption

        response = requests.post(
            f"{TELEGRAM_API}/sendDocument",
            data=data,
            files=files,
            timeout=60
        )

        print(
            "Backup send:",
            response.status_code,
            response.text[:500],
            flush=True
        )

        return response.json()

    except Exception as e:

        print(
            "Backup send error:",
            e,
            flush=True
        )

        return None


def send_media(chat_id, item):

    media_type = item.get(
        "media_type"
    )

    file_id = item.get(
        "file_id"
    )

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

    return list(
        dict.fromkeys(tags)
    )


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
# MEDIA
# =========================================================

def detect_media(message):

    photos = message.get(
        "photo"
    )

    if photos:

        photo = photos[-1]

        return (
            "photo",
            photo.get("file_id")
        )

    video = message.get(
        "video"
    )

    if video:

        return (
            "video",
            video.get("file_id")
        )

    animation = message.get(
        "animation"
    )

    if animation:

        return (
            "animation",
            animation.get("file_id")
        )

    document = message.get(
        "document"
    )

    if document:

        mime = document.get(
            "mime_type",
            ""
        )

        if mime.startswith(
            "video/"
        ):

            return (
                "video_document",
                document.get("file_id")
            )

        if mime.startswith(
            "image/"
        ):

            return (
                "image_document",
                document.get("file_id")
            )

    return None, None


# =========================================================
# DATABASE
# =========================================================

def save_item(item, tags):

    item_id = item[
        "item_id"
    ]

    saved = {

        "item_id": item_id,

        "source": item[
            "source"
        ],

        "message_id": item.get(
            "message_id"
        ),

        "source_chat_id": item.get(
            "source_chat_id"
        ),

        "file_id": item[
            "file_id"
        ],

        "media_type": item[
            "media_type"
        ],

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

    if raw:

        try:

            return json.loads(
                raw
            )

        except Exception:

            return None

    # Поддержка старых video:* записей

    if str(
        item_id
    ).isdigit():

        old_raw = db.get(
            f"video:{item_id}"
        )

        if old_raw:

            try:

                old = json.loads(
                    old_raw
                )

                return {

                    "item_id":
                        str(item_id),

                    "source":
                        "legacy",

                    "message_id":
                        None,

                    "source_chat_id":
                        None,

                    "file_id":
                        old.get(
                            "file_id"
                        ),

                    "media_type":
                        "video",

                    "tags":
                        old.get(
                            "tags",
                            []
                        )
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

            item = json.loads(
                raw
            )

            if (
                item.get(
                    "item_id"
                )
                == item_id
            ):

                return True

        except Exception:

            pass

    return False


def add_to_queue(item):

    item_id = item[
        "item_id"
    ]

    if db.exists(
        f"media:{item_id}"
    ):

        return False

    if queue_contains(
        item_id
    ):

        return False

    was_empty = (
        db.llen(
            "media_queue"
        )
        == 0
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

        return json.loads(
            raw
        )

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

        "photo":
            "📷 Фото",

        "image_document":
            "📷 Изображение",

        "video":
            "🎬 Видео",

        "video_document":
            "🎬 Видео",

        "animation":
            "🎞 GIF"

    }.get(
        item["media_type"],
        "📁 Материал"
    )

    if (
        item.get(
            "source"
        )
        == "channel"
    ):

        source_name = (
            "из канала"
        )

    else:

        source_name = (
            "импортировано через бота"
        )

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

            items.append(
                item
            )

    # Каждый поиск перемешивается заново
    random.shuffle(
        items
    )

    return items


# =========================================================
# STORAGE
# =========================================================

def get_storage_info():

    try:

        info = db.info(
            "memory"
        )

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
            for _
            in db.scan_iter(
                "tag:*"
            )
        )

        catalog_bytes = 0

        patterns = [

            "media:*",
            "video:*",
            "tag:*"

        ]

        checked = set()

        for pattern in patterns:

            for key in db.scan_iter(
                pattern
            ):

                if key in checked:

                    continue

                checked.add(
                    key
                )

                try:

                    catalog_bytes += (
                        db.memory_usage(
                            key
                        )
                        or 0
                    )

                except Exception:

                    pass

        service_keys = [

            "media_items",
            "videos",
            "media_queue",
            "owner_chat_id",
            "next_import_id",
            "last_backup_at"

        ]

        for key in service_keys:

            try:

                catalog_bytes += (
                    db.memory_usage(
                        key
                    )
                    or 0
                )

            except Exception:

                pass

        count_for_estimate = (
            total_items
            + legacy_items
        )

        estimated_left = None

        if (
            count_for_estimate
            >= 20
            and catalog_bytes
            > 0
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
                limit_bytes
                - used_bytes
            )

            if avg_bytes > 0:

                estimated_left = int(
                    free_bytes
                    / avg_bytes
                )

        return {

            "used_mb":
                used_mb,

            "percent":
                percent,

            "total_items":
                total_items,

            "legacy_items":
                legacy_items,

            "queue_items":
                queue_items,

            "tag_count":
                tag_count,

            "estimated_left":
                estimated_left
        }

    except Exception as e:

        print(
            "Storage error:",
            e,
            flush=True
        )

        return None


# =========================================================
# BACKUP
# =========================================================

def build_backup():

    """
    Создаёт полный логический бэкап
    нашей медиатеки.
    """

    backup = {

        "backup_format":
            "vlad_media_library",

        "version":
            1,

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "media":
            {},

        "legacy_videos":
            {},

        "tags":
            {},

        "media_items":
            list(
                db.smembers(
                    "media_items"
                )
            ),

        "videos":
            list(
                db.smembers(
                    "videos"
                )
            ),

        "queue":
            db.lrange(
                "media_queue",
                0,
                -1
            ),

        "next_import_id":
            db.get(
                "next_import_id"
            )
    }

    # Новые записи

    for key in db.scan_iter(
        "media:*"
    ):

        value = db.get(
            key
        )

        if value is not None:

            backup[
                "media"
            ][key] = value

    # Старые записи

    for key in db.scan_iter(
        "video:*"
    ):

        value = db.get(
            key
        )

        if value is not None:

            backup[
                "legacy_videos"
            ][key] = value

    # Индексы тегов

    for key in db.scan_iter(
        "tag:*"
    ):

        backup[
            "tags"
        ][key] = list(
            db.smembers(
                key
            )
        )

    return backup


def backup_to_bytes():

    backup = build_backup()

    text_data = json.dumps(
        backup,
        ensure_ascii=False,
        indent=2
    )

    return text_data.encode(
        "utf-8"
    )


def create_backup(
    chat_id,
    automatic=False
):

    try:

        now = datetime.now(
            timezone.utc
        )

        filename = (
            "vlad_media_backup_"
            + now.strftime(
                "%Y-%m-%d_%H-%M"
            )
            + ".json"
        )

        content = backup_to_bytes()

        total = (
            db.scard(
                "media_items"
            )
            + db.scard(
                "videos"
            )
        )

        if automatic:

            caption = (
                "🛡 Автоматический бэкап медиатеки\n\n"
                f"📚 Материалов: {total}\n"
                "Сохрани этот файл."
            )

        else:

            caption = (
                "💾 Ручной бэкап медиатеки\n\n"
                f"📚 Материалов: {total}\n"
                "Сохрани этот файл."
            )

        result = send_document_bytes(
            chat_id,
            filename,
            content,
            caption
        )

        if (
            result
            and result.get(
                "ok"
            )
        ):

            db.set(
                "last_backup_at",
                now.isoformat()
            )

            db.set(
                "last_backup_count",
                total
            )

            return True

        return False

    except Exception as e:

        print(
            "Create backup error:",
            e,
            flush=True
        )

        return False


def maybe_auto_backup():

    """
    Автоматический бэкап.

    Проверяем после изменения каталога.
    Если прошло 7 дней — присылаем новый файл.
    """

    owner = get_owner()

    if not owner:

        return

    last_raw = db.get(
        "last_backup_at"
    )

    should_backup = False

    if not last_raw:

        should_backup = True

    else:

        try:

            last = (
                datetime.fromisoformat(
                    last_raw
                )
            )

            if (
                datetime.now(
                    timezone.utc
                )
                - last
                >= timedelta(
                    days=AUTO_BACKUP_DAYS
                )
            ):

                should_backup = True

        except Exception:

            should_backup = True

    if should_backup:

        create_backup(
            owner,
            automatic=True
        )


# =========================================================
# RESTORE
# =========================================================

def restore_backup(
    backup
):

    """
    Восстанавливаем каталог из JSON.

    Существующий каталог сначала очищается
    только в пределах ключей нашей медиатеки.
    """

    if (
        backup.get(
            "backup_format"
        )
        != "vlad_media_library"
    ):

        raise ValueError(
            "Неверный формат бэкапа"
        )

    pipe = db.pipeline(
        transaction=True
    )

    # Удаляем текущие данные каталога

    keys_to_delete = []

    patterns = [

        "media:*",
        "video:*",
        "tag:*"

    ]

    for pattern in patterns:

        keys_to_delete.extend(
            list(
                db.scan_iter(
                    pattern
                )
            )
        )

    keys_to_delete.extend(
        [
            "media_items",
            "videos",
            "media_queue",
            "next_import_id"
        ]
    )

    if keys_to_delete:

        pipe.delete(
            *keys_to_delete
        )

    # MEDIA

    for key, value in (
        backup.get(
            "media",
            {}
        ).items()
    ):

        pipe.set(
            key,
            value
        )

    # LEGACY

    for key, value in (
        backup.get(
            "legacy_videos",
            {}
        ).items()
    ):

        pipe.set(
            key,
            value
        )

    # TAGS

    for key, members in (
        backup.get(
            "tags",
            {}
        ).items()
    ):

        if members:

            pipe.sadd(
                key,
                *members
            )

    # MEDIA ITEMS

    media_items = backup.get(
        "media_items",
        []
    )

    if media_items:

        pipe.sadd(
            "media_items",
            *media_items
        )

    # OLD VIDEOS

    videos = backup.get(
        "videos",
        []
    )

    if videos:

        pipe.sadd(
            "videos",
            *videos
        )

    # QUEUE

    queue = backup.get(
        "queue",
        []
    )

    if queue:

        pipe.rpush(
            "media_queue",
            *queue
        )

    # IMPORT ID

    next_import_id = backup.get(
        "next_import_id"
    )

    if next_import_id is not None:

        pipe.set(
            "next_import_id",
            next_import_id
        )

    pipe.execute()


def download_telegram_document(
    file_id
):

    result = tg(
        "getFile",
        {
            "file_id": file_id
        }
    )

    if (
        not result
        or not result.get(
            "ok"
        )
    ):

        return None

    file_path = (
        result.get(
            "result",
            {}
        ).get(
            "file_path"
        )
    )

    if not file_path:

        return None

    url = (
        f"https://api.telegram.org/file/"
        f"bot{BOT_TOKEN}/"
        f"{file_path}"
    )

    try:

        response = requests.get(
            url,
            timeout=60
        )

        response.raise_for_status()

        return response.content

    except Exception as e:

        print(
            "Download backup error:",
            e,
            flush=True
        )

        return None


# =========================================================
# WEB
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    try:

        db.ping()

        database = (
            "connected"
        )

    except Exception as e:

        database = (
            f"error: {e}"
        )

    return (
        "Vlad Media Library Bot is running. "
        f"Database: {database}"
    )


@app.route(
    "/setup",
    methods=["GET"]
)
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


@app.route(
    "/checkbot",
    methods=["GET"]
)
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

@app.route(
    "/webhook",
    methods=["POST"]
)
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
    # CHANNEL
    # =====================================================

    post = update.get(
        "channel_post"
    )

    if post:

        channel_id = (
            post.get(
                "chat",
                {}
            ).get(
                "id"
            )
        )

        if (
            channel_id
            != CHANNEL_ID
        ):

            return "OK", 200

        media_type, file_id = (
            detect_media(
                post
            )
        )

        if not file_id:

            return "OK", 200

        message_id = post[
            "message_id"
        ]

        item = {

            "item_id":
                f"channel:{message_id}",

            "source":
                "channel",

            "source_chat_id":
                CHANNEL_ID,

            "message_id":
                message_id,

            "file_id":
                file_id,

            "media_type":
                media_type
        }

        was_empty = add_to_queue(
            item
        )

        if was_empty:

            ask_for_next_tags()

        return "OK", 200


    # =====================================================
    # PRIVATE MESSAGE
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

    if (
        chat.get(
            "type"
        )
        != "private"
    ):

        return "OK", 200

    chat_id = chat[
        "id"
    ]

    text = (
        message.get(
            "text"
        )
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
            "пересылай фото или видео сюда.\n\n"

            f"ПОИСК:\n"
            f"до {SEARCH_LIMIT} случайных "
            "материалов за запрос.\n\n"

            "Команды:\n"

            "/search тег — фото + видео\n"
            "/photo тег — только фото\n"
            "/video тег — только видео\n"

            "/tags — все теги\n"
            "/count — статистика\n"
            "/storage — память базы\n\n"

            "/backup — создать бэкап\n"
            "/backupinfo — последний бэкап\n\n"

            "/queue — очередь\n"
            "/next — текущий материал\n"
            "/skip — отложить\n"
            "/delete — убрать из очереди\n\n"

            "🛡 Автобэкап включён."
        )

        if (
            db.llen(
                "media_queue"
            )
            > 0
        ):

            ask_for_next_tags()

        return "OK", 200


    # =====================================================
    # OWNER CHECK
    # =====================================================

    owner = get_owner()

    if owner != chat_id:

        send_message(
            chat_id,
            "⛔ Нет доступа."
        )

        return "OK", 200


    # =====================================================
    # BACKUP FILE RESTORE
    # =====================================================

    document = message.get(
        "document"
    )

    if document:

        filename = (
            document.get(
                "file_name"
            )
            or ""
        )

        if (
            filename.startswith(
                "vlad_media_backup_"
            )
            and filename.endswith(
                ".json"
            )
        ):

            content = (
                download_telegram_document(
                    document[
                        "file_id"
                    ]
                )
            )

            if not content:

                send_message(
                    chat_id,
                    "❌ Не удалось скачать бэкап."
                )

                return "OK", 200

            try:

                backup = json.loads(
                    content.decode(
                        "utf-8"
                    )
                )

                if (
                    backup.get(
                        "backup_format"
                    )
                    != "vlad_media_library"
                ):

                    raise ValueError(
                        "wrong backup"
                    )

                # Пока только сохраняем файл
                # для подтверждения восстановления

                db.set(
                    "pending_restore",
                    content.decode(
                        "utf-8"
                    ),
                    ex=600
                )

                send_message(
                    chat_id,
                    "⚠️ Бэкап распознан.\n\n"
                    "Чтобы восстановить базу "
                    "из этого файла, отправь:\n\n"
                    "/restore\n\n"
                    "Текущий каталог будет заменён "
                    "данными из бэкапа."
                )

            except Exception:

                send_message(
                    chat_id,
                    "❌ Это не подходящий "
                    "файл бэкапа."
                )

            return "OK", 200


    # =====================================================
    # RESTORE CONFIRMATION
    # =====================================================

    if text == "/restore":

        pending = db.get(
            "pending_restore"
        )

        if not pending:

            send_message(
                chat_id,
                "Нет ожидающего восстановления.\n\n"
                "Сначала отправь мне файл "
                "vlad_media_backup_....json"
            )

            return "OK", 200

        try:

            backup = json.loads(
                pending
            )

            restore_backup(
                backup
            )

            db.delete(
                "pending_restore"
            )

            # После восстановления сразу
            # создаём свежую контрольную копию

            create_backup(
                chat_id,
                automatic=False
            )

            send_message(
                chat_id,
                "✅ Каталог восстановлен "
                "из бэкапа."
            )

        except Exception as e:

            print(
                "Restore error:",
                e,
                flush=True
            )

            send_message(
                chat_id,
                "❌ Ошибка восстановления."
            )

        return "OK", 200


    # =====================================================
    # MANUAL BACKUP
    # =====================================================

    if text == "/backup":

        send_message(
            chat_id,
            "⏳ Создаю бэкап..."
        )

        success = create_backup(
            chat_id,
            automatic=False
        )

        if not success:

            send_message(
                chat_id,
                "❌ Не удалось создать бэкап."
            )

        return "OK", 200


    # =====================================================
    # BACKUP INFO
    # =====================================================

    if text == "/backupinfo":

        last = db.get(
            "last_backup_at"
        )

        count = db.get(
            "last_backup_count"
        )

        if not last:

            send_message(
                chat_id,
                "🛡 Бэкапов ещё не было.\n\n"
                "Используй /backup."
            )

            return "OK", 200

        try:

            dt = datetime.fromisoformat(
                last
            )

            date_text = dt.strftime(
                "%d.%m.%Y %H:%M UTC"
            )

        except Exception:

            date_text = last

        send_message(
            chat_id,
            "🛡 Последний бэкап\n\n"
            f"📅 {date_text}\n"
            f"📚 Материалов: {count or '?'}\n\n"
            f"Автобэкап: каждые "
            f"{AUTO_BACKUP_DAYS} дней "
            "при работе с каталогом."
        )

        return "OK", 200


    # =====================================================
    # IMPORT MEDIA
    # =====================================================

    media_type, file_id = (
        detect_media(
            message
        )
    )

    if file_id:

        import_id = db.incr(
            "next_import_id"
        )

        item = {

            "item_id":
                f"import:{import_id}",

            "source":
                "import",

            "source_chat_id":
                chat_id,

            "message_id":
                message.get(
                    "message_id"
                ),

            "file_id":
                file_id,

            "media_type":
                media_type
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
                f"Сейчас в очереди: "
                f"{queue_size}"
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

            item = get_item(
                item_id
            )

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

        videos += len(
            legacy_ids
        )

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

        storage = (
            get_storage_info()
        )

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

        percent = storage[
            "percent"
        ]

        total = (
            storage[
                "total_items"
            ]
            + storage[
                "legacy_items"
            ]
        )

        tag_count = storage[
            "tag_count"
        ]

        queue_items = storage[
            "queue_items"
        ]

        estimated_left = storage[
            "estimated_left"
        ]

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

        msg = (
            "💾 Хранилище медиатеки\n\n"
            f"{bar}\n\n"
            f"Использовано Redis: "
            f"{used_mb:.2f} МБ\n"
            f"Расчётный лимит: "
            f"{REDIS_LIMIT_MB} МБ\n"
            f"Заполнено: "
            f"{percent:.1f}%\n\n"
            f"📚 Материалов: {total}\n"
            f"🏷 Тегов: {tag_count}\n"
            f"⏳ В очереди: {queue_items}"
        )

        if (
            estimated_left
            is not None
        ):

            left = (
                f"{estimated_left:,}"
                .replace(
                    ",",
                    " "
                )
            )

            msg += (
                "\n\n"
                "📊 Примерно осталось места:\n"
                f"≈ {left} материалов"
            )

        send_message(
            chat_id,
            msg
        )

        return "OK", 200


    # =====================================================
    # QUEUE
    # =====================================================

    if text == "/queue":

        send_message(
            chat_id,
            f"⏳ В очереди: "
            f"{db.llen('media_queue')}"
        )

        return "OK", 200


    # =====================================================
    # NEXT
    # =====================================================

    if text == "/next":

        if (
            db.llen(
                "media_queue"
            )
            == 0
        ):

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
                "🗑 Убрал материал "
                "из очереди."
            )

            if (
                db.llen(
                    "media_queue"
                )
                > 0
            ):

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

            tag = key[
                4:
            ]

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
                + "\n".join(
                    lines
                )
            )

        return "OK", 200


    # =====================================================
    # SEARCH
    # =====================================================

    search_mode = None
    query = None

    if text.startswith(
        "/search"
    ):

        search_mode = "all"

        query = text[
            len(
                "/search"
            ):
        ].strip()

    elif text.startswith(
        "/photo"
    ):

        search_mode = "photo"

        query = text[
            len(
                "/photo"
            ):
        ].strip()

    elif text.startswith(
        "/video"
    ):

        search_mode = "video"

        query = text[
            len(
                "/video"
            ):
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

        if (
            search_mode
            == "photo"
        ):

            items = [

                item

                for item in items

                if item.get(
                    "media_type"
                )

                in (
                    "photo",
                    "image_document"
                )
            ]

        elif (
            search_mode
            == "video"
        ):

            items = [

                item

                for item in items

                if item.get(
                    "media_type"
                )

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

        total_found = len(
            items
        )

        selected = items[
            :SEARCH_LIMIT
        ]

        send_message(
            chat_id,
            f"🎲 Найдено: "
            f"{total_found}\n"
            f"Показываю: "
            f"{len(selected)} "
            "случайных."
        )

        for item in selected:

            send_media(
                chat_id,
                item
            )

            item_tags = item.get(
                "tags",
                []
            )

            if item_tags:

                formatted = (
                    ", ".join(
                        "#"
                        + tag

                        for tag
                        in item_tags
                    )
                )

                send_message(
                    chat_id,
                    f"🏷 {formatted}"
                )

        if (
            total_found
            > SEARCH_LIMIT
        ):

            send_message(
                chat_id,
                f"🎲 Показаны случайные "
                f"{SEARCH_LIMIT} из "
                f"{total_found}.\n\n"
                "Повтори поиск — "
                "подборка перемешается."
            )

        return "OK", 200


    # =====================================================
    # TAG INPUT
    # =====================================================

    pending = current_pending()

    if (
        pending
        and text
    ):

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

        formatted = (
            ", ".join(
                "#"
                + tag

                for tag
                in tags
            )
        )

        if pending.get(
            "media_type"
        ) in (
            "photo",
            "image_document"
        ):

            media_name = (
                "Фото"
            )

        else:

            media_name = (
                "Видео"
            )

        send_message(
            chat_id,
            f"✅ {media_name} сохранено!\n\n"
            f"🏷 {formatted}"
        )

        # Проверяем необходимость
        # автоматического бэкапа
        maybe_auto_backup()

        if (
            db.llen(
                "media_queue"
            )
            > 0
        ):

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
            "Или перешли фото/видео "
            "прямо мне."
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
