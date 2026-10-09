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
ACTIVE_ANONYMOUS_CHATS = {}       
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
    else:
        return "International", "International 🌍"

def detect_user_gender(user) -> str:
    name = (user.first_name or "").lower()
    female_keywords = ["siti", "putri", "ayu", "dewi", "anisa", "zahra", "nur", "fitri", "indah", "nina", "vina", "dinda"]
    if any(k in name for k in female_keywords):
        return "Wanita 👩"
    return "Pria 👨"

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

def is_anonymeet_premium(user_id: int) -> bool:
    if is_permanent_premium(user_id):
        return True
    user = get_user_data(user_id)
    bonus_expire = user.get("anonymeet_premium_expire", 0)
    if time.time() < bonus_expire:
        return True
    return False

def admin_url() -> str:
    username = str(ADMIN_USERNAME).strip().lstrip('@').replace("https://t.me/", "")
    text_pesan = "Halo Admin, saya ingin membeli saldo atau paket Premium Anonymeet Pilihan Pasangan."
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
            "anonymeet_premium_expire": 0,
            "preferred_gender": "Semua",
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

    # Bersihkan sesi obrolan aktif saat /start ditekan agar tidak nyangkut
    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text="🔴 Pasangan Anda menutup percakapan."
                )
            except Exception:
                pass

    user = get_user_data(user_id)
    is_adm = is_admin(user_id)
    is_prem = is_anonymeet_premium(user_id)

    status_text = "👑 PREMIUM ANONYMEET" if is_prem else "👤 FREE (Random)"
    points = user.get('points', 50)
    saldo = user.get('saldo', 0)

    menu = [
        [InlineKeyboardButton("👥 Cari Pasangan (Anonymeet)", callback_data="anonymeet_start")],
        [InlineKeyboardButton("📞 Voice Random Call (Telepon Suara)", callback_data="voice_random_menu")],
        [InlineKeyboardButton("📍 Cari Pasangan Terdekat (GPS)", callback_data="anonymeet_nearby_menu")],
        [InlineKeyboardButton("💎 Paket Premium Pilihan Pasangan", callback_data="info_anonymeet_premium")],
        [InlineKeyboardButton("❤️ Reaksi Channel Telegram", callback_data="reaction_menu")],
        [InlineKeyboardButton("🎵 Buat Musik AI", callback_data="music"), InlineKeyboardButton("🎥 Buat Video AI (Engine)", callback_data="video_menu")],
        [InlineKeyboardButton("📥 Download Video", callback_data="download_zip_menu"), InlineKeyboardButton("🎧 Download MP3 HD", callback_data="mp3_download_menu")],
        [InlineKeyboardButton("🔍 Lacak ID Telegram", callback_data="menu_lacak_id"), InlineKeyboardButton("📱 Cek Nomor Telepon", callback_data="menu_get_phone")],
        [InlineKeyboardButton("🎁 Redeem Code", callback_data="redeem_menu"), InlineKeyboardButton("💡 Beri Saran Menu", callback_data="suggestion_menu")],
        [InlineKeyboardButton("💳 Cek Saldo & Poin", callback_data="saldo")],
        [InlineKeyboardButton("🆔 Cek ID Telegram", callback_data="check_id"), InlineKeyboardButton("💬 Hubungi Admin", url=admin_url())],
    ]
    if is_adm:
        menu.append([InlineKeyboardButton("👑 PANEL KONTROL ADMIN", callback_data="admin")])

    welcome_message = (
        "🔥 **WELCOME TO BYMODZ ANONYMEET & AI STUDIO** 🔥\n\n"
        "Cari teman ngobrol anonim, gunakan fitur AI canggih, unduhan pintar, dan komunitas interaktif dalam satu bot! 🚀\n\n"
        f"• Status: `{status_text}`\n"
        f"• Saldo: `Rp {saldo:,}`\n"
        f"• Poin: `🪙 {points}`\n\n"
        "💡 *Pilih menu di bawah atau klik tombol untuk mulai:*"
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
    user = get_user_data(user_id)
    is_prem = is_anonymeet_premium(user_id)
    pref_gender = user.get("preferred_gender", "Semua")

    is_banned_anon, sisa_detik = is_anonymeet_banned(user_id)
    if is_banned_anon:
        menit_sisa = int(sisa_detik // 60) + 1
        await chat_obj.reply_text(f"❌ **Akses Anonymeet Ditangguhkan!**\nAnda diblokir dari fitur ini selama {menit_sisa} menit lagi.")
        return

    global WAITING_ANONYMOUS_QUEUE
    WAITING_ANONYMOUS_QUEUE = [item for item in WAITING_ANONYMOUS_QUEUE if item["user_id"] != user_id]

    if WAITING_ANONYMOUS_QUEUE:
        partner_idx = -1
        for idx, queued in enumerate(WAITING_ANONYMOUS_QUEUE):
            if queued["user_id"] == user_id:
                continue
            if is_prem and pref_gender and pref_gender != "Semua":
                if queued["gender"] == pref_gender:
                    partner_idx = idx
                    break
            else:
                partner_idx = idx
                break

        if partner_idx != -1:
            matched_item = WAITING_ANONYMOUS_QUEUE.pop(partner_idx)
            partner_id = matched_item["user_id"]
            partner_gender = matched_item["gender"]
            partner_country = matched_item["country"]

            is_p_banned, _ = is_anonymeet_banned(partner_id)
            if is_p_banned:
                user_gender = detect_user_gender(update.effective_user)
                _, user_country = get_country_info(update.effective_user)
                WAITING_ANONYMOUS_QUEUE.append({"user_id": user_id, "gender": user_gender, "country": user_country, "pref": pref_gender})
                await chat_obj.reply_text("🚀 Sedang mencarikan pasangan baru yang sesuai...")
                return

            ACTIVE_ANONYMOUS_CHATS[user_id] = partner_id
            ACTIVE_ANONYMOUS_CHATS[partner_id] = user_id

            user_gender = detect_user_gender(update.effective_user)
            _, user_country = get_country_info(update.effective_user)

            admin_notice = GLOBAL_DB.get("admin_broadcast_text", "Selamat mengobrol!")

            match_text_1 = (
                f"✨ **Pasangan Anonim Ditemukan!** ✨\n\n"
                f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
                f"🔒 **Privasi Terjaga:** Tidak ada nama asli atau ID Telegram yang ditampilkan.\n"
                f"• Gender Partner:\n`{partner_gender}`\n\n"
                f"• Asal:\n`{partner_country}`\n\n"
                f"💬 Silakan kirim pesan.\n"
                f"• /next ➔ Langsung ganti pasangan baru\n"
                f"• /report ➔ Laporkan pelanggaran\n"
                f"• /stop ➔ Keluar obrolan"
            )

            match_text_2 = (
                f"✨ **Pasangan Anonim Ditemukan!** ✨\n\n"
                f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
                f"🔒 **Privasi Terjaga:** Tidak ada nama asli atau ID Telegram yang ditampilkan.\n"
                f"• Gender Partner:\n`{user_gender}`\n\n"
                f"• Asal:\n`{user_country}`\n\n"
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
            user_gender = detect_user_gender(update.effective_user)
            _, user_country = get_country_info(update.effective_user)
            WAITING_ANONYMOUS_QUEUE.append({"user_id": user_id, "gender": user_gender, "country": user_country, "pref": pref_gender})
            
            notif_mode = "🟢 Anda berada di **Anonymeet Premium** (Pilihan Gender Aktif)." if is_prem else "👤 Anda dalam mode **Free** (Pasangan Random Murni)."
            admin_notice = GLOBAL_DB.get("admin_broadcast_text", "Selamat mengobrol!")
            await chat_obj.reply_text(
                f"🚀 **Sedang mencari pasangan anonim...**\n\n"
                f"{notif_mode}\n\n"
                f"📢 **Info Admin:** *{admin_notice}*\n\n"
                "Mohon tunggu beberapa saat.",
                parse_mode="Markdown"
            )
    else:
        user_gender = detect_user_gender(update.effective_user)
        _, user_country = get_country_info(update.effective_user)
        WAITING_ANONYMOUS_QUEUE.append({"user_id": user_id, "gender": user_gender, "country": user_country, "pref": pref_gender})
        
        notif_mode = "🟢 Anda berada di **Anonymeet Premium**." if is_prem else "👤 Anda dalam mode **Free** (Pasangan Random Murni)."
        admin_notice = GLOBAL_DB.get("admin_broadcast_text", "Selamat mengobrol!")
        await chat_obj.reply_text(
            f"🚀 **Sedang mencari pasangan anonim...**\n\n"
            f"{notif_mode}\n\n"
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

    if data == "admin":
        if not is_admin(user_id):
            await query.answer("❌ Akses ditolak!", show_alert=True)
            return
        await query.answer()
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
            "• **Blokir User:** `/ban [ID]`\n"
            "• **Buka Blokir:** `/unban [ID]`",
            reply_markup=InlineKeyboardMarkup(kb_admin),
            parse_mode="Markdown"
        )
        return

    if data == "anonymeet_start":
        await query.answer()
        if user_id in ACTIVE_ANONYMOUS_CHATS:
            await query.message.reply_text("⚠️ Anda sedang terhubung dalam sesi obrolan! Ketik /stop untuk mengakhiri.")
            return
        
        is_prem = is_anonymeet_premium(user_id)
        if is_prem:
            await query.message.reply_text(
                "🟢 **ANDA BERADA DI ANONYMEET PREMIUM**\n\n"
                "Silakan pilih gender pasangan yang Anda inginkan untuk obrolan ini:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👨 Pria", callback_data="pref_pria"), InlineKeyboardButton("👩 Wanita", callback_data="pref_wanita")],
                    [InlineKeyboardButton("🌐 Bebas / Random", callback_data="pref_semua")]
                ]),
                parse_mode="Markdown"
            )
        else:
            user["preferred_gender"] = "Semua"
            await trigger_find_partner(update, context)
        return

    if data.startswith("pref_"):
        await query.answer()
        pref_map = {"pref_pria": "Pria 👨", "pref_wanita": "Wanita 👩", "pref_semua": "Semua"}
        user["preferred_gender"] = pref_map.get(data, "Semua")
        await query.message.edit_text(f"✅ Preferensi gender disimpan: **{user['preferred_gender']}**. Memulai pencarian...")
        await trigger_find_partner(update, context)
        return

    if data == "info_anonymeet_premium":
        await query.answer()
        await query.message.reply_text(
            "💎 **PAKET PREMIUM ANONYMEET (PILIHAN PASANGAN)**\n\n"
            "Ingin memilih gender pasangan sesuai keinginan (Pria / Wanita) saat mencari teman ngobrol? Upgrade ke akun Premium Anonymeet sekarang!\n\n"
            "📋 **Daftar Durasi & Harga:**\n"
            "• **7 Hari** ➔ Rp 5.000\n"
            "• **30 Hari (1 Bulan)** ➔ Rp 15.000\n"
            "• **90 Hari (3 Bulan)** ➔ Rp 35.000\n"
            "• **180 Hari (6 Bulan)** ➔ Rp 60.000\n"
            "• **365 Hari (1 Tahun)** ➔ Rp 100.000\n\n"
            "💬 *Silakan hubungi Admin untuk melakukan pembayaran saldo atau aktivasi paket:*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Hubungi Admin", url=admin_url())],
                [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == "voice_random_menu":
        await query.answer()
        clear_user_flow(user)
        webapp_url = "https://bot-telegram-nine-alpha.vercel.app/"
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
            "• Klik tombol **'📍 Bagikan Lokasi Saya Sekarang'** di bawah.",
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
            "Silakan masukkan atau ketik **Telegram ID** (angka) target:",
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
            "Klik tombol **'📱 Bagikan Nomor Telepon'** di bawah ini.",
            reply_markup=reply_markup
        )
        return

    if data == "reaction_menu":
        await query.answer()
        clear_user_flow(user)
        user["waiting_reaction_link"] = True
        await query.message.reply_text(
            "❤️ **REAKSI CHANNEL TELEGRAM**\n\n"
            "🔗 Silakan kirimkan **link postingan channel Telegram**:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Batal", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
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
            "Ketik saran atau menu tambahan:",
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
            "Masukkan kode redeem:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Batal", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == "video_menu":
        await query.answer()
        clear_user_flow(user)
        user["selected_video_engine"] = "gemini"
        user["waiting_custom_engine_prompt"] = True
        await query.message.reply_text(
            "🎥 **PEMBUATAN VIDEO AI**\n\n"
            "Silakan kirimkan ide / topik video:",
            parse_mode="Markdown"
        )
        return

    if data == "mp3_download_menu":
        await query.answer()
        clear_user_flow(user)
        user["waiting_mp3_link"] = True
        await query.message.reply_text(
            "🎧 **DOWNLOAD MP3 HD**\n\nKirimkan link musik:",
            parse_mode="Markdown"
        )
        return

    if data == "download_zip_menu":
        await query.answer()
        clear_user_flow(user)
        user["waiting_download_link"] = True
        await query.message.reply_text(
            "📥 **SMART CLOUD DOWNLOADER**\n\nKirimkan link video:",
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
            f"💳 **INFORMASI SALDO & AKUN**\n\n"
            f"• Status Anonymeet: `{'PREMIUM' if is_anonymeet_premium(user_id) else 'FREE'}`\n"
            f"• Saldo: Rp `{user['saldo']:,}`\n"
            f"• Poin: `🪙 {user.get('points', 0)}`",
            parse_mode="Markdown"
        )
        return

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
            "🔴 Percakapan anonim diakhiri.\nKetik /start untuk mencari pasangan baru.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 Cari Pasangan Lagi", callback_data="anonymeet_start")]
            ])
        )
    else:
        await update.message.reply_text("❌ Anda sedang tidak terhubung dalam percakapan aktif. Ketik /start untuk kembali ke menu utama.")

async def next_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text="🔄 Pasangan Anda beralih ke sesi lain (Next)."
                )
            except Exception:
                pass

    global WAITING_ANONYMOUS_QUEUE
    WAITING_ANONYMOUS_QUEUE = [item for item in WAITING_ANONYMOUS_QUEUE if item["user_id"] != user_id]

    await update.message.reply_text("🔄 **Mencari pasangan baru...** Mohon tunggu sebentar.")
    await trigger_find_partner(update, context)

async def report_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ACTIVE_ANONYMOUS_CHATS:
        await update.message.reply_text("❌ Anda sedang tidak berada dalam sesi percakapan anonim.")
        return

    partner_id = ACTIVE_ANONYMOUS_CHATS.get(user_id)
    if partner_id in ACTIVE_ANONYMOUS_CHATS:
        ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
    ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)

    await update.message.reply_text("✅ Laporan diterima. Sesi obrolan diakhiri.")
    try:
        await context.bot.send_message(chat_id=partner_id, text="⚠️ Partner melaporkan sesi ini. Sesi ditutup.")
    except Exception:
        pass

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
        p_lat, p_lon = waiting_item["lat"], waiting_item["lon"]
        dist = calculate_distance(lat, lon, p_lat, p_lon)
        if dist <= 30.0:
            matched_partner = WAITING_LOCATION_QUEUE.pop(idx)
            break

    if matched_partner:
        partner_id = matched_partner["user_id"]
        ACTIVE_ANONYMOUS_CHATS[user_id] = partner_id
        ACTIVE_ANONYMOUS_CHATS[partner_id] = user_id

        admin_notice = GLOBAL_DB.get("admin_broadcast_text", "Selamat mengobrol!")
        match_text = (
            f"✨ **Pasangan Terdekat (GPS) Ditemukan!** ✨\n\n"
            f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
            f"🔒 **Privasi Terjaga:** Lokasi tepat Anda disembunyikan.\n"
            f"💬 Silakan kirim pesan. Ketik /next, /report, atau /stop."
        )
        await update.message.reply_text(match_text, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=partner_id, text=match_text, parse_mode="Markdown")
        except Exception:
            pass
    else:
        WAITING_LOCATION_QUEUE.append({"user_id": user_id, "lat": lat, "lon": lon})
        await update.message.reply_text(
            "📍 **Lokasi Diterima!**\nSedang mencari pengguna terdekat di sekitar Anda...",
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

        lower_text = text.lower()
        if any(keyword in lower_text for keyword in STRICT_ADULT_KEYWORDS) and not is_admin(user_id):
            ban_user_anonymeet(user_id, hours=3)
            ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            await update.message.reply_text("❌ Terdeteksi kata terlarang / 18+. Akses diblokir 3 jam.")
            try:
                await context.bot.send_message(chat_id=partner_id, text="🔴 Sesi diakhiri karena partner melanggar aturan.")
            except Exception:
                pass
            return

        try:
            await context.bot.send_message(chat_id=partner_id, text=f"{text}")
        except Exception:
            await update.message.reply_text("❌ Gagal mengirim pesan ke partner.")
        return

    if user.get("waiting_telegram_id_input"):
        user.pop("waiting_telegram_id_input", None)
        if not text.isdigit():
            await update.message.reply_text("❌ ID Telegram harus berupa angka.")
            return
        profile_link = f"tg://user?id={text}"
        await update.message.reply_text(
            f"✅ **HASIL PELACAKAN PROFIL**\n• Target ID: `{text}`\n• Link: [Buka Profil]({profile_link})",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Buka Akun", url=profile_link)],
                [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if user.get("waiting_suggestion_input"):
        user.pop("waiting_suggestion_input", None)
        SUGGESTIONS_DB.append({"user_id": user_id, "text": text})
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
        user["anonymeet_premium_expire"] = max(time.time(), user.get("anonymeet_premium_expire", 0)) + (info["days"] * 86400)
        save_db(GLOBAL_DB)
        await update.message.reply_text(f"🎉 Berhasil redeem! Status Anonymeet Premium aktif selama {info['days']} hari.")
        return

    if user.get("waiting_custom_engine_prompt"):
        user.pop("waiting_custom_engine_prompt", None)
        await update.message.reply_text(f"✅ **HASIL SKRIP VIDEO AI:**\n\nKonsep untuk topik: `{text}`\n• Scene 1: Intro\n• Scene 2: Inti\n• Scene 3: Outro", parse_mode="Markdown")
        return

    if user.get("waiting_mp3_link") or user.get("waiting_download_link"):
        clear_user_flow(user)
        await update.message.reply_text("📥 Link berhasil diproses.")
        return

    await update.message.reply_text("Silakan gunakan tombol menu atau ketik /start.")

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        await update.message.reply_text(f"✅ Kontak diterima: +{update.message.contact.phone_number}", reply_markup=ReplyKeyboardRemove())

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
        await update.effective_message.reply_text(f"✅ Kode redeem berhasil dibuat: `{code}`", parse_mode="Markdown")
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

async def addsaldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 2: return
    try:
        t_id, amt = int(context.args[0]), int(context.args[1])
        get_user_data(t_id)["saldo"] += amt
        save_db(GLOBAL_DB)
        await update.effective_message.reply_text(f"✅ Berhasil menambah saldo Rp {amt:,} untuk ID `{t_id}`.", parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: {e}")

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Menu Utama"),
        BotCommand("menu", "Tampilkan Menu"),
        BotCommand("next", "Ganti pasangan baru"),
        BotCommand("stop", "Akhiri percakapan"),
        BotCommand("report", "Laporkan partner"),
        BotCommand("id", "Cek ID Telegram"),
    ])

def main() -> None:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("next", next_chat_cmd))
    app.add_handler(CommandHandler("stop", stop_chat_cmd))
    app.add_handler(CommandHandler("report", report_chat_cmd))
    app.add_handler(CommandHandler("id", check_id_cmd))
    app.add_handler(CommandHandler("addpoint", addpoint))
    app.add_handler(CommandHandler("addsaldo", addsaldo))
    app.add_handler(CommandHandler("createcode", createcode))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION | filters.Sticker.ALL, handle_message))

    print("🤖 BOT BERJALAN DENGAN PANEL ADMIN & PREMIUM ANONYMEET LENGKAP!")
    app.run_polling()

if __name__ == "__main__":
    main()
