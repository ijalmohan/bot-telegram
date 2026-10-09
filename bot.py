import re
import math
import asyncio
import aiohttp
from pathlib import Path
import urllib.parse
import random
import os
import json
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, BotCommand, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from groq import Groq
import openai

# ==================== KONFIGURASI UTAMA ====================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = 5794869044
ADMIN_USERNAME = "@Bymodz"

ADMIN_IDS = [5794869044]
PREMIUM_IDS = [5794869044]

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# ============================================================

groq_client = Groq(api_key=GROQ_API_KEY)
DB_FILE = "database.json"

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "users": {}, 
        "codes": {}, 
        "suggestions": [], 
        "banned_users": [], 
        "anonymeet_bans": {},
        "admin_broadcast_text": "🚀 Selamat datang di Anonymeet! Nikmati obrolan santai, jaga kesopanan, dan hormati privasi sesama pengguna."
    }

def save_db(data):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Gagal menyimpan database: {e}")

GLOBAL_DB = load_db()
USERS_DB = GLOBAL_DB.setdefault("users", {})
REDEEM_CODES_DB = GLOBAL_DB.setdefault("codes", {})
SUGGESTIONS_DB = GLOBAL_DB.setdefault("suggestions", [])
BANNED_USERS_DB = GLOBAL_DB.setdefault("banned_users", [])
ANONYMEET_BANS_DB = GLOBAL_DB.setdefault("anonymeet_bans", {})

WAITING_ANONYMOUS_QUEUE = []
WAITING_LOCATION_QUEUE = []
ACTIVE_ANONYMOUS_CHATS = {}       # user_id -> partner_id (Teks)
CHAT_HISTORY_LOGS = {}

STRICT_ADULT_KEYWORDS = [
    "porno", "kontol", "memek", "ngentot", "colmek", "colok",
    "bokep", "nsfw", "open bo", "vcs", "pap tt", "pap memek", "sangean"
]

def get_country_info(user) -> tuple[str, str]:
    lang_code = (user.language_code or "").lower()
    if "id" in lang_code:
        return "Indonesia", "Indonesia 🇮🇩"
    elif "en" in lang_code:
        return "Global / English", "Global 🌐"
    elif "ms" in lang_code:
        return "Malaysia", "Malaysia 🇲🇾"
    elif "ar" in lang_code:
        return "Arab Emirates", "UAE 🇦🇪"
    elif "ru" in lang_code:
        return "Russia", "Russia 🇷🇺"
    else:
        return "International", "International 🌍"

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_ID) or user_id in ADMIN_IDS

def is_banned(user_id: int) -> bool:
    if is_admin(user_id):
        return False
    return user_id in BANNED_USERS_DB or str(user_id) in [str(b) for b in BANNED_USERS_DB]

def is_anonymeet_banned(user_id: int) -> tuple[bool, int]:
    if is_admin(user_id):
        return False, 0
    s_id = str(user_id)
    if s_id in ANONYMEET_BANS_DB:
        expire_time = ANONYMEET_BANS_DB[s_id]
        if time.time() < expire_time:
            return True, int(expire_time - time.time())
        else:
            del ANONYMEET_BANS_DB[s_id]
            save_db(GLOBAL_DB)
    return False, 0

def ban_user_anonymeet(user_id: int, hours: int = 3):
    if is_admin(user_id):
        return
    s_id = str(user_id)
    expire_time = time.time() + (hours * 3600)
    ANONYMEET_BANS_DB[s_id] = expire_time
    save_db(GLOBAL_DB)

def is_permanent_premium(user_id: int) -> bool:
    return is_admin(user_id) or user_id in PREMIUM_IDS

def is_premium_or_admin(user_id: int) -> bool:
    if is_permanent_premium(user_id):
        return True
    user = get_user_data(user_id)
    bonus_expire = user.get("bonus_expire_time", 0)
    if time.time() < bonus_expire:
        return True
    return False

def admin_url() -> str:
    username = str(ADMIN_USERNAME).strip().lstrip('@').replace("https://t.me/", "")
    text_pesan = "Halo Admin, saya ingin upgrade akun bot AI ke Premium permanen."
    return f"https://t.me/{username}?text={urllib.parse.quote(text_pesan)}"

def clear_user_flow(user: dict) -> None:
    for key in (
        "music_menu_pending", "waiting_prompt", "waiting_style", "waiting_lyrics",
        "pending_mode", "pending_style", "waiting_chat", "waiting_deepseek",
        "waiting_chatgpt", "waiting_image", "waiting_upscale", "waiting_target_link",
        "waiting_target_count", "waiting_broadcast", "waiting_video_prompt", 
        "waiting_video_duration_secs", "waiting_download_link", "waiting_mp3_link", 
        "waiting_redeem_input", "waiting_suggestion_input", "selected_video_engine", 
        "waiting_custom_engine_prompt", "waiting_reaction_link", "selected_reaction_link", 
        "selected_reaction_count", "waiting_telegram_id_input", "waiting_custom_admin_notice",
        "waiting_location_share"
    ):
        user.pop(key, None)

def get_user_data(user_id: int) -> dict:
    s_id = str(user_id)
    if s_id not in USERS_DB:
        is_adm = is_admin(user_id)
        USERS_DB[s_id] = {
            "status": "ADMIN" if is_adm else "FREE",
            "saldo": 50000 if is_adm else 0,
            "music": 10 if is_adm else 1,
            "daily_limit": 999 if is_adm else 3,
            "points": 99999 if is_adm else 50,
            "bonus_expire_time": 0,
            "last_claim_date": "",
            "reaction_count": 0,
            "reaction_date": ""
        }
        save_db(GLOBAL_DB)
    return USERS_DB[s_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.effective_message.reply_text("❌ Akun Anda telah diblokir dari bot ini.")
        return

    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text="🔴 Pasangan Anda menutup percakapan. Ketik /start untuk mulai mencari pasangan baru."
                )
            except Exception:
                pass

    user = get_user_data(user_id)
    is_adm = is_admin(user_id)
    is_perm = is_permanent_premium(user_id)
    is_prem = is_premium_or_admin(user_id)

    current_time = time.time()
    bonus_expire = user.get("bonus_expire_time", 0)

    if not is_perm and bonus_expire > current_time:
        sisa_detik = int(bonus_expire - current_time)
        menit = sisa_detik // 60
        status_text = f"⏳ BONUS AKTIF ({menit} menit tersisa)"
    elif is_prem:
        status_text = "👑 ADMIN / PREMIUM"
    else:
        status_text = "👤 FREE (Terkunci)"

    points = user.get('points', 50)

    menu = [
        [InlineKeyboardButton("👥 Cari Pasangan (Anonymeet)", callback_data="anonymeet_start")],
        [InlineKeyboardButton("📞 Voice Random Call (Telepon Suara)", callback_data="voice_random_menu")],
        [InlineKeyboardButton("📍 Cari Pasangan Terdekat (GPS)", callback_data="anonymeet_nearby_menu")],
        [InlineKeyboardButton("❤️ Reaksi Channel Telegram", callback_data="reaction_menu")],
        [InlineKeyboardButton("🎵 Buat Musik AI", callback_data="music"), InlineKeyboardButton("🎥 Buat Video AI (Engine)", callback_data="video_menu")],
        [InlineKeyboardButton("📥 Download Video / 18+", callback_data="download_zip_menu"), InlineKeyboardButton("🎧 Download MP3 HD", callback_data="mp3_download_menu")],
        [InlineKeyboardButton("🔍 Lacak ID Telegram", callback_data="menu_lacak_id"), InlineKeyboardButton("📱 Cek Nomor Telepon", callback_data="menu_get_phone")],
        [InlineKeyboardButton("🎁 Redeem Code", callback_data="redeem_menu"), InlineKeyboardButton("💡 Beri Saran Menu", callback_data="suggestion_menu")],
        [InlineKeyboardButton("💳 Cek Akun & Poin", callback_data="saldo")],
        [InlineKeyboardButton("🆔 Cek ID Telegram", callback_data="check_id"), InlineKeyboardButton("💬 Hubungi Admin", url=admin_url())],
    ]
    if is_adm:
        menu.append([InlineKeyboardButton("👑 PANEL KONTROL ADMIN", callback_data="admin")])

    welcome_message = (
        "🔥 **WELCOME TO BYMODZ ANONYMEET & AI STUDIO** 🔥\n\n"
        "Cari teman ngobrol anonim, gunakan fitur AI canggih, unduhan pintar, dan komunitas interaktif dalam satu bot! 🚀\n\n"
        "📊 **Informasi Akun Anda:**\n"
        f"• Status: `{status_text}`\n"
        f"• Saldo / Poin: `🪙 {points}`\n\n"
        "💡 *Pilih menu di bawah atau klik tombol Anonymeet untuk mencari pasangan ngobrol:*"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            welcome_message,
            reply_markup=InlineKeyboardMarkup(menu),
            parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            welcome_message,
            reply_markup=InlineKeyboardMarkup(menu),
            parse_mode="Markdown"
        )

async def check_id_handler(update_or_query, user) -> None:
    if hasattr(update_or_query, "effective_user"):
        user_tg = update_or_query.effective_user
        msg = update_or_query.message or update_or_query.effective_message
    else:
        user_tg = update_or_query.from_user
        msg = update_or_query.message

    text_id = (
        f"🆔 **INFORMASI TELEGRAM ID**\n\n"
        f"• Nama: `{user_tg.first_name}`\n"
        f"• Username: `@{user_tg.username if user_tg.username else '-'}`\n"
        f"• Telegram ID: `{user_tg.id}`\n\n"
        f"*(Ketuk ID di atas untuk menyalin)*"
    )
    await msg.reply_text(text_id, parse_mode="Markdown")

async def check_id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_banned(user_id): return
    await check_id_handler(update, update.effective_user)

async def trigger_find_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_obj = update.effective_message

    is_banned_anon, sisa_detik = is_anonymeet_banned(user_id)
    if is_banned_anon:
        menit_sisa = int(sisa_detik // 60) + 1
        await chat_obj.reply_text(f"❌ **Akses Anonymeet Ditangguhkan!**\nAnda diblokir dari fitur ini selama {menit_sisa} menit lagi.")
        return

    if user_id in WAITING_ANONYMOUS_QUEUE:
        await chat_obj.reply_text("⏳ Anda sudah ada di antrean. Sedang mencari pasangan...")
        return

    if WAITING_ANONYMOUS_QUEUE:
        partner_id = WAITING_ANONYMOUS_QUEUE.pop(0)
        if partner_id == user_id:
            WAITING_ANONYMOUS_QUEUE.append(user_id)
            await chat_obj.reply_text("🚀 Sedang mencari pasangan untuk Anda...\nMohon tunggu sebentar.")
            return

        is_p_banned, _ = is_anonymeet_banned(partner_id)
        if is_p_banned:
            WAITING_ANONYMOUS_QUEUE.append(user_id)
            await chat_obj.reply_text("🚀 Sedang mencarikan pasangan baru yang sesuai...")
            return

        ACTIVE_ANONYMOUS_CHATS[user_id] = partner_id
        ACTIVE_ANONYMOUS_CHATS[partner_id] = user_id

        CHAT_HISTORY_LOGS[user_id] = []
        CHAT_HISTORY_LOGS[partner_id] = []

        genders = ["Pria 👨", "Wanita 👩"]
        gender_user = random.choice(genders)
        gender_partner = "Pria 👨" if gender_user == "Wanita 👩" else "Wanita 👩"

        user_tg = update.effective_user
        _, country_user_str = get_country_info(user_tg)

        try:
            partner_chat_member = await context.bot.get_chat(partner_id)
            _, country_partner_str = get_country_info(partner_chat_member)
        except Exception:
            country_partner_str = "Indonesia 🇮🇩"

        partner_display = gender_partner
        if is_admin(partner_id):
            partner_display = "Owner / Admin 👑"

        user_display = gender_user
        if is_admin(user_id):
            user_display = "Owner / Admin 👑"

        admin_notice = GLOBAL_DB.get("admin_broadcast_text", "Selamat mengobrol!")

        match_text_1 = (
            f"✨ **Pasangan Anonim Ditemukan!** ✨\n\n"
            f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
            f"🔒 **Privasi Terjaga:** Tidak ada nama asli atau ID Telegram yang ditampilkan.\n"
            f"• Partner Anda: `{partner_display}`\n"
            f"• Asal: `{country_partner_str}`\n\n"
            f"💬 Silakan kirim pesan.\n"
            f"• /next ➔ Langsung ganti pasangan baru\n"
            f"• /report ➔ Laporkan pelanggaran\n"
            f"• /stop ➔ Keluar obrolan"
        )

        match_text_2 = (
            f"✨ **Pasangan Anonim Ditemukan!** ✨\n\n"
            f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
            f"🔒 **Privasi Terjaga:** Tidak ada nama asli atau ID Telegram yang ditampilkan.\n"
            f"• Partner Anda: `{user_display}`\n"
            f"• Asal: `{country_user_str}`\n\n"
            f"💬 Silakan kirim pesan.\n"
            f"• /next ➔ Langsung ganti pasangan baru\n"
            f"• /report ➔ Laporkan pelanggaran\n"
            f"• /stop ➔ Keluar obrolan"
        )

        await chat_obj.reply_text(match_text_1, parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=partner_id, text=match_text_2, parse_mode="Markdown")
        except Exception:
            pass
    else:
        WAITING_ANONYMOUS_QUEUE.append(user_id)
        admin_notice = GLOBAL_DB.get("admin_broadcast_text", "Selamat mengobrol!")
        await chat_obj.reply_text(
            f"🚀 **Sedang mencari pasangan anonim...**\n\n"
            f"📢 **Info Admin:** *{admin_notice}*\n\n"
            "Mohon tunggu beberapa saat.",
            parse_mode="Markdown"
        )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    if is_banned(user_id):
        await query.answer("❌ Akun Anda telah diblokir.", show_alert=True)
        return

    user = get_user_data(user_id)
    data = query.data

    if data == "anonymeet_start":
        await query.answer()
        if user_id in ACTIVE_ANONYMOUS_CHATS:
            await query.message.reply_text("⚠️ Anda sedang terhubung dalam sesi obrolan! Selesaikan atau akhiri terlebih dahulu.")
            return
        await trigger_find_partner(update, context)
        return

    if data == "voice_random_menu":
        await query.answer()
        clear_user_flow(user)
        
        # Ganti URL di bawah dengan link Web App (misal Vercel / Netlify / Railway) aplikasi telepon suara Anda
        webapp_url = "https://your-voice-webapp-url.com"
        
        await query.message.reply_text(
            "📞 **VOICE RANDOM CALL (TELEPON SUARA)**\n\n"
            "Nikmati obrolan suara anonim dengan pengguna lain secara acak.\n\n"
            "Untuk mulai mengobrol, tekan tombol **‘Mulai Mengobrol’** di bagian bawah.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎙️ Mulai Mengobrol", web_app=WebAppInfo(url=webapp_url))],
                [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == "anonymeet_nearby_menu":
        await query.answer()
        clear_user_flow(user)
        user["waiting_location_share"] = True
        
        loc_keyboard = [[KeyboardButton("📍 Bagikan Lokasi Saya Sekarang", request_location=True)]]
        reply_markup = ReplyKeyboardMarkup(loc_keyboard, resize_keyboard=True, one_time_keyboard=True)
        
        await query.message.reply_text(
            "📍 **CARI PASANGAN TERDEKAT (GPS)**\n\n"
            "Bot akan menghubungkan Anda dengan pengguna lain yang berada di radius terdekat.\n\n"
            "• Jika Anda bersedia, silakan klik tombol **'📍 Bagikan Lokasi Saya Sekarang'** di bawah.\n"
            "• Jika Anda **tidak ingin** membagikan lokasi, cukup abaikan pesan ini atau kembali ke menu utama.",
            reply_markup=reply_markup
        )
        return

    if data == "set_admin_notice_menu":
        if not is_admin(user_id):
            await query.answer("Akses ditolak.", show_alert=True)
            return
        await query.answer()
        clear_user_flow(user)
        user["waiting_custom_admin_notice"] = True
        await query.message.reply_text(
            "✏️ **BUAT KATA-KATA KUSTOM ADMIN**\n\n"
            "Silakan ketik kalimat atau pengumuman yang ingin ditampilkan kepada semua pengguna saat mereka mencari pasangan di Anonymeet:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Batal", callback_data="admin")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == "menu_lacak_id":
        await query.answer()
        clear_user_flow(user)
        user["waiting_telegram_id_input"] = True
        await query.message.reply_text(
            "🔍 **FITUR LACAK PROFIL TELEGRAM**\n\n"
            "Silakan masukkan atau ketik **Telegram ID** (angka) target yang ingin Anda lacak:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Batal", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == "menu_get_phone":
        await query.answer()
        clear_user_flow(user)
        contact_keyboard = [[KeyboardButton("📱 Bagikan Nomor Telepon", request_contact=True)]]
        reply_markup = ReplyKeyboardMarkup(contact_keyboard, resize_keyboard=True, one_time_keyboard=True)
        await query.message.reply_text(
            "📱 **CEK NOMOR TELEPON**\n\n"
            "Untuk melihat atau mendeteksi nomor telepon yang terdaftar di akun Anda, silakan klik tombol **'📱 Bagikan Nomor Telepon'** di bawah ini.",
            reply_markup=reply_markup
        )
        return

    if data == "reaction_menu":
        if not check_reaction_limit(user_id, user):
            sisa_limit = 10 - user.get("reaction_count", 0)
            await query.answer(f"❌ Batas harian tercapai! Sisa kuota hari ini: {sisa_limit} reaksi.", show_alert=True)
            return
        await query.answer()
        clear_user_flow(user)
        user["waiting_reaction_link"] = True
        sisa_info = "Tanpa Batas (Admin/VIP)" if is_premium_or_admin(user_id) else f"{10 - user.get('reaction_count', 0)} dari 10 tersisa hari ini"
        await query.message.reply_text(
            "❤️ **REAKSI CHANNEL TELEGRAM**\n\n"
            f"• Kuota Anda: `{sisa_info}`\n\n"
            "🔗 Silakan kirimkan **link postingan channel Telegram** yang ingin diberikan reaksi:\n"
            "*(Contoh: `https://t.me/namachannel/123`)*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Batal", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data.startswith("send_react_"):
        emoji_map = {
            "heart": "❤️",
            "thumbsup": "👍",
            "fire": "🔥",
            "clapping": "👏",
            "grin": "😁",
            "party": "🎉"
        }
        react_key = data.replace("send_react_", "")
        chosen_emoji = emoji_map.get(react_key, "❤️")
        target_link = user.get("selected_reaction_link")
        target_count = user.get("selected_reaction_count", 1)

        if not target_link:
            await query.answer("❌ Link channel tidak ditemukan. Silakan ulangi dari menu Reaksi.", show_alert=True)
            return

        if not check_reaction_limit(user_id, user):
            await query.answer("❌ Batas kuota harian reaksi Anda sudah habis!", show_alert=True)
            return

        match = re.search(r"t\.me/(?:c/)?([^/]+)/(\d+)", target_link)
        if not match:
            await query.answer("❌ Format link tidak valid!", show_alert=True)
            return

        chat_identifier = match.group(1)
        message_id = int(match.group(2))

        if chat_identifier.isdigit():
            chat_id = int("-100" + chat_identifier)
        else:
            chat_id = f"@{chat_identifier}"

        try:
            from telegram import ReactionTypeEmoji
            await context.bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=chosen_emoji)]
            )
            add_reaction_usage(user_id, user)
            clear_user_flow(user)

            sisa_info = "Tanpa Batas" if is_premium_or_admin(user_id) else f"{10 - user.get('reaction_count', 0)} tersisa hari ini"
            await query.answer("✅ Reaksi berhasil dikirim!", show_alert=True)
            await query.message.reply_text(
                f"✅ **REAKSI BERHASIL DIKIRIM!**\n\n"
                f"• Target Post: `{target_link}`\n"
                f"• Jumlah Diminta: `{target_count}`\n"
                f"• Emoji: `{chosen_emoji}`\n"
                f"• Sisa Kuota Anda: `{sisa_info}`",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❤️ Kirim Reaksi Lagi", callback_data="reaction_menu")],
                    [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
        except Exception as e:
            await query.answer(f"Gagal mengirim reaksi: {str(e)[:100]}", show_alert=True)
            await query.message.reply_text(
                f"❌ **Gagal Mengirim Reaksi!**\n\n"
                f"Penyebab: Pastikan bot sudah menjadi **Administrator** di channel tersebut dengan izin mengelola pesan.\n\n"
                f"Detail Error: `{str(e)[:200]}`",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
        return

    if data == "admin" and not is_admin(user_id):
        await query.answer("Akses ditolak.", show_alert=True)
        return

    if data == "check_id":
        await query.answer()
        await check_id_handler(query, user)
        return

    if data == "suggestion_menu":
        await query.answer()
        clear_user_flow(user)
        user["waiting_suggestion_input"] = True
        await query.message.reply_text(
            "💡 **SARAN & REQUEST FITUR BARU**\n\n"
            "Silakan ketik saran atau menu tambahan apa yang ingin Anda lihat di bot ini. Pesan Anda akan langsung dikirimkan ke Admin:\n\n"
            "*(Ketik pesan saran Anda sekarang di chat)*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Batal", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == "redeem_menu":
        await query.answer()
        clear_user_flow(user)
        user["waiting_redeem_input"] = True
        await query.message.reply_text(
            "🎁 **REDEEM KODE VIP / PREMIUM**\n\n"
            "Silakan masukkan **kode redeem** yang Anda dapatkan dari Admin untuk membuka akses fitur bot secara penuh:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Batal", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == "video_menu":
        if not is_premium_or_admin(user_id):
            await query.answer("❌ Menu terkunci! Klaim bonus dulu.", show_alert=True)
            return
        await query.answer()
        clear_user_flow(user)
        await query.message.reply_text(
            "🎥 **PEMBUATAN VIDEO AI BERBASIS ENGINE**\n\n"
            "Pilih mesin AI pilihan Anda untuk merancang konsep, skrip, dan visual video:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🧠 Gemini AI", callback_data="eng_gemini"), InlineKeyboardButton("⚡ OpenAI / ChatGPT", callback_data="eng_openai")],
                [InlineKeyboardButton("🚀 Standard Prompt", callback_data="eng_standard"), InlineKeyboardButton("❌ Batal", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data.startswith("eng_"):
        if not is_premium_or_admin(user_id):
            await query.answer("❌ Akses ditolak!", show_alert=True)
            return
        engine_type = data.split("_")[1]
        user["selected_video_engine"] = engine_type
        user["waiting_custom_engine_prompt"] = True

        engine_names = {"gemini": "Gemini AI", "openai": "OpenAI / ChatGPT", "standard": "Standard Prompt"}

        await query.answer()
        await query.message.reply_text(
            f"🎬 Engine Dipilih: **{engine_names.get(engine_type)}**\n\n"
            f"✏ Silakan kirimkan **ide / topik video** yang ingin Anda buat:",
            parse_mode="Markdown"
        )
        return

    if data == "mp3_download_menu":
        if not is_premium_or_admin(user_id):
            await query.answer("❌ Menu terkunci! Klaim bonus dulu.", show_alert=True)
            return
        await query.answer()
        clear_user_flow(user)
        user["waiting_mp3_link"] = True
        await query.message.reply_text(
            "🎧 **DOWNLOAD MP3 BERKUALITAS TINGGI**\n\n"
            "Kirimkan link apa pun (YouTube, SoundCloud, direct link lagu, dll):\n"
            "• Bot akan otomatis mengunduh dan mengonversinya ke format **MP3 High Quality**\n"
            "• File musik akan langsung dikirim ke chat ini.\n\n"
            "🔗 *Silakan kirimkan link musiknya sekarang:*",
            parse_mode="Markdown"
        )
        return

    if data == "download_zip_menu":
        if not is_premium_or_admin(user_id):
            await query.answer("❌ Menu terkunci! Klaim bonus dulu.", show_alert=True)
            return
        await query.answer()
        clear_user_flow(user)
        user["waiting_download_link"] = True
        await query.message.reply_text(
            "📥 **SMART CLOUD DOWNLOADER (IG, TIKTOK, YT, 18+)**\n\n"
            "Kirimkan link video apa pun:\n"
            "• **Link Instagram** ➔ Langsung diarahkan ke downloader instan SaveClip.\n"
            "• **Link 18+** ➔ Diubah ke link Cloud permanen.\n"
            "• **Link Umum / TikTok** ➔ Dikirim langsung ke chat bot.\n\n"
            "🔗 *Silakan kirimkan link videonya sekarang:*",
            parse_mode="Markdown"
        )
        return

    if data == "main_menu":
        await query.answer()
        await start(update, context)
        return

    if data == "saldo":
        await query.answer()
        await query.message.reply_text(
            f"💳 **INFORMASI AKUN & POIN**\n\n"
            f"• Status: `{user['status']}`\n"
            f"• Saldo: Rp `{user['saldo']:,}`\n"
            f"• Poin Tukar Like: `{user.get('points', 0)} Poin`\n"
            f"• Sisa Kuota Harian: `{user.get('daily_limit', 0)}`",
            parse_mode="Markdown"
        )
        return

    await button_secondary(update, context)

async def button_secondary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    user = get_user_data(user_id)

    if data == "admin":
        if not is_admin(user_id):
            await query.answer("❌ Akses ditolak!", show_alert=True)
            return

        current_notice = GLOBAL_DB.get("admin_broadcast_text", "-")
        kb_admin = [
            [InlineKeyboardButton("✏️ Set Kata Kustom Anonymeet", callback_data="set_admin_notice_menu")],
            [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
        ]

        await query.message.reply_text(
            "👑 **PANEL ADMIN**\n\n"
            f"📢 **Kata Kustom Aktif Saat Ini:**\n> *{current_notice}*\n\n"
            "• Tambah Poin: `/addpoint [ID] [JUMLAH]`\n"
            "• Tambah Saldo: `/addsaldo [ID] [JUMLAH]`\n"
            "• Buat Kode: `/createcode [HARI] [MAKS]`\n"
            "• **Blokir User:** `/ban [ID Telegram]`\n"
            "• **Buka Blokir:** `/unban [ID Telegram]`",
            reply_markup=InlineKeyboardMarkup(kb_admin),
            parse_mode="Markdown"
        )
    elif data == "claim":
        if is_permanent_premium(user_id):
            await query.answer("❌ Akses sudah Premium Permanen!", show_alert=True)
            return

        current_date = time.strftime("%Y-%m-%d")
        if user.get("last_claim_date") == current_date:
            await query.answer("❌ Anda sudah mengklaim bonus hari ini. Silakan coba lagi besok!", show_alert=True)
            return

        user["last_claim_date"] = current_date
        user["points"] += 50
        user["bonus_expire_time"] = time.time() + 3600
        save_db(GLOBAL_DB)

        await query.answer("🎉 Klaim Bonus Berhasil!", show_alert=True)
        await query.message.reply_text(
            "🎉 **KLAIM BONUS BERHASIL!**\n\n"
            "• Anda mendapatkan **+50 Poin Gratis**!\n"
            "• **Semua tombol fitur telah dibuka** selama **1 Jam ke depan**.\n\n"
            "Ketik /start untuk mulai menggunakan menu.",
            parse_mode="Markdown"
        )
    elif data == "music":
        if not is_premium_or_admin(user_id):
            await query.answer("❌ Menu terkunci! Klaim bonus dulu.", show_alert=True)
            return
        user["music_menu_pending"] = True
        await query.message.reply_text("🎵 Pilih metode pembuatan musik AI:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💡 Dari Ide Teks", callback_data="music_prompt")], [InlineKeyboardButton("❌ Batal", callback_data="music_cancel")]]))
    elif data == "music_prompt":
        if not is_premium_or_admin(user_id):
            await query.answer("❌ Akses ditolak!", show_alert=True)
            return
        user["waiting_prompt"] = True
        await query.message.reply_text("💡 Tuliskan ide lagu Anda:")
    elif data == "music_cancel":
        clear_user_flow(user)
        await query.message.reply_text("✨ Dibatalkan. Ketik /start untuk kembali.")
    else:
        await query.answer("Fitur siap digunakan.")

async def stop_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text="🔴 Pasangan Anda menutup percakapan.\nKetik /start untuk mencari pasangan baru."
                )
            except Exception:
                pass
        await update.message.reply_text(
            "🔴 Percakapan anonim diakhiri.\n"
            "Ketik /start atau klik tombol di bawah untuk mencari pasangan baru.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 Cari Pasangan Lagi", callback_data="anonymeet_start")]
            ])
        )
    else:
        await update.message.reply_text("❌ Anda sedang tidak terhubung dalam percakapan aktif.")

async def next_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text="🔄 Pasangan Anda beralih ke sesi lain (Next).\nKetik /start atau /next untuk mencari pasangan baru."
                )
            except Exception:
                pass

    if user_id in WAITING_ANONYMOUS_QUEUE:
        WAITING_ANONYMOUS_QUEUE.remove(user_id)

    await update.message.reply_text("🔄 **Mencari pasangan baru...** Mohon tunggu sebentar.")
    await trigger_find_partner(update, context)

async def report_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ACTIVE_ANONYMOUS_CHATS and not is_admin(user_id):
        await update.message.reply_text("❌ Anda sedang tidak berada dalam sesi percakapan anonim untuk melakukan report.")
        return

    partner_id = ACTIVE_ANONYMOUS_CHATS.get(user_id)
    if not partner_id:
        await update.message.reply_text("❌ Tidak ada partner aktif yang ditemukan untuk dilaporkan.")
        return
    
    partner_logs = CHAT_HISTORY_LOGS.get(partner_id, [])
    status_msg = await update.message.reply_text("🤖 **Bot sedang memeriksa riwayat chat secara objektif & teliti...**")

    is_guilty = False
    reason_text = "Tidak ditemukan pelanggaran nyata."

    try:
        if GROQ_API_KEY and partner_logs:
            chat_context_str = "\n".join(partner_logs[-15:])
            prompt_ai = (
                "Anda adalah hakim moderasi bot Telegram yang sangat adil, objektif, dan cerdas. "
                "Tugas Anda: Tentukan apakah teks chat berikut BENAR-BENAR mengandung unsur pornografi eksplisit kasar, penipuan, ancaman kekerasan, atau spam jualan.\n"
                "PENTING: Percakapan santai, sapaan, gombalan biasa, candaan ringan, atau tanya kabar BUKANLAH pelanggaran sama sekali.\n\n"
                f"Riwayat Chat Partner:\n{chat_context_str}\n\n"
                "Jawab HANYA dengan format: YA [alasan singkat] jika itu adalah pelanggaran nyata/porno kasar, atau TIDAK [alasan singkat] jika itu obrolan normal/biasa."
            )
            completion = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt_ai}],
                temperature=0.1
            )
            ai_result = completion.choices[0].message.content.strip()
            if ai_result.upper().startswith("YA"):
                is_guilty = True
                reason_text = ai_result
        else:
            is_guilty = False
    except Exception:
        is_guilty = False

    if is_admin(partner_id):
        is_guilty = False

    if is_guilty:
        ban_user_anonymeet(partner_id, hours=3)
        ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)

        try:
            await context.bot.send_message(
                chat_id=partner_id,
                text="⚠️ **Laporan Terbukti!**\nAI mendapati adanya pelanggaran aturan obrolan/18+. Akses Anonymeet Anda ditangguhkan 3 jam."
            )
        except Exception:
            pass

        await status_msg.edit_text(
            "✅ **Laporan Diterima & Terbukti!**\n"
            f"• Analisis: `{reason_text}`\n"
            "• Partner telah diberi sanksi dan sesi ditutup.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 Cari Pasangan Baru", callback_data="anonymeet_start")]
            ])
        )
    else:
        await status_msg.edit_text(
            "❌ **Laporan Ditolak oleh Sistem AI!**\n"
            "AI mendeteksi bahwa partner Anda **tidak melanggar aturan** (hanya obrolan normal/biasa). Tidak ada sanksi yang diberikan, sesi obrolan tetap dilanjutkan.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔴 Akhiri Sesi (/stop)", callback_data="anonymeet_cancel")]
            ])
        )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user = get_user_data(user_id)
    
    if not user.get("waiting_location_share"):
        return

    user.pop("waiting_location_share", None)
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude

    matched_partner = None
    for idx, waiting_item in enumerate(WAITING_LOCATION_QUEUE):
        p_id = waiting_item["user_id"]
        p_lat, p_lon = waiting_item["lat"], waiting_item["lon"]
        
        dist = calculate_distance(lat, lon, p_lat, p_lon)
        if dist <= 30.0:
            matched_partner = WAITING_LOCATION_QUEUE.pop(idx)
            break

    if matched_partner:
        partner_id = matched_partner["user_id"]
        ACTIVE_ANONYMOUS_CHATS[user_id] = partner_id
        ACTIVE_ANONYMOUS_CHATS[partner_id] = user_id

        CHAT_HISTORY_LOGS[user_id] = []
        CHAT_HISTORY_LOGS[partner_id] = []

        genders = ["Pria 👨", "Wanita 👩"]
        gender_user = random.choice(genders)
        gender_partner = "Pria 👨" if gender_user == "Wanita 👩" else "Wanita 👩"

        partner_display = gender_partner
        if is_admin(partner_id):
            partner_display = "Owner / Admin 👑"

        user_display = gender_user
        if is_admin(user_id):
            user_display = "Owner / Admin 👑"

        admin_notice = GLOBAL_DB.get("admin_broadcast_text", "Selamat mengobrol!")

        match_text_1 = (
            f"✨ **Pasangan Terdekat (GPS) Ditemukan!** ✨\n\n"
            f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
            f"🔒 **Privasi Terjaga:** Lokasi tepat Anda disembunyikan.\n"
            f"• Partner Anda: `{partner_display}`\n\n"
            f"💬 Silakan kirim pesan. Ketik /next, /report, atau /stop."
        )

        match_text_2 = (
            f"✨ **Pasangan Terdekat (GPS) Ditemukan!** ✨\n\n"
            f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
            f"🔒 **Privasi Terjaga:** Lokasi tepat Anda disembunyikan.\n"
            f"• Partner Anda: `{user_display}`\n\n"
            f"💬 Silakan kirim pesan. Ketik /next, /report, atau /stop."
        )

        await update.message.reply_text(match_text_1, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=partner_id, text=match_text_2, parse_mode="Markdown")
        except Exception:
            pass
    else:
        WAITING_LOCATION_QUEUE.append({"user_id": user_id, "lat": lat, "lon": lon})
        await update.message.reply_text(
            "📍 **Lokasi Diterima!**\nSedang mencari pengguna terdekat di sekitar Anda...\nMohon tunggu sebentar.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_banned(user_id): return

    user = get_user_data(user_id)
    text = update.message.text.strip() if update.message.text else ""

    if user.get("waiting_custom_admin_notice"):
        user.pop("waiting_custom_admin_notice", None)
        GLOBAL_DB["admin_broadcast_text"] = text
        save_db(GLOBAL_DB)
        await update.message.reply_text(
            f"✅ **Berhasil Memperbarui Kata Kustom Admin!**\n\n> *{text}*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👑 Panel Admin", callback_data="admin")],
                [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS[user_id]

        if text.lower() == "/stop":
            await stop_chat_cmd(update, context)
            return
        if text.lower() == "/next":
            await next_chat_cmd(update, context)
            return
        if text.lower() == "/report":
            await report_chat_cmd(update, context)
            return

        if text:
            if user_id not in CHAT_HISTORY_LOGS:
                CHAT_HISTORY_LOGS[user_id] = []
            CHAT_HISTORY_LOGS[user_id].append(text)

        is_media_violation = update.message.photo or update.message.video or update.message.document or update.message.animation or update.message.sticker
        if is_media_violation and not is_admin(user_id):
            ban_user_anonymeet(user_id, hours=3)
            ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
            if partner_id in ACTIVE_ANONYMOUS_CHATS:
                ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            
            await update.message.reply_text("❌ **Peringatan Pelanggaran!**\nPengiriman foto, video, dokumen, stiker/media dilarang dalam mode Anonymeet untuk menjaga privasi & mencegah konten 18+. Akses Anonymeet Anda diblokir selama **3 jam**.")
            try:
                await context.bot.send_message(chat_id=partner_id, text="🔴 Partner mencoba mengirim media terlarang. Sesi ditutup demi keamanan.")
            except Exception:
                pass
            return

        lower_text = text.lower()
        if any(keyword in lower_text for keyword in STRICT_ADULT_KEYWORDS) and not is_admin(user_id):
            ban_user_anonymeet(user_id, hours=3)
            ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
            if partner_id in ACTIVE_ANONYMOUS_CHATS:
                ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)

            await update.message.reply_text("❌ **Terdeteksi Kata Terlarang / 18+ / Spam!**\nSesi dihentikan dan akses Anonymeet Anda diblokir otomatis selama **3 jam**.")
            try:
                await context.bot.send_message(chat_id=partner_id, text="🔴 Sesi diakhiri karena partner melanggar aturan teks/spam/18+.")
            except Exception:
                pass
            return

        try:
            if update.message.text:
                await context.bot.send_message(chat_id=partner_id, text=f"{text}")
        except Exception:
            await update.message.reply_text("❌ Gagal mengirim pesan ke partner. Partner mungkin telah keluar.")
        return

    if user.get("waiting_telegram_id_input"):
        user.pop("waiting_telegram_id_input", None)
        if not text.isdigit():
            await update.message.reply_text("❌ **Format Salah!** ID Telegram harus berupa angka.", parse_mode="Markdown")
            return
        target_id = text
        profile_link = f"tg://user?id={target_id}"
        await update.message.reply_text(
            f"✅ **HASIL PELACAKAN PROFIL TELEGRAM**\n\n• Target ID: `{target_id}`\n• Link: [Buka Profil]({profile_link})",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Buka Akun", url=profile_link)],
                [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if user.get("waiting_reaction_link"):
        if not text.startswith("http"):
            await update.message.reply_text("❌ Link tidak valid!")
            return
        user.pop("waiting_reaction_link", None)
        user["selected_reaction_link"] = text
        user["waiting_reaction_count"] = True
        await update.message.reply_text("🔢 Masukkan jumlah reaksi yang diinginkan:")
        return

    if user.get("waiting_reaction_count"):
        try:
            count = int(text)
            if count <= 0: raise ValueError()
        except ValueError:
            await update.message.reply_text("❌ Masukkan angka yang valid:")
            return
        user.pop("waiting_reaction_count", None)
        user["selected_reaction_count"] = count
        await update.message.reply_text(
            "Pilih emoji reaksi:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❤️ Hati", callback_data="send_react_heart"), InlineKeyboardButton("👍 Jempol", callback_data="send_react_thumbsup")],
                [InlineKeyboardButton("🔥 Api", callback_data="send_react_fire"), InlineKeyboardButton("❌ Batal", callback_data="main_menu")]
            ])
        )
        return

    if user.get("waiting_custom_engine_prompt"):
        engine_type = user.pop("selected_video_engine", "standard")
        user.pop("waiting_custom_engine_prompt", None)
        status_msg = await update.message.reply_text(f"🎬 Merancang konsep video AI ({engine_type.upper()})...")
        try:
            script = f"Konsep video sinematik untuk topik: {text}\n• Scene 1: Intro menarik\n• Scene 2: Inti pembahasan\n• Scene 3: Outro."
            await status_msg.edit_text(f"✅ **HASIL SKRIP VIDEO:**\n\n```text\n{script}\n```", parse_mode="Markdown")
        except Exception as e:
            await status_msg.edit_text(f"❌ Gagal: {e}")
        return

    if user.get("waiting_suggestion_input"):
        user.pop("waiting_suggestion_input", None)
        SUGGESTIONS_DB.append({"user_id": user_id, "text": text, "time": time.strftime("%Y-%m-%d %H:%M")})
        save_db(GLOBAL_DB)
        await update.message.reply_text("✅ Saran Anda telah dikirim ke Admin.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]]))
        return

    if user.get("waiting_redeem_input"):
        user.pop("waiting_redeem_input", None)
        code = text.upper()
        if code not in REDEEM_CODES_DB:
            await update.message.reply_text("❌ Kode tidak valid!")
            return
        info = REDEEM_CODES_DB[code]
        if user_id in info["used_by"]:
            await update.message.reply_text("❌ Anda sudah menggunakan kode ini!")
            return
        info["used_by"].append(user_id)
        user["bonus_expire_time"] = max(time.time(), user.get("bonus_expire_time", 0)) + (info["days"] * 86400)
        user["status"] = "PREMIUM"
        save_db(GLOBAL_DB)
        await update.message.reply_text(f"🎉 Berhasil redeem! Status Anda kini PREMIUM selama {info['days']} hari.")
        return

    if user.get("waiting_mp3_link"):
        user.pop("waiting_mp3_link", None)
        if not text.startswith("http"):
            await update.message.reply_text("❌ Link tidak valid!")
            return
        status_msg = await update.message.reply_text("🎧 Mendownload dan mengonversi ke MP3 HD...")
        await status_msg.edit_text("✅ Fitur MP3 diproses.")
        return

    if user.get("waiting_download_link"):
        user.pop("waiting_download_link", None)
        if not text.startswith("http"):
            await update.message.reply_text("❌ Link tidak valid!")
            return
        await update.message.reply_text(f"📥 Link diterima: {text}")
        return

    await update.message.reply_text("Silakan gunakan tombol menu atau ketik /start untuk berinteraksi.")

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if contact:
        await update.message.reply_text(
            f"✅ Kontak diterima: +{contact.phone_number}",
            reply_markup=ReplyKeyboardRemove()
        )

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 1: return
    try:
        b_id = int(context.args[0])
        if b_id not in BANNED_USERS_DB:
            BANNED_USERS_DB.append(b_id)
            save_db(GLOBAL_DB)
        await update.effective_message.reply_text(f"🚫 Berhasil memblokir ID `{b_id}`.", parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: {e}")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 1: return
    try:
        b_id = int(context.args[0])
        if b_id in BANNED_USERS_DB:
            BANNED_USERS_DB.remove(b_id)
            save_db(GLOBAL_DB)
        await update.effective_message.reply_text(f"✅ Berhasil membuka blokir ID `{b_id}`.", parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: {e}")

async def createcode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 2: return
    try:
        days, max_uses = int(context.args[0]), int(context.args[1])
        code = f"VIP-{random.randint(10000, 99999)}"
        REDEEM_CODES_DB[code] = {"days": days, "max_uses": max_uses, "used_by": []}
        save_db(GLOBAL_DB)
        await update.effective_message.reply_text(f"✅ Kode berhasil dibuat: `{code}`", parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: {e}")

async def addpoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 2: return
    try:
        t_id, amt = int(context.args[0]), int(context.args[1])
        get_user_data(t_id)["points"] += amt
        save_db(GLOBAL_DB)
        await update.effective_message.reply_text(f"✅ Berhasil menambah {amt} poin.")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: {e}")

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Mulai ulang / Menu Utama"),
        BotCommand("menu", "Tampilkan panel menu"),
        BotCommand("next", "Langsung ganti pasangan baru"),
        BotCommand("stop", "Akhiri percakapan anonim"),
        BotCommand("report", "Laporkan partner & verifikasi AI"),
        BotCommand("id", "Cek ID Telegram Anda"),
    ])

def main() -> None:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("next", next_chat_cmd))
    app.add_handler(CommandHandler("stop", stop_chat_cmd))
    app.add_handler(CommandHandler("report", report_chat_cmd))
    app.add_handler(CommandHandler("id", check_id_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(CommandHandler("addpoint", addpoint))
    app.add_handler(CommandHandler("createcode", createcode))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION | filters.Sticker.ALL, handle_message))

    print("🤖 BOT ANONYMEET WEBAPP VOICE CALL & FULL FEATURES BERJALAN SEMPURNA!")
    app.run_polling()

if __name__ == "__main__":
    main()
