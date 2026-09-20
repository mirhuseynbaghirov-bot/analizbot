import asyncio
import json
import logging
import os
import re
import threading

import requests
import google.generativeai as genai
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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

RAPIDAPI_HOST = os.environ.get("RAPIDAPI_HOST", "instagram-scraper-stable-api.p.rapidapi.com")
RAPIDAPI_ENDPOINT = os.environ.get("RAPIDAPI_ENDPOINT", "get_ig_user_posts.php")
RAPIDAPI_PARAM = os.environ.get("RAPIDAPI_PARAM", "username_or_url")
RAPIDAPI_METHOD = os.environ.get("RAPIDAPI_METHOD", "POST").upper()

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL)

FORMAT_RULES = (
    "\n\nFORMAT QAYDALARI (çox vacib):\n"
    "- Cavab Telegram üçün SADƏ MƏTN olmalıdır.\n"
    "- Markdown işlətmə: #, ##, ###, **, *, ---, ``` qadağandır.\n"
    "- Bölmə başlıqlarını uyğun emoji ilə və BÖYÜK hərflə yaz.\n"
    "- Siyahı üçün yalnız '• ' işarəsini işlət.\n"
    "- Bölmələr arasında boş sətir burax. Giriş və çıxış salamlaması yazma."
)


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

    caption = ""
    edges = (
        node.get("edge_media_to_caption", {}).get("edges")
        if isinstance(node.get("edge_media_to_caption"), dict)
        else None
    )
    if edges:
        caption = edges[0].get("node", {}).get("text", "")
    elif isinstance(node.get("caption"), dict):
        caption = node["caption"].get("text", "")
    elif isinstance(node.get("caption"), str):
        caption = node["caption"]

    likes = node.get("like_count")
    if likes is None:
        likes = (node.get("edge_media_preview_like") or {}).get("count")
    if likes is None:
        likes = (node.get("edge_liked_by") or {}).get("count", 0)

    comments = node.get("comment_count")
    if comments is None:
        comments = (node.get("edge_media_to_comment") or {}).get("count", 0)

    return {
        "caption": (caption or "")[:400],
        "likes": likes or 0,
        "comments_count": comments or 0,
    }


def clean_text(text: str) -> str:
    """Gemini yenə də markdown yazsa, Telegram üçün təmizləyir."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("---", "***", "___"):
            continue
        stripped = re.sub(r"^#{1,6}\s*", "", stripped)
        stripped = re.sub(r"^[\*\-]\s+", "• ", stripped)
        stripped = stripped.replace("**", "").replace("__", "").replace("`", "")
        lines.append(stripped)
    result = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def post_stats(posts):
    n = len(posts) or 1
    return {
        "post_sayi": len(posts),
        "orta_bəyənmə": round(sum(p["likes"] for p in posts) / n),
        "orta_şərh": round(sum(p["comments_count"] for p in posts) / n),
    }


async def fetch_posts(username: str):
    res_data = await asyncio.to_thread(call_rapidapi, username)
    items = find_post_list(res_data)
    posts = [p for p in (parse_post(i) for i in items[:6]) if p]
    return posts, res_data


async def gemini_text(prompt: str) -> str:
    resp = await asyncio.to_thread(model.generate_content, prompt + FORMAT_RULES)
    return clean_text(resp.text)


async def send_long(message, text: str, reply_markup=None):
    """Telegram 4096 limitinə görə hissələrə böl. Düymələr son hissədə olur."""
    chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)] or [""]
    for idx, chunk in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        await message.reply_text(chunk, reply_markup=markup)


def result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🆚 Rəqiblə müqayisə", callback_data="compare")],
            [
                InlineKeyboardButton("📅 30 günlük plan", callback_data="plan"),
                InlineKeyboardButton("💡 Reels ideyaları", callback_data="ideas"),
            ],
            [InlineKeyboardButton("🔄 Yeni profil analiz et", callback_data="new")],
        ]
    )


# ---------------- Promptlar ----------------
def analysis_prompt(username, posts):
    return (
        "Sən təcrübəli Sosial Media Analitiki və Strategisən.\n"
        f"Aşağıda '@{username}' Instagram profilinin son {len(posts)} postunun məlumatları var:\n\n"
        f"Məlumatlar:\n{json.dumps(posts, ensure_ascii=False)}\n\n"
        "Əvvəlcə postların məzmunundan profilin nişini (sahəsini) özün müəyyən et. "
        "Heç bir sahəni əvvəlcədən fərz etmə, yalnız verilən məlumatlara əsaslan.\n"
        "Sonra aşağıdakı bölmələrlə qısa, aydın və konkret hesabat yaz:\n\n"
        "📌 1. PROFİL XÜLASƏSİ (niş və ümumi təəssürat)\n"
        "🔥 2. ƏN UĞURLU POSTLAR (bəyənmə və şərhlə)\n"
        "📉 3. ZƏİF POSTLAR VƏ SƏBƏBLƏRİ\n"
        "💬 4. AUDİTORİYA REAKSİYASI\n"
        "📸 5. KONTENT VƏ FORMAT STRATEGİYASI\n"
        "💡 6. TÖVSİYƏLƏR"
    )


def plan_prompt(username, posts):
    return (
        "Sən Sosial Media Strategisən.\n"
        f"'@{username}' profilinin son postları:\n{json.dumps(posts, ensure_ascii=False)}\n\n"
        "Profilin nişini postlardan müəyyən et və hansı formatların daha yaxşı işlədiyini nəzərə alaraq "
        "30 günlük Instagram kontent planı hazırla.\n"
        "Plan 4 həftəyə bölünsün. Hər həftə üçün 3-4 konkret paylaşım yaz: "
        "gün, format (Reels/Karusel/Foto/Story) və qısa mövzu.\n"
        "Sonda 3 qısa ümumi məsləhət ver."
    )


def ideas_prompt(username, posts):
    return (
        "Sən Reels və kontent ideyaları üzrə mütəxəssissən.\n"
        f"'@{username}' profilinin son postları:\n{json.dumps(posts, ensure_ascii=False)}\n\n"
        "Profilin nişini postlardan müəyyən et və ona uyğun 8 Reels/post ideyası ver.\n"
        "Hər ideya üçün: başlıq, 1 cümləlik konsept və ilk 3 saniyənin (hook) mətni yazılsın.\n"
        "Sonda 5 uyğun hashtag təklif et."
    )


def compare_prompt(u1, p1, u2, p2):
    return (
        "Sən Sosial Media Analitikisən. İki Instagram profilini müqayisə et.\n\n"
        f"Profil 1: @{u1}\nOrta göstəricilər: {json.dumps(post_stats(p1), ensure_ascii=False)}\n"
        f"Postlar: {json.dumps(p1, ensure_ascii=False)}\n\n"
        f"Profil 2: @{u2}\nOrta göstəricilər: {json.dumps(post_stats(p2), ensure_ascii=False)}\n"
        f"Postlar: {json.dumps(p2, ensure_ascii=False)}\n\n"
        "Bölmələr:\n"
        "📊 1. ÜMUMİ MÜQAYİSƏ (orta bəyənmə və şərh)\n"
        "🏆 2. KİM ÖNDƏDİR VƏ NİYƏ\n"
        "📸 3. KONTENT FƏRQLƏRİ\n"
        "💡 4. BİRİNCİ PROFİL RƏQİBDƏN NƏ ÖYRƏNƏ BİLƏR\n"
        "🎯 5. 3 KONKRET ADDIM"
    )


# ---------------- Bot handlerləri ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Salam! Mən AI Instagram Analitikiyəm. 📊\n\n"
        "Instagram profilinin istifadəçi adını (məsələn: sehife_adi) və ya linkini göndər.\n"
        "Analizdən sonra düymələrlə rəqib müqayisəsi, kontent planı və Reels ideyaları ala bilərsən!"
    )


async def show_raw_error(message, res_data):
    preview = json.dumps(res_data, indent=2, ensure_ascii=False)[:1500]
    await message.reply_text(f"⚠️ API cavabından post tapılmadı:\n\n{preview}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = extract_username(update.message.text)
    if not username:
        await update.message.reply_text("İstifadəçi adı tapılmadı. Yenidən yaz.")
        return

    # --- Rəqib müqayisəsi rejimi ---
    if context.user_data.get("awaiting_compare"):
        context.user_data["awaiting_compare"] = False
        main_user = context.user_data.get("username")
        main_posts = context.user_data.get("posts")
        if not main_posts:
            await update.message.reply_text("Əvvəlcə əsas profili analiz et.")
            return

        await update.message.reply_text(f"🆚 '@{username}' ilə müqayisə edilir. Gözləyin...")
        try:
            posts2, res2 = await fetch_posts(username)
            if not posts2:
                await show_raw_error(update.message, res2)
                return
            text = await gemini_text(compare_prompt(main_user, main_posts, username, posts2))
            await send_long(update.message, text, result_keyboard())
        except Exception as e:
            logging.exception("Müqayisə xətası")
            await update.message.reply_text(f"❌ Xəta baş verdi: {e}")
        return

    # --- Adi analiz ---
    await update.message.reply_text(
        f"🔎 '@{username}' profilinin məlumatları çəkilir və AI analiz edir. Xahiş olunur gözləyin..."
    )
    try:
        posts, res_data = await fetch_posts(username)
        if not posts:
            await show_raw_error(update.message, res_data)
            return

        context.user_data["username"] = username
        context.user_data["posts"] = posts

        text = await gemini_text(analysis_prompt(username, posts))
        await send_long(update.message, text, result_keyboard())

    except Exception as e:
        logging.exception("Xəta")
        await update.message.reply_text(f"❌ Xəta baş verdi: {e}")


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    message = query.message

    if action == "new":
        context.user_data["awaiting_compare"] = False
        await message.reply_text("Yeni profilin istifadəçi adını və ya linkini göndər. 👇")
        return

    username = context.user_data.get("username")
    posts = context.user_data.get("posts")
    if not posts:
        await message.reply_text("Məlumat köhnəlib. Profil adını yenidən göndər.")
        return

    if action == "compare":
        context.user_data["awaiting_compare"] = True
        await message.reply_text(
            f"@{username} ilə müqayisə üçün rəqibin istifadəçi adını və ya linkini göndər. 👇"
        )
        return

    try:
        if action == "plan":
            await message.reply_text("📅 30 günlük plan hazırlanır...")
            text = await gemini_text(plan_prompt(username, posts))
        elif action == "ideas":
            await message.reply_text("💡 İdeyalar hazırlanır...")
            text = await gemini_text(ideas_prompt(username, posts))
        else:
            return
        await send_long(message, text, result_keyboard())
    except Exception as e:
        logging.exception("Düymə xətası")
        await message.reply_text(f"❌ Xəta baş verdi: {e}")


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()

    tg_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CallbackQueryHandler(handle_button))
    tg_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    tg_app.run_polling()
