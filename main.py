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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if "instagram.com" in text:
        username = text.split("instagram.com/")[-1].replace("/", "").split("?")[0].strip()
    else:
        username = text.replace("@", "").strip()
        
    await update.message.reply_text(f"🔎 '@{username}' profilinin məlumatları çəkilir və AI analiz edir. Xahiş olunur gözləyin...")
    
    try:
        # Stabil və yenilənmiş API endpointi
        url = f"https://instagram-scraper-stable-api.p.rapidapi.com/v1/posts?username_or_id_or_url={username}"
        
        headers = {
            'x-rapidapi-host': 'instagram-scraper-stable-api.p.rapidapi.com',
            'x-rapidapi-key': RAPIDAPI_KEY
        }

        api_response = requests.get(url, headers=headers)
        res_data = api_response.json()

        posts_data = []
        items = []

        if isinstance(res_data, dict):
            if "data" in res_data and isinstance(res_data["data"], list):
                items = res_data["data"]
            elif "items" in res_data and isinstance(res_data["items"], list):
                items = res_data["items"]
            elif "data" in res_data and isinstance(res_data["data"], dict):
                items = res_data["data"].get("items", [])

        for item in items[:6]:
            caption = ""
            if "caption" in item and isinstance(item["caption"], dict):
                caption = item["caption"].get("text", "")
            elif "caption" in item and isinstance(item["caption"], str):
                caption = item["caption"]

            likes = item.get("like_count", item.get("likes", 0))
            comments = item.get("comment_count", item.get("comments", 0))
            
            posts_data.append({
                "caption": caption,
                "likes": likes,
                "comments_count": comments
            })

        if not posts_data:
            preview = json.dumps(res_data, indent=2)[:1500]
            await update.message.reply_text(f"⚠️ Məlumat strukturu çəkilə bilmədi.\n\nAPI Cavabı:\n```json\n{preview}\n```", parse_mode='Markdown')
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
