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
    # Deteksi otomatis gender berdasarkan nama depan atau secara cerdas
    name = (user.first_name or "").lower()
    female_keywords = ["siti", "putri", "ayu", "dewi", "anisa", "zahra", "nur", "fitri", "indah", "nina", "vina", "dinda"]
    if any(k in name for k in female_keywords):
        return "Wanita 👩"
    return "Pria 👨"

def is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_ID) or user_id in ADMIN_IDS

def is_banned(user_id: int) -> bool:
    if is_admin(user_id): return False
    return user_id in BANNED_USERS_DB or str(user_id) in [str(b) for b in BANNED_USERS_DB]

def is_anonymeet_banned(user_id: int) -> tuple[bool, int]:
    if is_admin(user_id): return False, 0
    s_id = str(user_id)
    if s_id in ANONYMEET_BANS_DB:
        expire_time = ANONYMEET_BANS_DB[s_id]
        if time.time() < expire_time:
            return True, int(expire_time - time.time())
        else:
            del ANONYMEET_BANS_DB[s_id]
            save_db(GLOBAL_DB)
    return False, 0

def is_permanent_premium(user_id: int) -> bool:
    return is_admin(user_id) or user_id in PREMIUM_IDS

def is_anonymeet_premium(user_id: int) -> bool:
    if is_permanent_premium(user_id): return True
    user = get_user_data(user_id)
    return time.time() < user.get("anonymeet_premium_expire", 0)

def admin_url() -> str:
    username = str(ADMIN_USERNAME).strip().lstrip('@')
    text_pesan = "Halo Admin, saya ingin membeli saldo / Premium Anonymeet Pilihan Pasangan."
    return f"https://t.me/{username}?text={urllib.parse.quote(text_pesan)}"

def get_user_data(user_id: int) -> dict:
    s_id = str(user_id)
    if s_id not in USERS_DB:
        is_adm = is_admin(user_id)
        USERS_DB[s_id] = {
            "status": "ADMIN" if is_adm else "FREE",
            "saldo": 50000 if is_adm else 0,
            "points": 99999 if is_adm else 50,
            "anonymeet_premium_expire": 0,
            "preferred_gender": None,
        }
        save_db(GLOBAL_DB)
    return USERS_DB[s_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_banned(user_id): return

    user = get_user_data(user_id)
    is_adm = is_admin(user_id)
    is_prem = is_anonymeet_premium(user_id)

    status_text = "👑 PREMIUM ANONYMEET" if is_prem else "👤 FREE (Random)"
    points = user.get('points', 50)
    saldo = user.get('saldo', 0)

    menu = [
        [InlineKeyboardButton("👥 Cari Pasangan (Anonymeet)", callback_data="anonymeet_start")],
        [InlineKeyboardButton("📞 Voice Random Call (Telepon Suara)", callback_data="voice_random_menu")],
        [InlineKeyboardButton("💎 Beli / Info Premium Anonymeet", callback_data="info_anonymeet_premium")],
        [InlineKeyboardButton("💳 Cek Saldo & Akun", callback_data="saldo")],
        [InlineKeyboardButton("🆔 Cek ID Telegram", callback_data="check_id"), InlineKeyboardButton("💬 Hubungi Admin", url=admin_url())],
    ]
    if is_adm:
        menu.append([InlineKeyboardButton("👑 PANEL KONTROL ADMIN", callback_data="admin")])

    welcome_message = (
        "🔥 **WELCOME TO BYMODZ ANONYMEET & VOICE CALL** 🔥\n\n"
        "Cari teman ngobrol anonim secara acak atau pilih gender bagi member premium! 🚀\n\n"
        f"• Status: `{status_text}`\n"
        f"• Saldo: `Rp {saldo:,}`\n"
        f"• Poin: `🪙 {points}`\n\n"
        "💡 *Pilih menu di bawah untuk memulai:*"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(welcome_message, reply_markup=InlineKeyboardMarkup(menu), parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(welcome_message, reply_markup=InlineKeyboardMarkup(menu), parse_mode="Markdown")

async def trigger_find_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_obj = update.effective_message
    user = get_user_data(user_id)
    is_prem = is_anonymeet_premium(user_id)
    pref_gender = user.get("preferred_gender")

    if WAITING_ANONYMOUS_QUEUE:
        # Cari partner dari antrean yang cocok dengan preferensi jika user premium
        partner_idx = -1
        for idx, queued in enumerate(WAITING_ANONYMOUS_QUEUE):
            if queued["user_id"] == user_id:
                continue
            # Jika user premium dan menetapkan pilihan gender
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

            ACTIVE_ANONYMOUS_CHATS[user_id] = partner_id
            ACTIVE_ANONYMOUS_CHATS[partner_id] = user_id

            user_gender = detect_user_gender(update.effective_user)
            _, user_country = get_country_info(update.effective_user)

            admin_notice = GLOBAL_DB.get("admin_broadcast_text", "🚀 Selamat datang di Anonymeet! Nikmati obrolan santai, jaga kesopanan, dan hormati privasi sesama pengguna.")

            match_text_1 = (
                f"✨ **Pasangan Anonim Ditemukan!** ✨\n\n"
                f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
                f"🔒 **Privasi Terjaga:** Tidak ada nama asli atau ID Telegram yang ditampilkan.\n"
                f"• Gender Partner:\n`{partner_gender}`\n\n"
                f"• Asal:\n`{partner_country}`\n\n"
                f"💬 Silakan kirim pesan.\n"
                f"• /next ➔ Ganti pasangan\n"
                f"• /report ➔ Laporkan\n"
                f"• /stop ➔ Keluar"
            )

            match_text_2 = (
                f"✨ **Pasangan Anonim Ditemukan!** ✨\n\n"
                f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
                f"🔒 **Privasi Terjaga:** Tidak ada nama asli atau ID Telegram yang ditampilkan.\n"
                f"• Gender Partner:\n`{user_gender}`\n\n"
                f"• Asal:\n`{user_country}`\n\n"
                f"💬 Silakan kirim pesan.\n"
                f"• /next ➔ Ganti pasangan\n"
                f"• /report ➔ Laporkan\n"
                f"• /stop ➔ Keluar"
            )

            await chat_obj.reply_text(match_text_1, parse_mode="Markdown")
            try:
                await context.bot.send_message(chat_id=partner_id, text=match_text_2, parse_mode="Markdown")
            except Exception:
                pass
        else:
            # Masukkan ke antrean
            user_gender = detect_user_gender(update.effective_user)
            _, user_country = get_country_info(update.effective_user)
            WAITING_ANONYMOUS_QUEUE.append({"user_id": user_id, "gender": user_gender, "country": user_country})
            
            notif_prem = "🟢 Anda berada di **Anonymeet Premium**. Pilihan gender aktif." if is_prem else "👤 Anda dalam mode **Free** (Pasangan Random Murni)."
            admin_notice = GLOBAL_DB.get("admin_broadcast_text", "Selamat mengobrol!")
            await chat_obj.reply_text(
                f"🚀 **Sedang mencari pasangan anonim...**\n\n"
                f"{notif_prem}\n\n"
                f"📢 **Info Admin:** *{admin_notice}*\n\n"
                "Mohon tunggu beberapa saat.",
                parse_mode="Markdown"
            )
    else:
        user_gender = detect_user_gender(update.effective_user)
        _, user_country = get_country_info(update.effective_user)
        WAITING_ANONYMOUS_QUEUE.append({"user_id": user_id, "gender": user_gender, "country": user_country})
        
        notif_prem = "🟢 Anda berada di **Anonymeet Premium**." if is_prem else "👤 Anda dalam mode **Free** (Pasangan Random Murni)."
        await chat_obj.reply_text(
            f"🚀 **Sedang mencari pasangan anonim...**\n\n"
            f"{notif_prem}\n\n"
            "Mohon tunggu beberapa saat.",
            parse_mode="Markdown"
        )

async def stop_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(chat_id=partner_id, text="🔴 Pasangan Anda menutup percakapan. Ketik /start untuk mencari pasangan baru.")
            except Exception:
                pass
        await update.message.reply_text("🔴 Percakapan anonim diakhiri. Ketik /start untuk mencari pasangan baru.")
    else:
        await update.message.reply_text("❌ Anda sedang tidak terhubung dalam percakapan aktif.")

async def next_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(chat_id=partner_id, text="🔄 Pasangan Anda beralih ke sesi lain.")
            except Exception:
                pass
    WAITING_ANONYMOUS_QUEUE[:] = [item for item in WAITING_ANONYMOUS_QUEUE if item["user_id"] != user_id]
    await update.message.reply_text("🔄 **Mencari pasangan baru...**")
    await trigger_find_partner(update, context)

async def report_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ACTIVE_ANONYMOUS_CHATS:
        await update.message.reply_text("❌ Anda sedang tidak berada dalam sesi percakapan anonim.")
        return
    partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
    if partner_id in ACTIVE_ANONYMOUS_CHATS:
        ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
    await update.message.reply_text("✅ Laporan diterima. Sesi diakhiri.")
    try:
        await context.bot.send_message(chat_id=partner_id, text="⚠️ Partner melaporkan sesi ini. Sesi ditutup.")
    except Exception:
        pass

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    if is_banned(user_id): return

    user = get_user_data(user_id)
    data = query.data

    if data == "anonymeet_start":
        await query.answer()
        if user_id in ACTIVE_ANONYMOUS_CHATS:
            await query.message.reply_text("⚠️ Anda sedang terhubung dalam sesi obrolan!")
            return
        
        is_prem = is_anonymeet_premium(user_id)
        if is_prem:
            # Tampilkan pilihan gender bagi member premium
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
            "💎 **PREMIUM ANONYMEET (PILIHAN PASANGAN)**\n\n"
            "Nikmati fitur eksklusif untuk memilih gender pasangan sesuai keinginan Anda!\n\n"
            "📋 **Daftar Harga & Durasi:**\n"
            "• **7 Hari** ➔ Rp 5.000\n"
            "• **30 Hari (1 Bulan)** ➔ Rp 15.000\n"
            "• **90 Hari (3 Bulan)** ➔ Rp 35.000\n"
            "• **180 Hari (6 Bulan)** ➔ Rp 60.000\n"
            "• **365 Hari (1 Tahun)** ➔ Rp 100.000\n\n"
            "💬 *Silakan hubungi Admin untuk melakukan pembelian saldo atau aktivasi paket premium:*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Hubungi Admin", url=admin_url())],
                [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == "voice_random_menu":
        await query.answer()
        webapp_url = "https://bot-telegram-nine-alpha.vercel.app/"
        await query.message.reply_text(
            "📞 **VOICE RANDOM CALL (TELEPON SUARA)**\n\n"
            "Nikmati obrolan suara anonim dengan pengguna lain secara acak.\n\n"
            "Tekan tombol **‘Mulai Mengobrol’** di bawah untuk mulai.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎙️ Mulai Mengobrol", web_app=WebAppInfo(url=webapp_url))],
                [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == "saldo":
        await query.answer()
        await query.message.reply_text(
            f"💳 **INFORMASI SALDO & AKUN**\n\n"
            f"• Saldo Anda: `Rp {user.get('saldo', 0):,}`\n"
            f"• Status Anonymeet: `{'PREMIUM' if is_anonymeet_premium(user_id) else 'FREE'}`",
            parse_mode="Markdown"
        )
        return

    if data == "main_menu":
        await query.answer()
        await start(update, context)
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_banned(user_id): return

    text = update.message.text.strip() if update.message.text else ""

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
        try:
            await context.bot.send_message(chat_id=partner_id, text=text)
        except Exception:
            pass
        return

    await update.message.reply_text("Silakan gunakan tombol menu atau ketik /start.")

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Menu Utama"),
        BotCommand("menu", "Tampilkan Menu"),
        BotCommand("next", "Ganti pasangan baru"),
        BotCommand("stop", "Akhiri percakapan"),
        BotCommand("report", "Laporkan partner"),
    ])

def main() -> None:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("next", next_chat_cmd))
    app.add_handler(CommandHandler("stop", stop_chat_cmd))
    app.add_handler(CommandHandler("report", report_chat_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 BOT BERJALAN DENGAN FITUR PREMIUM ANONYMEET!")
    app.run_polling()

if __name__ == "__main__":
    main()
