import logging
import os
import threading
import requests
from flask import Flask
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# Render üçün mini Flask veb-server
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot aktivdir və işləyir!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Açar məlumatları
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")

# Gemini AI Konfiqurasiyası
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
    
    # Username çıxarırıq
    if "instagram.com" in text:
        username = text.split("instagram.com/")[-1].replace("/", "").split("?")[0].strip()
    else:
        username = text.replace("@", "").strip()
        
    await update.message.reply_text(f"🔎 '@{username}' profilinin məlumatları çəkilir və AI analiz edir. Xahiş olunur gözləyin...")
    
    try:
        # RapidAPI Instagram Scraper Sorğusu
        url = "https://instagram-scraper-stable-api.p.rapidapi.com/get_ig_user_posts_v2.php"
        
        payload = f"username_or_url={username}"
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'x-rapidapi-host': 'instagram-scraper-stable-api.p.rapidapi.com',
            'x-rapidapi-key': RAPIDAPI_KEY
        }

        api_response = requests.post(url, data=payload, headers=headers)
        res_data = api_response.json()

        posts_data = []
        
        # Məlumatları təmizləyib struktura salırıq
        items = res_data.get("data", {}).get("user", {}).get("edge_owner_to_timeline_media", {}).get("edges", [])
        
        for item in items[:6]:  # Son 6 post
            node = item.get("node", {})
            caption = ""
            if node.get("edge_media_to_caption", {}).get("edges"):
                caption = node["edge_media_to_caption"]["edges"][0]["node"].get("text", "")
                
            likes = node.get("edge_media_preview_like", {}).get("count", 0)
            comments = node.get("edge_media_to_comment", {}).get("count", 0)
            
            posts_data.append({
                "caption": caption,
                "likes": likes,
                "comments_count": comments
            })

        if not posts_data:
            await update.message.reply_text("❌ Məlumat tapılmadı. Profil adının düzgün və açıq (public) olduğuna əmin olun.")
            return

        # AI Prompt
        prompt = f"""
        Sən təcrübəli Lüks Ətir Mağazası Biznes Konsultantı və Sosial Media Strategisən.
        Aşağıda Instagram profilinin son {len(posts_data)} postunun məlumatları var:
        
        Məlumatlar:
        {posts_data}
        
        Bu göstəriciləri əsas götürərək, mağaza sahibinə aydın və dəqiq hesabat hazırla:

        🔥 1. TOP MƏHSUL (Ən Çox Bəyənilən Və İstənilən Ətir):
        - Hansı ətir/post ən yüksək bəyənmə və şərh alıb?
        - Məsləhət: Bu ətiri daha çox paylaşın, ön plana çıxarın.

        📉 2. ZƏİF PERFORMANSLI MƏHSUL:
        - Hansı ətir/post az bəyənilib və ya diqqətdən kənarda qalıb?
        - Məsləhət: Bu ətrin təqdimat formasını dəyişin.

        💬 3. MÜŞTƏRİ TƏLƏBİ VƏ İŞTİRAK ANALİZİ:
        - Postların açıqlamaları (caption) və bəyənmə/şərh nisbətinə əsasən müştərilərin diqqətini nə çəkir?

        📦 4. STOK VƏ SİFARİŞ MƏSLƏHƏTİ:
        - Tələbə əsasən təcili hansı ətrin stokunu artırmaq lazımdır?

        📸 5. KONTENT VƏ FORMAT STRATEGİYASI:
        - Story, Carousel və Reels (TikTok) üçün tövsiyələr ver.

        💡 6. XÜSUSİ BİZNES TÖVSİYƏLƏRİ:
        - Müştəriləri DM-ə çəkmək və satışı bağlamaq üçün 1-2 taktika ver.
        """
        
        ai_response = model.generate_content(prompt)
        await update.message.reply_text(ai_response.text)

    except Exception as e:
        await update.message.reply_text(f"❌ Xəta baş verdi: {e}")

if __name__ == '__main__':
    # Veb serveri arxa fonda başladırıq
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Telegram botunu başladırıq
    tg_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Bot aktivdir...")
    tg_app.run_polling()
