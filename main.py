import asyncio
import json
import logging
import os
import re
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

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

# ---------------- Müəllif məlumatları ----------------
AUTHOR_NAME = os.environ.get("AUTHOR_NAME", "").strip()
AUTHOR_TELEGRAM = os.environ.get("AUTHOR_TELEGRAM", "").strip().lstrip("@")
AUTHOR_INSTAGRAM = os.environ.get("AUTHOR_INSTAGRAM", "").strip().lstrip("@")
CHANNEL_URL = os.environ.get("CHANNEL_URL", "").strip()
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip().lstrip("@")

LINE = "━━━━━━━━━━━━━━━"


def author_line() -> str:
    if not (AUTHOR_NAME or AUTHOR_TELEGRAM):
        return ""
    line = "👨‍💻 Hazırlayan: " + (AUTHOR_NAME or f"@{AUTHOR_TELEGRAM}")
    if AUTHOR_NAME and AUTHOR_TELEGRAM:
        line += f" (@{AUTHOR_TELEGRAM})"
    return line


def author_link_buttons():
    rows = []
    if CHANNEL_URL:
        rows.append([InlineKeyboardButton("📢 Telegram kanalı", url=CHANNEL_URL)])
    if AUTHOR_INSTAGRAM:
        rows.append(
            [InlineKeyboardButton("📸 Instagram", url=f"https://instagram.com/{AUTHOR_INSTAGRAM}")]
        )
    if AUTHOR_TELEGRAM:
        rows.append([InlineKeyboardButton("💬 Müəllifə yaz", url=f"https://t.me/{AUTHOR_TELEGRAM}")])
    return rows


def about_text() -> str:
    who_ = AUTHOR_NAME or (f"@{AUTHOR_TELEGRAM}" if AUTHOR_TELEGRAM else "müəllif")
    return (
        "👨‍💻 BOT HAQQINDA\n"
        f"{LINE}\n\n"
        f"Bu bot {who_} tərəfindən SMM mütəxəssisləri və biznes sahibləri üçün hazırlanıb.\n\n"
        "▫️ Instagram profillərini AI ilə analiz edir\n"
        "▫️ Rəqib müqayisəsi edir\n"
        "▫️ Kontent planı və Reels ideyaları verir\n\n"
        "Rəy, təklif və ya yeni bot sifarişi üçün müəllifə yaza bilərsən. 👇"
    )


def share_url() -> str:
    text = "Instagram profilini AI ilə pulsuz analiz edən bot 📊"
    return (
        "https://t.me/share/url?url="
        + quote(f"https://t.me/{BOT_USERNAME}")
        + "&text="
        + quote(text)
    )


# ---------------- Statistika ----------------
ADMIN_ID = os.environ.get("ADMIN_ID", "").strip()
STATS_FILE = os.environ.get("STATS_FILE", "stats.json")
_stats_lock = threading.Lock()


def _load_stats():
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("users", {})
            data.setdefault("analyses", [])
            return data
    except Exception:
        return {"users": {}, "analyses": []}


STATS = _load_stats()


def _save_stats():
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(STATS, f, ensure_ascii=False)
    except Exception:
        logging.exception("Statistika yazıla bilmədi")


def who(user) -> str:
    if user.username:
        return f"@{user.username}"
    return f"{user.full_name} (id {user.id})"


def track_user(user) -> bool:
    uid = str(user.id)
    with _stats_lock:
        if uid in STATS["users"]:
            return False
        STATS["users"][uid] = {
            "name": who(user),
            "first_seen": datetime.now(timezone.utc).isoformat(),
        }
        _save_stats()
        return True


def track_analysis(user, profile: str):
    with _stats_lock:
        STATS["analyses"].append(
            {"t": datetime.now(timezone.utc).isoformat(), "u": str(user.id), "p": profile}
        )
        STATS["analyses"] = STATS["analyses"][-5000:]
        _save_stats()


async def notify_admin(context, text: str):
    if not ADMIN_ID:
        return
    try:
        await context.bot.send_message(chat_id=int(ADMIN_ID), text=text)
    except Exception:
        logging.exception("Admin bildirişi göndərilmədi")


# ---------------- Instagram məlumatı ----------------
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


async def fetch_posts(username: str):
    res_data = await asyncio.to_thread(call_rapidapi, username)
    items = find_post_list(res_data)
    posts = [p for p in (parse_post(i) for i in items[:6]) if p]
    return posts, res_data


def post_stats(posts):
    n = len(posts) or 1
    return {
        "post_sayi": len(posts),
        "orta_bəyənmə": round(sum(p["likes"] for p in posts) / n),
        "orta_şərh": round(sum(p["comments_count"] for p in posts) / n),
    }


# ---------------- Mətn təmizləmə və Gemini ----------------
FORMAT_RULES = (
    "\n\nFORMAT QAYDALARI (çox vacib):\n"
    "- Cavab Telegram üçün SADƏ MƏTN olmalıdır. Uzun paraqraf YAZMA.\n"
    "- Markdown işlətmə: #, ##, ###, **, *, ---, ``` qadağandır.\n"
    "- Hər sətir maksimum 15 söz olsun, qısa və konkret yaz.\n"
    "- Siyahı üçün yalnız '▫️ ' işarəsini işlət.\n"
    "- Bölmə başlıqlarını uyğun emoji ilə və BÖYÜK hərflə yaz, bölmələr arasında boş sətir burax.\n"
    "- Giriş və çıxış salamlaması yazma."
)


def clean_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("---", "***", "___"):
            continue
        stripped = re.sub(r"^#{1,6}\s*", "", stripped)
        stripped = re.sub(r"^[\*\-•]\s+", "▫️ ", stripped)
        stripped = stripped.replace("**", "").replace("__", "").replace("`", "")
        lines.append(stripped)
    result = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


async def gemini_text(prompt: str) -> str:
    resp = await asyncio.to_thread(model.generate_content, prompt + FORMAT_RULES)
    return clean_text(resp.text)


async def gemini_raw(prompt: str) -> str:
    resp = await asyncio.to_thread(model.generate_content, prompt)
    return resp.text


async def send_blocks(message, blocks, reply_markup=None, limit=3900):
    """Blokları Telegram limitinə görə mesajlara yığır, düymələr sonuncuda olur."""
    chunks, current = [], ""
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        while len(block) > limit:  # çox nadir: tək blok limiti keçirsə
            if current:
                chunks.append(current)
                current = ""
            chunks.append(block[:limit])
            block = block[limit:]
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)

    for idx, chunk in enumerate(chunks or [""]):
        markup = reply_markup if idx == len(chunks) - 1 else None
        await message.reply_text(chunk, reply_markup=markup)


async def send_long(message, text: str, reply_markup=None):
    await send_blocks(message, text.split("\n\n"), reply_markup)


# ---------------- Hesabat ----------------
def fmt(n) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except Exception:
        return str(n)


def short_caption(caption: str, n: int = 70) -> str:
    c = " ".join((caption or "").split())
    if not c:
        return "mətnsiz post"
    return c[:n] + ("…" if len(c) > n else "")


def pick_posts(posts):
    """Bəyənməyə görə ən yaxşı və ən zəif postların indekslərini seçir."""
    n = len(posts)
    order = sorted(range(n), key=lambda i: posts[i]["likes"], reverse=True)
    k = max(1, min(2, n // 2))
    best = order[:k]
    weak = [i for i in reversed(order) if i not in best][:k]
    return best, weak


def _clean_list(value, limit):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(i).strip() for i in value if str(i).strip()][:limit]


def _clean_entries(value):
    if not isinstance(value, list):
        return []
    out = []
    for e in value:
        if isinstance(e, dict):
            out.append(e)
    return out


def parse_report(text: str):
    """Gemini-nin JSON cavabını oxuyur. Alınmasa None qaytarır."""
    try:
        text = text.strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        data = json.loads(text[start : end + 1])
    except Exception:
        return None

    try:
        score = max(1, min(10, int(data.get("score", 0))))
    except Exception:
        score = 0

    report = {
        "niche": str(data.get("niche", "")).strip() or "Müəyyən edilmədi",
        "summary": str(data.get("summary", "")).strip(),
        "score": score,
        "best": _clean_entries(data.get("best")),
        "weak": _clean_entries(data.get("weak")),
        "audience": _clean_list(data.get("audience"), 4),
        "strategy": _clean_list(data.get("strategy"), 4),
        "tips": _clean_list(data.get("tips"), 5),
    }
    if not (report["best"] or report["weak"] or report["tips"]):
        return None
    return report


def find_entry(entries, post_no, fallback_idx):
    for e in entries:
        try:
            if int(e.get("post")) == post_no:
                return e
        except Exception:
            continue
    if fallback_idx < len(entries):
        return entries[fallback_idx]
    return {}


def post_header(icon, i, posts) -> str:
    p = posts[i]
    return (
        f"{icon} Post {i + 1} · ❤️ {fmt(p['likes'])} · 💬 {fmt(p['comments_count'])}\n"
        f"📝 «{short_caption(p['caption'])}»"
    )


def build_report(username, posts, report, best_idx, weak_idx):
    n = len(posts)
    likes = [p["likes"] for p in posts]
    comments = [p["comments_count"] for p in posts]

    score_line = ""
    if report["score"]:
        s = report["score"]
        score_line = f"⭐ Qiymət: {s}/10  {'▰' * s}{'▱' * (10 - s)}\n"

    blocks = [
        f"📊 @{username} · PROFİL ANALİZİ\n"
        f"{LINE}\n"
        f"🏷 Niş: {report['niche']}\n"
        f"{score_line}"
        f"{LINE}\n\n"
        f"❤️ Orta bəyənmə: {fmt(sum(likes) / n)}\n"
        f"💬 Orta şərh: {fmt(sum(comments) / n)}\n"
        f"📝 Analiz olunan post: {n}"
    ]

    if report["summary"]:
        blocks.append(f"📌 XÜLASƏ\n{report['summary']}")

    # --- Ən çox sevilən postlar ---
    medals = ["🥇", "🥈", "🥉"]
    entries = []
    for pos, i in enumerate(best_idx):
        e = find_entry(report["best"], i + 1, pos)
        lines = [post_header(medals[pos] if pos < 3 else "🏅", i, posts)]
        if e.get("why"):
            lines.append(f"✅ Niyə sevildi: {e['why']}")
        if e.get("repeat"):
            lines.append(f"🔁 Təkrar et: {e['repeat']}")
        entries.append("\n".join(lines))
    if entries:
        entries[0] = f"{LINE}\n🔥 ƏN ÇOX SEVİLƏN POSTLAR\n{LINE}\n\n" + entries[0]
        blocks.extend(entries)

    # --- Zəif postlar ---
    entries = []
    for pos, i in enumerate(weak_idx):
        e = find_entry(report["weak"], i + 1, pos)
        lines = [post_header("🔻", i, posts)]
        if e.get("why"):
            lines.append(f"⚠️ Niyə az sevildi: {e['why']}")
        if e.get("fix"):
            lines.append(f"🛠 Necə düzəltmək olar: {e['fix']}")
        entries.append("\n".join(lines))
    if entries:
        entries[0] = f"{LINE}\n📉 ƏN ZƏİF POSTLAR\n{LINE}\n\n" + entries[0]
        blocks.extend(entries)

    # --- Siyahı bölmələri ---
    for title, key in (
        ("💬 AUDİTORİYA REAKSİYASI", "audience"),
        ("📸 KONTENT STRATEGİYASI", "strategy"),
        ("🤖 KÖMƏKÇİ MƏSLƏHƏTİ", "tips"),
    ):
        items = report[key]
        if items:
            body = "\n".join(f"▫️ {i}" for i in items)
            blocks.append(f"{LINE}\n{title}\n{LINE}\n\n{body}")

    footer = "👇 Aşağıdakı düymələrlə davam edə bilərsən"
    author = author_line()
    blocks.append(f"{footer}\n{author}" if author else footer)
    return blocks


# ---------------- Düymələr ----------------
def tools_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🆚 Rəqiblə müqayisə", callback_data="compare")],
        [
            InlineKeyboardButton("📅 30 günlük plan", callback_data="plan"),
            InlineKeyboardButton("💡 Reels ideyaları", callback_data="ideas"),
        ],
    ]
    extra = []
    if AUTHOR_NAME or AUTHOR_TELEGRAM or AUTHOR_INSTAGRAM or CHANNEL_URL:
        extra.append(InlineKeyboardButton("👨‍💻 Bot müəllifi", callback_data="author"))
    if BOT_USERNAME:
        extra.append(InlineKeyboardButton("👥 Dostuna göndər", url=share_url()))
    if extra:
        rows.append(extra)
    rows.append([InlineKeyboardButton("🔄 Yeni profil analiz et", callback_data="new")])
    return InlineKeyboardMarkup(rows)


# ---------------- Promptlar ----------------
def analysis_prompt(username, posts, best_idx, weak_idx):
    numbered = [{"post": i + 1, **p} for i, p in enumerate(posts)]
    best_names = ", ".join(f"Post {i + 1}" for i in best_idx)
    weak_names = ", ".join(f"Post {i + 1}" for i in weak_idx) or "yoxdur"
    return (
        "Sən dost və köməkçi SMM asistanısan. İstifadəçiyə 'sən' deyə müraciət et, səmimi və konkret danış.\n"
        f"Aşağıda '@{username}' Instagram profilinin son {len(posts)} postunun məlumatları var:\n\n"
        f"{json.dumps(numbered, ensure_ascii=False)}\n\n"
        f"Ən çox bəyənilən postlar: {best_names}.\n"
        f"Ən zəif postlar: {weak_names}.\n\n"
        "Postların məzmunundan profilin nişini özün müəyyən et. "
        "Heç bir sahəni əvvəlcədən fərz etmə, yalnız verilən məlumatlara əsaslan. "
        "Səbəbləri izah edərkən caption-a, formata (video, foto, sitat), mövzuya və rəqəmlərə istinad et.\n\n"
        "Cavabı YALNIZ JSON formatında qaytar, başqa heç nə yazma:\n"
        "{\n"
        '  "niche": "profilin nişi, 2-4 söz",\n'
        '  "score": 1-dən 10-a qədər tam ədəd (ümumi performans),\n'
        '  "summary": "1-2 qısa cümləlik ümumi xülasə",\n'
        '  "best": [{"post": post nömrəsi, "why": "niyə çox sevildi, 1-2 qısa cümlə", '
        '"repeat": "bunu necə təkrarlamaq olar, 1 qısa cümlə"}],\n'
        '  "weak": [{"post": post nömrəsi, "why": "niyə az sevildi, 1-2 qısa cümlə", '
        '"fix": "necə düzəltmək olar, 1 qısa cümlə"}],\n'
        '  "audience": ["3 qısa bənd: auditoriya reaksiyası"],\n'
        '  "strategy": ["3 qısa bənd: kontent və format strategiyası"],\n'
        '  "tips": ["4 qısa bənd: köməkçi kimi birbaşa sənə müraciətlə konkret məsləhətlər"]\n'
        "}\n\n"
        "'best' massivində yalnız yuxarıdakı ən çox bəyənilən postlar, "
        "'weak' massivində yalnız ən zəif postlar olsun. "
        "Hər bənd maksimum 15 söz olsun. Mətndə markdown və emoji işlətmə."
    )


def plan_prompt(username, posts):
    return (
        "Sən dost və köməkçi Sosial Media Strategisən.\n"
        f"'@{username}' profilinin son postları:\n{json.dumps(posts, ensure_ascii=False)}\n\n"
        "Profilin nişini postlardan müəyyən et və hansı formatların daha yaxşı işlədiyini nəzərə alaraq "
        "30 günlük Instagram kontent planı hazırla.\n\n"
        "Format:\n"
        "📅 1-Cİ HƏFTƏ\n"
        "▫️ Bazar ertəsi • Reels • qısa mövzu\n"
        "▫️ Çərşənbə • Karusel • qısa mövzu\n"
        "▫️ Cümə • Foto • qısa mövzu\n\n"
        "4 həftə üçün eyni quruluşu işlət (hər həftə 3 paylaşım). "
        "Sonda '💡 3 ÜMUMİ MƏSLƏHƏT' bölməsi yaz."
    )


def ideas_prompt(username, posts):
    return (
        "Sən Reels və kontent ideyaları üzrə köməkçi mütəxəssissən.\n"
        f"'@{username}' profilinin son postları:\n{json.dumps(posts, ensure_ascii=False)}\n\n"
        "Profilin nişini postlardan müəyyən et və ona uyğun 6 Reels/post ideyası ver.\n\n"
        "Hər ideya bu formatda olsun:\n"
        "💡 İDEYA 1: qısa başlıq\n"
        "🎬 Konsept: bir cümlə\n"
        "🪝 Hook: ilk 3 saniyənin mətni\n\n"
        "İdeyalar arasında boş sətir burax. Sonda '#️⃣ HASHTAGLƏR' başlığı ilə 5 uyğun hashtag yaz."
    )


def compare_prompt(u1, p1, u2, p2):
    return (
        "Sən köməkçi Sosial Media Analitikisən. İki Instagram profilini müqayisə et.\n\n"
        f"Profil 1: @{u1}\nOrta göstəricilər: {json.dumps(post_stats(p1), ensure_ascii=False)}\n"
        f"Postlar: {json.dumps(p1, ensure_ascii=False)}\n\n"
        f"Profil 2: @{u2}\nOrta göstəricilər: {json.dumps(post_stats(p2), ensure_ascii=False)}\n"
        f"Postlar: {json.dumps(p2, ensure_ascii=False)}\n\n"
        "Bölmələr (hər bölmədə 2-3 qısa bənd):\n"
        "📊 ÜMUMİ MÜQAYİSƏ\n"
        "🏆 KİM ÖNDƏDİR VƏ NİYƏ\n"
        "📸 KONTENT FƏRQLƏRİ\n"
        f"💡 @{u1} RƏQİBDƏN NƏ ÖYRƏNƏ BİLƏR\n"
        "🎯 3 KONKRET ADDIM"
    )


# ---------------- Bot handlerləri ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if track_user(update.effective_user):
        await notify_admin(context, f"👤 Yeni istifadəçi: {who(update.effective_user)}")
    text = (
        "Salam! Mən AI Instagram Analitikiyəm. 📊\n"
        f"{LINE}\n\n"
        "Instagram profilinin istifadəçi adını (məsələn: sehife_adi) və ya linkini göndər.\n\n"
        "Mən sənə:\n"
        "▫️ Ən çox sevilən postların niyə sevildiyini\n"
        "▫️ Zəif postların niyə az sevildiyini\n"
        "▫️ Nəyi necə düzəltməyi\n"
        "izah edəcəyəm. Sonra rəqib müqayisəsi, kontent planı və Reels ideyaları da hazırlaya bilərəm!"
    )
    if author_line():
        text += f"\n\n{author_line()}"
    await update.message.reply_text(text)


async def stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID:
        return

    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    with _stats_lock:
        users = dict(STATS["users"])
        analyses = list(STATS["analyses"])

    def parse(t):
        return datetime.fromisoformat(t)

    day = [a for a in analyses if parse(a["t"]) >= day_ago]
    week = [a for a in analyses if parse(a["t"]) >= week_ago]
    new_week = [u for u in users.values() if parse(u["first_seen"]) >= week_ago]
    top = Counter(a["p"] for a in analyses).most_common(5)
    top_text = "\n".join(f"▫️ @{p} ({n})" for p, n in top) or "▫️ hələ yoxdur"

    await update.message.reply_text(
        "📈 STATİSTİKA\n"
        f"{LINE}\n\n"
        f"👥 Ümumi istifadəçi: {len(users)}\n"
        f"🆕 Son 7 gündə yeni: {len(new_week)}\n\n"
        f"📊 Ümumi analiz: {len(analyses)}\n"
        f"🕐 Son 24 saat: {len(day)} analiz, {len({a['u'] for a in day})} nəfər\n"
        f"📅 Son 7 gün: {len(week)} analiz, {len({a['u'] for a in week})} nəfər\n\n"
        f"🔝 Ən çox analiz edilən profillər:\n{top_text}"
    )


async def send_about(message):
    rows = author_link_buttons()
    await message.reply_text(about_text(), reply_markup=InlineKeyboardMarkup(rows) if rows else None)


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_about(update.message)


async def show_raw_error(message, res_data):
    preview = json.dumps(res_data, indent=2, ensure_ascii=False)[:1500]
    await message.reply_text(f"⚠️ API cavabından post tapılmadı:\n\n{preview}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if track_user(update.effective_user):
        await notify_admin(context, f"👤 Yeni istifadəçi: {who(update.effective_user)}")

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
            await send_long(
                update.message,
                f"🆚 @{main_user} vs @{username}\n{LINE}\n\n{text}",
                tools_keyboard(),
            )
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

        track_analysis(update.effective_user, username)
        await notify_admin(
            context, f"📊 Yeni analiz: {who(update.effective_user)} → @{username}"
        )

        best_idx, weak_idx = pick_posts(posts)
        raw = await gemini_raw(analysis_prompt(username, posts, best_idx, weak_idx))
        report = parse_report(raw)

        if report:
            blocks = build_report(username, posts, report, best_idx, weak_idx)
            await send_blocks(update.message, blocks, tools_keyboard())
        else:
            # JSON alınmasa, təmizlənmiş mətnlə göstər
            text = clean_text(raw)
            if author_line():
                text += f"\n\n{author_line()}"
            await send_long(update.message, text, tools_keyboard())

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

    if action == "author":
        await send_about(message)
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
            text = f"📅 30 GÜNLÜK KONTENT PLANI · @{username}\n{LINE}\n\n{text}"
        elif action == "ideas":
            await message.reply_text("💡 İdeyalar hazırlanır...")
            text = await gemini_text(ideas_prompt(username, posts))
            text = f"💡 REELS İDEYALARI · @{username}\n{LINE}\n\n{text}"
        else:
            return
        await send_long(message, text, tools_keyboard())
    except Exception as e:
        logging.exception("Düymə xətası")
        await message.reply_text(f"❌ Xəta baş verdi: {e}")


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()

    tg_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("haqqinda", about))
    tg_app.add_handler(CommandHandler("stat", stat))
    tg_app.add_handler(CallbackQueryHandler(handle_button))
    tg_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    tg_app.run_polling()
