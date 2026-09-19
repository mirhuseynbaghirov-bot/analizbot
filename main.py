import logging
import os
import instaloader
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Açar məlumatları Serverdən oxunur
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Gemini AI Konfiqurasiyası
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salam! Mən Sənin Ətir Mağazası üçün AI Sosial Media Analitikisən. 🧪\n\n"
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
        
    await update.message.reply_text(f"🔎 '@{username}' profilinin son postları çəkilir və AI analiz edir. Xahiş olunur 10-15 saniyə gözləyin...")
    
    try:
        L = instaloader.Instaloader()
        profile = instaloader.Profile.from_username(L.context, username)
        
        posts_data = []
        count = 0
        
        # Son 12 postu çəkirik
        for post in profile.get_posts():
            if count >= 12:
                break
            
            comments = []
            try:
                for comment in post.get_comments():
                    comments.append(comment.text)
                    if len(comments) >= 15:
                        break
            except Exception:
                pass

            posts_data.append({
                "caption": post.caption or "",
                "likes": post.likes,
                "comments_count": post.comments,
                "comments_text": comments
            })
            count += 1
        
        # AI Prompt
        prompt = f"""
        Sən təcrübəli Lüks Ətir Mağazası Biznes Konsultantı və Sosial Media Strategisən.
        Aşağıda Instagram profilinin son {len(posts_data)} postunun verillənləri var:
        
        Məlumatlar:
        {posts_data}
        
        Bu göstəriciləri əsas götürərək, mağaza sahibinə aydın və dəqiq hesabat hazırla:

        🔥 **1. TOP MƏHSUL (Ən Çox Bəyənilən Və İstenilən Ətir):**
        - Hansı ətir/post ən yüksək bəyənmə və şərh alıb?
        - *Məsləhət:* "Bu ətir çox bəyənilib, bunu daha çox paylaşın, ön plana çıxarın."

        📉 **2. ZƏİF PERFORMANSLI MƏHSUL:**
        - Hansı ətir/post az bəyənilib və ya diqqətdən kənarda qalıb?
        - *Məsləhət:* "Bu ətrin paylaşımını azaldın və ya təqdimat formasını dəyişin."

        💬 **3. MÜŞTƏRİ TƏLƏBİ VƏ ŞƏRH ANALİZİ:**
        - Şərhlərdə müştərilər ən çox hansı ətrin qiymətini, qalıcılığını və ya stokunu soruşur?

        📦 **4. STOK VƏ SİFARİŞ MƏSLƏHƏTİ:**
        - Tələbə əsasən təcili hansı ətrin stokunu artırmaq lazımdır?

        📸 **5. KONTENT VƏ FORMAT STRATEGİYASI:**
        - **Story:** Hansı ətirlər üçün gündəlik Story açılmalıdır?
        - **Foto (Carousel):** Hansı ətirləri estetik foto kimi paylaşmaq lazımdır?
        - **Video (Reels/TikTok):** Hansı ətir üçün konseptual Reels çəkilməlidir?

        💡 **6. XÜSUSİ BİZNES TÖVSİYƏLƏRİ:**
        - Şərhlərdəki müştəriləri DM-ə çəkmək və satışı bağlamaq üçün 1-2 taktika ver.
        """
        
        response = model.generate_content(prompt)
        await update.message.reply_text(response.text, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Xəta baş verdi: Profilin açıq (public) olduğuna əmin olun. Əlavə xəta: {e}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Bot aktivdir...")
    app.run_polling()
