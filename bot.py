import re
import asyncio
import aiohttp
from pathlib import Path
import urllib.parse
import random
import os
import json
import subprocess
import time
import receive_sms

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
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
TOKEN = "8737678492:AAFs_qlovfO11Ki-Acg6mzqk0A_PyBYvVLw"
ADMIN_ID = 5794869044
ADMIN_USERNAME = "@Bymodz"

ADMIN_IDS = [5794869044]
PREMIUM_IDS = [5794869044]

GROQ_API_KEY = "masuknanti"
OPENAI_API_KEY = "sk-proj-Ip0oybohVLVPADbdu0WUF72cDkSvId51000U7qc"
GEMINI_API_KEY = "masuknanti"
# ============================================================

groq_client = Groq(api_key=GROQ_API_KEY)
PUBLIC_NUMBERS = {"us": "12018577757", "uk": "447520635797", "se": "46769436266"}

DB_FILE = "database.json"

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}, "codes": {}, "suggestions": [], "banned_users": []}

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
CAMPAIGNS_DB = []
TEMP_EMAILS_DB = {}

# Variabel Global untuk Sistem Anonymeet / GhostChat
WAITING_ANONYMOUS_QUEUE = []
ACTIVE_ANONYMOUS_CHATS = {} # user_id -> partner_id

def is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_ID) or user_id in ADMIN_IDS

def is_banned(user_id: int) -> bool:
    return user_id in BANNED_USERS_DB or str(user_id) in [str(b) for b in BANNED_USERS_DB]

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
        "waiting_target_count", "waiting_broadcast", "creating_campaign_platform",
        "waiting_video_prompt", "waiting_video_duration_secs", "waiting_download_link",
        "waiting_mp3_link", "waiting_redeem_input", "waiting_suggestion_input",
        "selected_video_engine", "waiting_custom_engine_prompt",
        "waiting_reaction_link", "selected_reaction_link", "waiting_reaction_count",
        "selected_reaction_count", "waiting_reaction_emoji", "waiting_telegram_id_input"
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

def check_reaction_limit(user_id: int, user: dict) -> bool:
    if is_premium_or_admin(user_id):
        return True
    current_date = time.strftime("%Y-%m-%d")
    if user.get("reaction_date") != current_date:
        user["reaction_date"] = current_date
        user["reaction_count"] = 0
        save_db(GLOBAL_DB)
    return user.get("reaction_count", 0) < 10

def add_reaction_usage(user_id: int, user: dict):
    if not is_premium_or_admin(user_id):
        user["reaction_count"] = user.get("reaction_count", 0) + 1
        save_db(GLOBAL_DB)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.effective_message.reply_text("❌ Akun Anda telah diblokir dari bot ini.")
        return

    # Jika sedang dalam sesi anonymeet aktif, akhiri sesi saat ketik /start
    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text="🔴 Pasangan Anda menutup percakapan, ketik /start atau cari pasangan baru untuk memulai percakapan lain.\n\nApakah Anda ingin meningkatkan kualitas pasangan Anda dan mendukung bot ini? Lihat paket /vip 😊"
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

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    if is_banned(user_id):
        await query.answer("❌ Akun Anda telah diblokir.", show_alert=True)
        return

    user = get_user_data(user_id)
    data = query.data

    # Handler Fitur Anonymeet / GhostChat
    if data == "anonymeet_start":
        await query.answer()
        if user_id in ACTIVE_ANONYMOUS_CHATS:
            await query.message.reply_text("⚠️ Anda sedang terhubung dengan seseorang! Ketik `/stop` untuk mengakhiri percakapan terlebih dahulu.")
            return

        if user_id in WAITING_ANONYMOUS_QUEUE:
            await query.message.reply_text("⏳ Anda sudah ada di antrean. Sedang mencari pasangan...")
            return

        # Cek apakah ada orang lain di antrean
        if WAITING_ANONYMOUS_QUEUE:
            partner_id = WAITING_ANONYMOUS_QUEUE.pop(0)
            if partner_id == user_id:
                # Jaga-jaga jika ID sendiri
                WAITING_ANONYMOUS_QUEUE.append(user_id)
                await query.message.reply_text("🚀 Sedang mencari pasangan untuk Anda...\nMohon tunggu sebentar.")
                return

            # Hubungkan keduanya
            ACTIVE_ANONYMOUS_CHATS[user_id] = partner_id
            ACTIVE_ANONYMOUS_CHATS[partner_id] = user_id

            fake_id_1 = random.randint(100000000, 999999999)
            fake_id_2 = random.randint(100000000, 999999999)

            match_text_1 = (
                f"✨ **It's a match!** ✨\n\n"
                f"**Pasangan ditemukan:**\n"
                f"🔹 Ketertarikan: 🚀 Super\n"
                f"🔹 Jenis kelamin: ♀️ Wanita\n"
                f"🔹 Bahasa: 🇮🇩 Indonesia\n\n"
                f"💬 `#{fake_id_1}`\n"
                f"Ketik pesan Anda sekarang untuk mulai mengobrol secara anonim! (Ketik /stop untuk berhenti)"
            )

            match_text_2 = (
                f"✨ **It's a match!** ✨\n\n"
                f"**Pasangan ditemukan:**\n"
                f"🔹 Ketertarikan: 🚀 Super\n"
                f"🔹 Jenis kelamin: ♂️ Pria\n"
                f"🔹 Bahasa: 🇮🇩 Indonesia\n\n"
                f"💬 `#{fake_id_2}`\n"
                f"Ketik pesan Anda sekarang untuk mulai mengobrol secara anonim! (Ketik /stop untuk berhenti)"
            )

            await query.message.reply_text(match_text_1, parse_mode="Markdown")
            try:
                await context.bot.send_message(chat_id=partner_id, text=match_text_2, parse_mode="Markdown")
            except Exception:
                pass
        else:
            WAITING_ANONYMOUS_QUEUE.append(user_id)
            await query.message.reply_text(
                "🚀 **Sedang mencari pasangan untuk Anda...**\n"
                "Mohon tunggu beberapa saat hingga partner ditemukan.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Batalkan Pencarian", callback_data="anonymeet_cancel")]
                ]),
                parse_mode="Markdown"
            )
        return

    if data == "anonymeet_cancel":
        await query.answer("Pencarian dibatalkan.")
        if user_id in WAITING_ANONYMOUS_QUEUE:
            WAITING_ANONYMOUS_QUEUE.remove(user_id)
        await start(update, context)
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

    if data == "locked_feature":
        await query.answer(
            "❌ Tombol Terkunci!\nSilakan klik tombol 'Klaim Bonus' di menu utama untuk membuka semua fitur selama 1 jam.",
            show_alert=True
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

        kb_admin = []
        codes_list = ""
        for code, info in REDEEM_CODES_DB.items():
            codes_list += f"• `{code}` ({info['days']} Hari | Terpakai: {len(info['used_by'])}/{info['max_uses']})\n"
            kb_admin.append([InlineKeyboardButton(f"📢 Share Kode: {code}", callback_data=f"broadcast_code_{code}")])

        if not codes_list:
            codes_list = "Belum ada kode aktif."

        suggestions_text = ""
        if SUGGESTIONS_DB:
            for s in SUGGESTIONS_DB[-5:]:
                suggestions_text += f"• ID `{s['user_id']}`: {s['text']}\n"
        else:
            suggestions_text = "Belum ada saran dari pengguna."

        banned_list = ", ".join([str(b) for b in BANNED_USERS_DB]) if BANNED_USERS_DB else "Tidak ada."

        kb_admin.append([InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")])

        await query.message.reply_text(
            "👑 **PANEL ADMIN**\n\n"
            "• Tambah Poin: `/addpoint [ID] [JUMLAH]`\n"
            "• Tambah Saldo: `/addsaldo [ID] [JUMLAH]`\n"
            "• Buat Kode: `/createcode [HARI] [MAKS]`\n"
            "• **Blokir User:** `/ban [ID Telegram]`\n"
            "• **Buka Blokir:** `/unban [ID Telegram]`\n\n"
            f"📋 **Daftar Kode Aktif:**\n{codes_list}\n"
            f"🚫 **Daftar User Diblokir:** `{banned_list}`\n\n"
            f"💡 **Saran Terbaru:**\n{suggestions_text}",
            reply_markup=InlineKeyboardMarkup(kb_admin),
            parse_mode="Markdown"
        )
    elif data == "claim":
        if is_permanent_premium(user_id):
            await query.answer("❌ Akun Anda sudah Premium Permanen!", show_alert=True)
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
                    text="🔴 Pasangan Anda menutup percakapan, ketik /start untuk memulai percakapan lain.\n\nApakah Anda ingin meningkatkan kualitas pasangan Anda dan mendukung bot ini? Lihat paket /vip 😊"
                )
            except Exception:
                pass
        await update.message.reply_text(
            "🔴 Percakapan diakhiri.\n"
            "Ketik /start atau klik tombol di bawah untuk mencari pasangan baru.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 Cari Pasangan Lagi", callback_data="anonymeet_start")]
            ])
        )
    else:
        await update.message.reply_text("❌ Anda sedang tidak terhubung dalam percakapan anonim.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_banned(user_id): return

    user = get_user_data(user_id)
    text = update.message.text.strip() if update.message.text else ""

    # Cek apakah user sedang dalam sesi chat anonymeet aktif
    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS[user_id]
        if text.lower() == "/stop":
            await stop_chat_cmd(update, context)
            return
        try:
            # Meneruskan pesan ke partner secara anonim
            await context.bot.forward_message(chat_id=partner_id, from_chat_id=user_id, message_id=update.message.message_id)
        except Exception:
            await update.message.reply_text("❌ Gagal mengirim pesan ke partner. Partner mungkin telah keluar.")
        return

    # Pengecekan state ID Telegram
    if user.get("waiting_telegram_id_input"):
        user.pop("waiting_telegram_id_input", None)
        if not text.isdigit():
            await update.message.reply_text(
                "❌ **Format Salah!** ID Telegram harus berupa angka (contoh: `123456789`).\n"
                "Silakan masukkan ID yang valid:",
                parse_mode="Markdown"
            )
            return

        target_id = text
        profile_link = f"tg://user?id={target_id}"
        
        result_msg = (
            f"✅ **HASIL PELACAKAN PROFIL TELEGRAM**\n\n"
            f"• **Target ID:** `{target_id}`\n"
            f"• **Direct Mention / Tag:** [Buka Profil Langsung]({profile_link})\n\n"
            f"_Catatan: Telegram API membatasi akses nama/username publik jika target belum pernah berinteraksi dengan bot._"
        )
        
        await update.message.reply_text(
            result_msg,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Buka Akun (Direct)", url=profile_link)],
                [InlineKeyboardButton("🔍 Lacak ID Lain", callback_data="menu_lacak_id")],
                [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if user.get("waiting_reaction_link"):
        if not text.startswith("http://") and not text.startswith("https://"):
            await update.message.reply_text("❌ Link tidak valid! Pastikan diawali dengan http:// atau https://")
            return

        user.pop("waiting_reaction_link", None)
        user["selected_reaction_link"] = text
        user["waiting_reaction_count"] = True

        await update.message.reply_text(
            f"🔗 **Link Diterima:** `{text}`\n\n"
            "🔢 Silakan ketik **jumlah reaksi** yang diinginkan (contoh: `10`, `100`, `200`):",
            parse_mode="Markdown"
        )
        return

    if user.get("waiting_reaction_count"):
        try:
            count = int(text)
            if count <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("❌ Masukkan angka jumlah reaksi yang valid (contoh: 10, 100, 200):")
            return

        user.pop("waiting_reaction_count", None)
        user["selected_reaction_count"] = count

        await update.message.reply_text(
            f"🔢 **Jumlah Reaksi Diminta:** `{count}`\n\n"
            "Silakan pilih **emoticon reaksi** yang ingin dikirimkan:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❤️ Hati (Heart)", callback_data="send_react_heart"), InlineKeyboardButton("👍 Jempol (Thumbsup)", callback_data="send_react_thumbsup")],
                [InlineKeyboardButton("🔥 Api (Fire)", callback_data="send_react_fire"), InlineKeyboardButton("👏 Tepuk Tangan", callback_data="send_react_clapping")],
                [InlineKeyboardButton("😁 Senyum", callback_data="send_react_grin"), InlineKeyboardButton("🎉 Pesta (Party)", callback_data="send_react_party")],
                [InlineKeyboardButton("❌ Batal", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if user.get("waiting_custom_engine_prompt"):
        engine_type = user.pop("selected_video_engine", "standard")
        user.pop("waiting_custom_engine_prompt", None)

        status_msg = await update.message.reply_text(
            f"🎬 **Merancang Konsep Video AI ({engine_type.upper()})...**\n\n"
            f"• Topik: `{text}`\n\n"
            "⏳ Sedang memproses skrip dan storyboard sinematik..."
        )

        try:
            generated_script = ""
            if engine_type == "gemini":
                gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
                payload = {
                    "contents": [{
                        "parts": [{"text": f"Bertindaklah sebagai sutradara AI profesional. Buatkan konsep video, judul adegan, dan narasi sinematik dalam bahasa Indonesia untuk topik berikut: {text}"}]
                    }]
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(gemini_url, json=payload) as resp:
                        res_json = await resp.json()
                        if resp.status == 200:
                            generated_script = res_json["candidates"][0]["content"]["parts"][0]["text"]
                        else:
                            raise Exception(res_json.get("error", {}).get("message", f"Gagal menghubungi API Gemini (Status {resp.status})"))
            elif engine_type == "openai":
                client_openai = openai.OpenAI(api_key=OPENAI_API_KEY)
                response = client_openai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "Kamu adalah penulis skrip dan pembuat konsep video kreatif profesional."},
                        {"role": "user", "content": f"Buatkan skrip video sinematik dan panduan visual untuk topik berikut: {text}"}
                    ]
                )
                generated_script = response.choices[0].message.content
            else:
                generated_script = f"🎬 **Konsep Video Standar**\n\nTopik: {text}\n• Adegan 1: Pembuka sinematik menarik.\n• Adegan 2: Penjelasan inti materi.\n• Adegan 3: Penutup & Call to Action."

            await status_msg.edit_text(
                f"✅ **HASIL SKRIP & STORYBOARD VIDEO ({engine_type.upper()})**\n\n"
                f"```text\n{generated_script[:3500]}\n```",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎥 Buat Video Lain", callback_data="video_menu")],
                    [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
        except Exception as e:
            await status_msg.edit_text(f"❌ Gagal memproses video AI: {str(e)[:300]}")
        return

    if user.get("waiting_suggestion_input"):
        user.pop("waiting_suggestion_input", None)

        suggestion_entry = {
            "user_id": user_id,
            "username": update.effective_user.username or "-",
            "text": text,
            "time": time.strftime("%Y-%m-%d %H:%M")
        }
        SUGGESTIONS_DB.append(suggestion_entry)
        save_db(GLOBAL_DB)

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"💡 **SARAN / REQUEST MENU BARU!**\n\n"
                     f"• Dari User ID: `{user_id}`\n"
                     f"• Username: `@{update.effective_user.username or '-'}`\n"
                     f"• Isi Saran:\n> {text}",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        await update.message.reply_text(
            "✅ **Terima Kasih!**\nSaran dan masukan menu Anda telah berhasil dikirimkan ke Admin.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if user.get("waiting_redeem_input"):
        user.pop("waiting_redeem_input", None)
        code_input = text.upper()

        if code_input not in REDEEM_CODES_DB:
            await update.message.reply_text("❌ **Kode Redeem Tidak Valid!**\nPastikan kode yang Anda masukkan benar.", parse_mode="Markdown")
            return

        code_info = REDEEM_CODES_DB[code_input]

        if user_id in code_info["used_by"]:
            await update.message.reply_text("❌ **Gagal!** Anda sudah pernah menggunakan kode redeem ini sebelumnya.", parse_mode="Markdown")
            return

        if len(code_info["used_by"]) >= code_info["max_uses"]:
            await update.message.reply_text("❌ **Kode Redeem Kedaluwarsa!**\nKuota maksimal penggunaan ID untuk kode ini sudah habis.", parse_mode="Markdown")
            return

        code_info["used_by"].append(user_id)
        days = code_info["days"]
        expire_seconds = days * 24 * 3600
        current_time = time.time()

        existing_expire = user.get("bonus_expire_time", 0)
        base_time = max(current_time, existing_expire)
        user["bonus_expire_time"] = base_time + expire_seconds
        user["status"] = "PREMIUM"

        save_db(GLOBAL_DB)

        await update.message.reply_text(
            f"🎉 **REDEEM KODE BERHASIL!**\n\n"
            f"• Selamat! Akun Anda kini berstatus **PREMIUM** selama **{days} Hari**.\n"
            f"• Semua fitur bot telah terbuka penuh.",
            parse_mode="Markdown"
        )
        return

    if user.get("waiting_mp3_link"):
        user.pop("waiting_mp3_link", None)

        if not text.startswith("http://") and not text.startswith("https://"):
            await update.message.reply_text("❌ Link tidak valid! Pastikan diawali dengan http:// atau https://")
            return

        if not is_premium_or_admin(user_id):
            await update.message.reply_text("❌ Akses ditolak! Klaim bonus terlebih dahulu.")
            return

        status_msg = await update.message.reply_text(
            "🎧 **Mendownload & Mengonversi ke MP3 HD...**\n\n"
            f"• Link: `{text}`\n\n"
            "⏳ Sedang memproses audio berkualitas tinggi..."
        )

        output_template = f"audio_{user_id}_{random.randint(100,999)}.%(ext)s"
        downloaded_file = None

        try:
            ydl_cmd = [
                "yt-dlp",
                "--no-check-certificates",
                "--geo-bypass",
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "-o", output_template,
                text
            ]

            process = await asyncio.create_subprocess_exec(
                *ydl_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise Exception(stderr.decode(errors='ignore')[:300])

            for f in os.listdir("."):
                if f.startswith(f"audio_{user_id}_") and f.endswith(".mp3"):
                    downloaded_file = f
                    break

            if not downloaded_file or not os.path.exists(downloaded_file):
                raise Exception("File MP3 hasil konversi tidak ditemukan.")

            file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)

            await status_msg.edit_text(
                f"📤 **Konversi Selesai ({file_size_mb:.2f} MB)!**\n\n"
                "⏳ Mengirimkan file audio MP3 ke chat..."
            )

            with open(downloaded_file, "rb") as audio_file:
                await context.bot.send_audio(
                    chat_id=user_id,
                    audio=audio_file,
                    caption=(
                        f"✅ **Audio MP3 HD Berhasil Dikirim!**\n\n"
                        f"• Ukuran File: `{file_size_mb:.2f} MB`\n"
                        f"• Kualitas: **High Quality (HQ 320kbps)**"
                    ),
                    parse_mode="Markdown",
                    read_timeout=900,
                    write_timeout=900,
                    connect_timeout=60
                )

            await status_msg.delete()
        except Exception as e:
            await status_msg.edit_text(f"❌ Gagal memproses MP3: {str(e)[:300]}")
        finally:
            if downloaded_file and os.path.exists(downloaded_file):
                os.remove(downloaded_file)
        return

    if user.get("waiting_download_link"):
        user.pop("waiting_download_link", None)

        if not text.startswith("http://") and not text.startswith("https://"):
            await update.message.reply_text("❌ Link yang Anda masukkan tidak valid. Pastikan diawali dengan http:// atau https://")
            return

        if not is_premium_or_admin(user_id):
            await update.message.reply_text("❌ Akses ditolak! Klaim bonus terlebih dahulu.")
            return

        if "instagram.com" in text.lower() or "instagr.am" in text.lower():
            encoded_url = urllib.parse.quote(text, safe='')
            saveclip_url = f"https://saveclip.app/id9/instagram-reels-video-download?url={encoded_url}"
            await update.message.reply_text(
                f"📸 **LINK INSTAGRAM TERDETEKSI**\n\n"
                f"• Link: `{text}`\n\n"
                f"Silakan klik tombol di bawah untuk mendownload video Instagram melalui SaveClip secara instan:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 Download Video Instagram di SaveClip", url=saveclip_url)],
                    [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
            return

        adult_keywords = ["porn", "porndude", "xvideos", "xnxx", "redtube", "brazzers", "adult", "sex", "hentai", "nsfw"]
        is_adult_link = any(keyword in text.lower() for keyword in adult_keywords)

        if is_adult_link:
            status_msg = await update.message.reply_text(
                "🔥 **Mendownload Video 18+ ke Cloud Server...**\n\n"
                f"• Link: `{text}`\n\n"
                "⏳ Sedang mengunduh file video agar terhindar dari error 403..."
            )

            output_template = f"adult_video_{user_id}_{random.randint(100,999)}.%(ext)s"
            downloaded_file = None

            try:
                ydl_cmd = [
                    "yt-dlp",
                    "--no-check-certificates",
                    "--geo-bypass",
                    "--age-limit", "30",
                    "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "-o", output_template,
                    text
                ]

                process = await asyncio.create_subprocess_exec(
                    *ydl_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()

                if process.returncode != 0:
                    raise Exception(stderr.decode(errors='ignore')[:300])

                for f in os.listdir("."):
                    if f.startswith(f"adult_video_{user_id}_") and not f.endswith(".part"):
                        downloaded_file = f
                        break

                if not downloaded_file or not os.path.exists(downloaded_file):
                    raise Exception("File video hasil unduhan tidak ditemukan.")

                file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)

                await status_msg.edit_text(
                    f"☁️ **Mengunggah ke Cloud Permanen ({file_size_mb:.2f} MB)...**\n\n"
                    "⏳ Membuat tautan unduhan instan..."
                )

                upload_cmd = [
                    "curl", "-s", "-F", "reqtype=fileupload",
                    "-F", f"fileToUpload=@{downloaded_file}",
                    "https://catbox.moe/user/api.php"
                ]

                up_process = await asyncio.create_subprocess_exec(
                    *upload_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                up_stdout, up_stderr = await up_process.communicate()
                cloud_link = up_stdout.decode().strip()

                if not cloud_link.startswith("http"):
                    raise Exception("Gagal mengunggah file ke server cloud.")

                await status_msg.delete()

                await update.message.reply_text(
                    f"✅ **LINK CLOUD 18+ SIAP DIBUKA!**\n\n"
                    f"🔗 `{cloud_link}`\n\n"
                    f"*(Klik tombol di bawah untuk mendownload atau memutar video dengan lancar di Browser / Chrome)*",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🌐 Buka / Download di Browser", url=cloud_link)],
                        [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
                    ]),
                    parse_mode="Markdown"
                )

            except Exception as e:
                await status_msg.edit_text(f"❌ Gagal memproses video 18+: {str(e)[:300]}")
            finally:
                if downloaded_file and os.path.exists(downloaded_file):
                    os.remove(downloaded_file)

        else:
            status_msg = await update.message.reply_text(
                "📥 **Mendeteksi Link Video Umum / TikTok...**\n\n"
                f"• Link: `{text}`\n\n"
                "⏳ Sedang mendownload video ke server bot..."
            )

            output_template = f"downloaded_video_{user_id}_{random.randint(100,999)}.%(ext)s"
            downloaded_file = None

            try:
                ydl_cmd = [
                    "yt-dlp",
                    "--no-check-certificates",
                    "--geo-bypass",
                    "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "-o", output_template,
                    text
                ]

                process = await asyncio.create_subprocess_exec(
                    *ydl_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()

                if process.returncode != 0:
                    raise Exception(stderr.decode(errors='ignore')[:300])

                for f in os.listdir("."):
                    if f.startswith(f"downloaded_video_{user_id}_") and not f.endswith(".part"):
                        downloaded_file = f
                        break

                if not downloaded_file or not os.path.exists(downloaded_file):
                    raise Exception("File video hasil unduhan tidak ditemukan.")

                file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)

                await status_msg.edit_text(
                    f"📤 **Video Selesai ({file_size_mb:.2f} MB)!**\n\n"
                    "⏳ Mengirimkan file video langsung ke chat..."
                )

                with open(downloaded_file, "rb") as vid_file:
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=vid_file,
                        caption=(
                            f"✅ **Video Berhasil Dikirim!**\n\n"
                            f"• Ukuran File: `{file_size_mb:.2f} MB`\n"
                            f"• Status: **Siap Ditonton**"
                        ),
                        parse_mode="Markdown",
                        read_timeout=900,
                        write_timeout=900,
                        connect_timeout=60
                    )

                await status_msg.delete()
            except Exception as e:
                await status_msg.edit_text(f"❌ Terjadi kesalahan: {str(e)[:300]}")
            finally:
                if downloaded_file and os.path.exists(downloaded_file):
                    os.remove(downloaded_file)
        return

    await update.message.reply_text("Silakan gunakan tombol menu atau ketik /start untuk berinteraksi.")

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if contact:
        phone_number = contact.phone_number
        first_name = contact.first_name
        user_id = contact.user_id
        
        await update.message.reply_text(
            f"✅ **DATA KONTAK BERHASIL DITERIMA!**\n\n"
            f"• **Nama:** {first_name}\n"
            f"• **User ID:** `{user_id}`\n"
            f"• **Nomor Telepon:** `+{phone_number}`",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Format Admin: `/ban [ID_TELEGRAM]`", parse_mode="Markdown")
        return
    try:
        b_id = int(context.args[0])
        if b_id not in BANNED_USERS_DB:
            BANNED_USERS_DB.append(b_id)
            save_db(GLOBAL_DB)
        await update.effective_message.reply_text(f"🚫 Berhasil memblokir pengguna dengan ID `{b_id}`.", parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: {e}")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Format Admin: `/unban [ID_TELEGRAM]`", parse_mode="Markdown")
        return
    try:
        b_id = int(context.args[0])
        if b_id in BANNED_USERS_DB:
            BANNED_USERS_DB.remove(b_id)
            save_db(GLOBAL_DB)
        await update.effective_message.reply_text(f"✅ Berhasil membuka blokir pengguna ID `{b_id}`.", parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: {e}")

async def createcode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Format Admin: `/createcode [JUMLAH_HARI] [MAKSIMAL_ID]`\nContoh: `/createcode 7 10`", parse_mode="Markdown")
        return
    try:
        days = int(context.args[0])
        max_uses = int(context.args[1])
        random_code = f"VIP-{random.randint(10000, 99999)}"

        REDEEM_CODES_DB[random_code] = {
            "days": days,
            "max_uses": max_uses,
            "used_by": []
        }
        save_db(GLOBAL_DB)

        await update.effective_message.reply_text(
            f"✅ **Kode Redeem Berhasil Dibuat!**\n\n"
            f"• Kode: `{random_code}`\n"
            f"• Masa Aktif: `{days} Hari`\n"
            f"• Kuota Maksimal ID: `{max_uses} Pengguna`\n\n"
            f"Buka **Panel Admin** untuk membagikan kode ini secara otomatis ke semua pengguna.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: {e}")

async def addpoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Format: `/addpoint [ID] [JUMLAH]`", parse_mode="Markdown")
        return
    try:
        t_id, amt = int(context.args[0]), int(context.args[1])
        t_user = get_user_data(t_id)
        t_user["points"] += amt
        save_db(GLOBAL_DB)
        await update.effective_message.reply_text(f"✅ Berhasil menambah {amt} Poin untuk user {t_id}.")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: {e}")

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        ("start", "Mulai ulang / Menu Utama"),
        ("menu", "Tampilkan panel menu"),
        ("stop", "Akhiri percakapan anonim"),
        ("id", "Cek ID Telegram Anda"),
    ])

def main() -> None:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("stop", stop_chat_cmd))
    app.add_handler(CommandHandler("id", check_id_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(CommandHandler("addpoint", addpoint))
    app.add_handler(CommandHandler("createcode", createcode))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 BOT ANONYMEET & GHOSTCHAT SYSTEM BERJALAN SEMPURNA!")
    app.run_polling()

if __name__ == "__main__":
    main()
