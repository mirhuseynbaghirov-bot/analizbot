import asyncio
import json
import logging
import os
import threading

import requests
import google.generativeai as genai
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# ---------------- Flask (Render + UptimeRobot üçün) ----------------
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot aktivdir və işləyir!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ---------------- Ayarlar (Render > Environment) ----------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")

# RapidAPI Code Snippets-dən yoxla və lazım olsa Render-də dəyiş:
RAPIDAPI_HOST = os.environ.get("RAPIDAPI_HOST", "instagram-scraper-stable-api.p.rapidapi.com")
RAPIDAPI_ENDPOINT = os.environ.get("RAPIDAPI_ENDPOINT", "get_ig_user_posts.php")
RAPIDAPI_PARAM = os.environ.get("RAPIDAPI_PARAM", "username_or_url")
RAPIDAPI_METHOD = os.environ.get("RAPIDAPI_METHOD", "POST").upper()

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")


# ---------------- Köməkçi funksiyalar ----------------
def extract_username(text: str) -> str:
    text = text.strip()
    if "instagram.com" in text:
        text = text.split("instagram.com/")[-1]
        text = text.split("?")[0].strip("/").split("/")[0]
    return text.replace("@", "").strip()


def call_rapidapi(username: str) -> dict:
    url = f"https://{RAPIDAPI_HOST}/{RAPIDAPI_ENDPOINT}"
    headers = {
        "x-rapidapi-host": RAPIDAPI_HOST,
        "x-rapidapi-key": RAPIDAPI_KEY,
    }
    payload = {RAPIDAPI_PARAM: username}

    if RAPIDAPI_METHOD == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        resp = requests.post(url, headers=headers, data=payload, timeout=45)
    else:
        resp = requests.get(url, headers=headers, params=payload, timeout=45)

    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text[:500]}


def find_post_list(data):
    """Cavabın içində post siyahısını tapır (format fərqli ola bilər)."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return data
        return []
    if not isinstance(data, dict):
        return []

    for key in ("posts", "items", "edges", "data", "user", "result", "response"):
        if key in data:
            found = find_post_list(data[key])
            if found:
                return found

    user = data.get("user")
    if isinstance(user, dict):
        timeline = user.get("edge_owner_to_timeline_media", {})
        if timeline.get("edges"):
            return timeline["edges"]

    timeline = data.get("edge_owner_to_timeline_media")
    if isinstance(timeline, dict) and timeline.get("edges"):
        return timeline["edges"]

    return []


def parse_post(item):
    node = item.get("node", item) if isinstance(item, dict) else {}
    if not isinstance(node, dict):
        return None

    # Caption
    caption = ""
    edges = node.get("edge_media_to_caption", {}).get("edges") if isinstance(
        node.get("edge_media_to_caption"), dict
    ) else None
    if edges:
        caption = edges[0].get("node", {}).get("text", "")
    elif isinstance(node.get("caption"), dict):
        caption = node["caption"].get("text", "")
    elif isinstance(node.get("caption"), str):
        caption = node["caption"]

    # Bəyənmə
    likes = node.get("like_count")
    if likes is None:
        likes = (node.get("edge_media_preview_like") or {}).get("count")
    if likes is None:
        likes = (node.get("edge_liked_by") or {}).get("count", 0)

    # Şərh
    comments = node.get("comment_count")
    if comments is None:
        comments = (node.get("edge_media_to_comment") or {}).get("count", 0)

    return {
        "caption": (caption or "")[:400],
        "likes": likes or 0,
        "comments_count": comments or 0,
    }


async def send_long(update: Update, text: str):
    """Telegram 4096 simvol limitinə görə hissə-hissə göndər."""
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i : i + 4000])


# ---------------- Bot handlerləri ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salam! Mən Sənin Ətir Mağazası üçün AI Sosial Media Analitikisəm. 🧪\n\n"
        "Instagram profilinin istifadəçi adını (məsələn: sehife_adi) və ya linkini göndər.\n"
        "Mən son postları, bəyənmələri və şərhləri analiz edib sənə stok, reklam və kontent məsləhəti verim!"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = extract_username(update.message.text)
    if not username:
        await update.message.reply_text("İstifadəçi adı tapılmadı. Yenidən yaz.")
        return

    await update.message.reply_text(
        f"🔎 '@{username}' profilinin məlumatları çəkilir və AI analiz edir. Xahiş olunur gözləyin..."
    )

    try:
        # requests bloklayıcıdır, ona görə ayrı thread-də işlədirik
        res_data = await asyncio.to_thread(call_rapidapi, username)

        items = find_post_list(res_data)
        posts_data = [p for p in (parse_post(i) for i in items[:6]) if p]

        if not posts_data:
            preview = json.dumps(res_data, indent=2, ensure_ascii=False)[:1500]
            await update.message.reply_text(f"⚠️ API cavabından post tapılmadı:\n\n{preview}")
            return

        prompt = (
            "Sən təcrübəli Lüks Ətir Mağazası Biznes Konsultantı və Sosial Media Strategisən.\n"
            f"Aşağıda Instagram profilinin son {len(posts_data)} postunun məlumatları var:\n\n"
            f"Məlumatlar:\n{json.dumps(posts_data, ensure_ascii=False)}\n\n"
            "Bu göstəriciləri əsas götürərək, mağaza sahibinə aydın və dəqiq hesabat hazırla:\n\n"
            "🔥 1. TOP MƏHSUL (Ən Çox Bəyənilən Və İstənilən Ətir)\n"
            "📉 2. ZƏİF PERFORMANSLI MƏHSUL\n"
            "💬 3. MÜŞTƏRİ TƏLƏBİ VƏ İŞTİRAK ANALİZİ\n"
            "📦 4. STOK VƏ SİFARİŞ MƏSLƏHƏTİ\n"
            "📸 5. KONTENT VƏ FORMAT STRATEGİYASI\n"
            "💡 6. XÜSUSİ BİZNES TÖVSİYƏLƏRİ"
        )

        ai_response = await asyncio.to_thread(model.generate_content, prompt)
        await send_long(update, ai_response.text)

    except Exception as e:
        logging.exception("Xəta")
        await update.message.reply_text(f"❌ Xəta baş verdi: {e}")


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()

    tg_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    tg_app.run_polling()
