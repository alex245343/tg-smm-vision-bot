# -*- coding: utf-8 -*-
import os
import sys
import time
import json
import io
import base64
import urllib.request
import urllib.error
import threading
from PIL import Image

# Read credentials and optional proxy settings
TG_TOKEN = os.environ.get("TG_TOKEN") or os.environ.get("BOT_TOKEN") or "8414879801:AAGfklQ9SvExA7MXF2CMWdJWeTjyZMYLXW0"
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_KEY") or "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"

# Allow custom proxy / base URL override (e.g. if host is in geo-blocked region)
NVIDIA_URL = os.environ.get("NVIDIA_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://integrate.api.nvidia.com/v1/chat/completions"
VISION_MODEL = os.environ.get("VISION_MODEL") or "meta/llama-3.2-11b-vision-instruct"

# Setup global proxy if HTTPS_PROXY / HTTP_PROXY is specified
proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
if proxy_url:
    proxy_handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
    opener = urllib.request.build_opener(proxy_handler)
    urllib.request.install_opener(opener)
    print(f"[*] Proxy enabled: {proxy_url}")

user_sessions = {}
session_lock = threading.Lock()

SYSTEM_PROMPT_NEW = (
    "Ты — профессиональный креативный SMM-копирайтер для Telegram-каналов. "
    "Твоя задача — внимательно изучить изображение и составить живой, эстетичный авторский пост на русском языке.\n\n"
    "ПРАВИЛА:\n"
    "1. НЕ пиши служебных меток Заголовок:, Описание:, Эмоция:, Призыв:, Хэштеги:. Это запрещено!\n"
    "2. Напиши единый цельный текст с абзацами и эмодзи.\n"
    "3. Опиши конкретные детали с фото: свет, тени, композицию, настроение.\n"
    "4. В конце добавь 3-5 хэштегов."
)

SYSTEM_PROMPT_EDIT = (
    "Ты — профессиональный редактор контента. Пользователь дал конкретное указание, как изменить существующий пост. "
    "Твоя задача — полностью переписать текст поста в точном соответствии с новым требованием (изменить тон, назвать объект некрасивым/смешным/грустным, сократить, поменять стиль). "
    "НЕ повторяй старый пост, если требование требует его изменить. Выдай только новый готовый текст поста."
)

def call_nvidia_nim(messages, retries=2):
    payload = {
        "model": VISION_MODEL,
        "messages": messages,
        "max_tokens": 800,
        "temperature": 0.7,
        "top_p": 0.9
    }
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "NVIDIA-NIM-Client/1.0"
    }
    data = json.dumps(payload).encode("utf-8")
    
    for attempt in range(retries + 1):
        req = urllib.request.Request(NVIDIA_URL, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                raw_text = res_json["choices"][0]["message"]["content"]
                return clean_response(raw_text)
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8")
            print(f"[NVIDIA HTTP ERROR] attempt {attempt}: {e.code} - {err_msg}", file=sys.stderr)
            if e.code == 451:
                return (
                    "⚠️ Ошибка 451 (Geo-Block):
"
                    "Сервер хостинга находится в регионе, заблокированном NVIDIA API по геолокации.
"
                    "👉 Укажите рабочий прокси в переменных окружения хостинга: HTTPS_PROXY=http://user:pass@ip:port"
                )
            if attempt == retries:
                return f"⚠️ Ошибка сервера модели ({e.code}). Попробуйте через минуту."
            time.sleep(2)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"[NVIDIA TIMEOUT] attempt {attempt}: {e}", file=sys.stderr)
            if attempt == retries:
                return "⚠️ Сервер модели был временно перегружен и не успел ответить. Попробуйте еще раз."
            time.sleep(2)
        except Exception as e:
            print(f"[NVIDIA ERROR] {e}", file=sys.stderr)
            return f"⚠️ Ошибка: {e}"

def clean_response(text):
    cleaned = text.strip()
    if cleaned.startswith("[{'type':") or cleaned.startswith('[{"type":'):
        try:
            parsed = json.loads(cleaned.replace("'", '"'))
            if isinstance(parsed, list) and len(parsed) > 0 and "text" in parsed[0]:
                cleaned = parsed[0]["text"]
        except Exception:
            pass
    return cleaned

def tg_api_call(method, params=None):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"
    if params:
        data = json.dumps(params).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers)
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[TG ERROR] {method}: {e}", file=sys.stderr)
        return None

def send_chat_action(chat_id, action="typing"):
    tg_api_call("sendChatAction", {"chat_id": chat_id, "action": action})

def send_photo_with_caption(chat_id, file_id, caption):
    if len(caption) <= 1024:
        params = {
            "chat_id": chat_id,
            "photo": file_id,
            "caption": caption
        }
        res = tg_api_call("sendPhoto", params)
        if res and res.get("ok"):
            return
    tg_api_call("sendPhoto", {"chat_id": chat_id, "photo": file_id})
    tg_api_call("sendMessage", {"chat_id": chat_id, "text": caption})

def send_message(chat_id, text):
    tg_api_call("sendMessage", {"chat_id": chat_id, "text": text})

def download_and_compress_image(file_id, max_size=768):
    file_info = tg_api_call("getFile", {"file_id": file_id})
    if not file_info or not file_info.get("ok"):
        return None
    file_path = file_info["result"]["file_path"]
    download_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}"
    try:
        with urllib.request.urlopen(download_url, timeout=30) as resp:
            img_bytes = resp.read()
            
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        out_buf = io.BytesIO()
        img.save(out_buf, format="JPEG", quality=75, optimize=True)
        b64_str = base64.b64encode(out_buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64_str}"
    except Exception as e:
        print(f"[IMAGE PROCESSING ERROR] {e}", file=sys.stderr)
        return None

def handle_update(update):
    msg = update.get("message")
    if not msg:
        return
    
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "")
    caption = msg.get("caption", "")
    photos = msg.get("photo")
    
    # 1. /start command
    if text and text.startswith("/start"):
        with session_lock:
            user_sessions.pop(user_id, None)
        send_message(
            chat_id,
            "👋 Привет! Пришли мне фотографию, и я создам готовый пост для Telegram-канала.\n\n"
            "• К фото можно добавить подпись с пожеланиями по стилю или теме.\n"
            "• Я пришлю фото вместе с готовым постом.\n"
            "• Ты можешь писать любые правки («сделай с юмором», «скажи что некрасиво», «короче») — я перепишу пост под твои слова.\n"
            "• Новая фотография сбрасывает старый пост и начинает новый диалог!"
        )
        return

    # 2. User sent a NEW PHOTO
    if photos:
        send_chat_action(chat_id, "typing")
        photo_file_id = photos[-1]["file_id"]
        
        img_b64 = download_and_compress_image(photo_file_id)
        if not img_b64:
            send_message(chat_id, "❌ Не удалось загрузить фото из Telegram. Попробуйте еще раз.")
            return

        instruction = caption.strip() if caption else "Напиши атмосферный, красивый авторский пост для Telegram-канала к этой фотографии."
        prompt_text = (
            f"Пользователь прислал эту фотографию с пожеланием:\n«{instruction}»\n\n"
            "Составь готовый пост для Telegram-канала (без анкетных заголовков)."
        )
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_NEW},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": img_b64}}
                ]
            }
        ]
        
        send_chat_action(chat_id, "typing")
        post_text = call_nvidia_nim(messages)
        
        with session_lock:
            user_sessions[user_id] = {
                "file_id": photo_file_id,
                "current_post": post_text
            }
            
        send_photo_with_caption(chat_id, photo_file_id, post_text)
        return

    # 3. User sent TEXT (Editing / Feedback)
    if text:
        with session_lock:
            session = user_sessions.get(user_id)
            
        if not session:
            send_message(chat_id, "📸 Сначала отправь фотографию, чтобы я написал к ней пост!")
            return
            
        send_chat_action(chat_id, "typing")
        current_post = session["current_post"]
        
        edit_messages = [
            {"role": "system", "content": SYSTEM_PROMPT_EDIT},
            {
                "role": "user",
                "content": (
                    f"Вот текущий текст поста:\n«{current_post}»\n\n"
                    f"Указание пользователя по переделке:\n«{text}»\n\n"
                    "Перепиши пост с нуля, строго следуя новому указанию пользователя. Выдай только готовый текст нового поста:"
                )
            }
        ]
        
        updated_post = call_nvidia_nim(edit_messages)
        
        with session_lock:
            user_sessions[user_id]["current_post"] = updated_post
            
        send_photo_with_caption(chat_id, session["file_id"], updated_post)
        return

def main():
    print("Starting Fully Reactive Vision Bot (NVIDIA NIM)...")
    offset = 0
    tg_api_call("getUpdates", {"offset": -1})
    while True:
        try:
            updates_res = tg_api_call("getUpdates", {"offset": offset, "timeout": 25})
            if updates_res and updates_res.get("ok"):
                for update in updates_res["result"]:
                    offset = update["update_id"] + 1
                    try:
                        handle_update(update)
                    except Exception as e:
                        print(f"[HANDLER ERROR] {e}", file=sys.stderr)
            else:
                time.sleep(1)
        except Exception as e:
            print(f"[POLL ERROR] {e}", file=sys.stderr)
            time.sleep(2)

if __name__ == "__main__":
    main()
