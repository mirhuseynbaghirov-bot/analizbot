import logging
import os
import threading
import requests
import json
from flask import Flask
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot aktivdir və işləyir!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salam! Mən Sənin Ətir Mağazası üçün AI Sosial Media Analitikisəm. 🧪\n\n"
        "Instagram profilinin istifadəçi adını (məsələn: sehife_adi) və ya linkini göndər.\n"
        "Mən son postları, bəyənmələri və şərhləri analiz edib sənə stok, reklam və kontent məsləhəti verim!"
    )

def extract_posts(data):
    """Müxtəlif API strukturlarından post siyahısını tapır"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # 1. Standart data -> user -> edges
        if "data" in data and isinstance(data["data"], dict):
            user = data["data"].get("user", {})
            if "edge_owner_to_timeline_media" in user:
                return user["edge_owner_to_timeline_media"].get("edges", [])
            if "posts" in user:
                return user.get("posts", [])
            if "items" in user:
                return user.get("items", [])
        
        # 2. Birbaşa açarlar
        for key in ["items", "edges", "posts", "data", "results"]:
            if key in data and isinstance(data[key], list):
                return data[key]
                
    return []

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if "instagram.com" in text:
        username = text.split("instagram.com/")[-1].replace("/", "").split("?")[0].strip()
    else:
        username = text.replace("@", "").strip()
        
    await update.message.reply_text(f"🔎 '@{username}' profilinin məlumatları çəkilir və AI analiz edir. Xahiş olunur gözləyin...")
    
    try:
        url = "https://instagram-scraper-stable-api.p.rapidapi.com/user_posts.php"
        querystring = {"username_or_id_or_url": username}
        
        headers = {
            'x-rapidapi-host': 'instagram-scraper-stable-api.p.rapidapi.com',
            'x-rapidapi-key': RAPIDAPI_KEY
        }

        api_response = requests.get(url, headers=headers, params=querystring)
        res_data = api_response.json()

        items = extract_posts(res_data)
        posts_data = []

        for item in items[:6]:
            node = item.get("node", item) if isinstance(item, dict) else {}
            
            caption = ""
            if "edge_media_to_caption" in node and node["edge_media_to_caption"].get("edges"):
                caption = node["edge_media_to_caption"]["edges"][0]["node"].get("text", "")
            elif "caption" in node:
                if isinstance(node["caption"], dict):
                    caption = node["caption"].get("text", "")
                elif isinstance(node["caption"], str):
                    caption = node["caption"]

            likes = node.get("edge_media_preview_like", {}).get("count", node.get("like_count", node.get("likes", 0)))
            comments = node.get("edge_media_to_comment", {}).get("count", node.get("comment_count", node.get("comments", 0)))
            
            posts_data.append({
                "caption": caption,
                "likes": likes,
                "comments_count": comments
            })

        if not posts_data:
            # Əgər yenə məlumat çıxara bilməsə, API-dən nə gəldiyini Telegram-a atır ki, strukturunu görək
            keys_info = list(res_data.keys()) if isinstance(res_data, dict) else str(type(res_data))
            preview = json.dumps(res_data, indent=2)[:1500]
            await update.message.reply_text(f"⚠️ Məlumat strukturu uyğun gəlmədi.\n\nAPI Açarları: `{keys_info}`\n\nGələn Cavab:\n```json\n{preview}\n```", parse_mode='Markdown')
            return

        prompt = (
            "Sən təcrübəli Lüks Ətir Mağazası Biznes Konsultantı və Sosial Media Strategisən.\n"
            f"Aşağıda Instagram profilinin son {len(posts_data)} postunun məlumatları var:\n\n"
            f"Məlumatlar:\n{posts_data}\n\n"
            "Bu göstəriciləri əsas götürərək, mağaza sahibinə aydın və dəqiq hesabat hazırla:\n\n"
            "🔥 1. TOP MƏHSUL (Ən Çox Bəyənilən Və İstənilən Ətir)\n"
            "📉 2. ZƏİF PERFORMANSLI MƏHSUL\n"
            "💬 3. MÜŞTƏRİ TƏLƏBİ VƏ İŞTİRAK ANALİZİ\n"
            "📦 4. STOK VƏ SİFARİŞ MƏSLƏHƏTİ\n"
            "📸 5. KONTENT VƏ FORMAT STRATEGİYASI\n"
            "💡 6. XÜSUSİ BİZNES TÖVSİYƏLƏRİ"
        )
        
        ai_response = model.generate_content(prompt)
        await update.message.reply_text(ai_response.text)

    except Exception as e:
        await update.message.reply_text(f"❌ Xəta baş verdi: {e}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    
    tg_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    tg_app.run_polling()
