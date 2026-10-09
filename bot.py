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
import websockets

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

# ==================== WEBSOCKET SIGNALING SERVER (PYTHON) ====================
CONNECTED_CLIENTS = set()
WAITING_WS_QUEUE = []
CLIENT_PARTNERS = {}

async def websocket_handler(websocket):
    CONNECTED_CLIENTS.add(websocket)
    print("Client terhubung via WebSocket Python.")
    try:
        async for raw_message in websocket:
            try:
                data = json.loads(raw_message)
            except Exception:
                continue

            msg_type = data.get("type")

            if msg_type == 'find':
                if websocket in WAITING_WS_QUEUE:
                    WAITING_WS_QUEUE.remove(websocket)
                
                if len(WAITING_WS_QUEUE) > 0:
                    partner = WAITING_WS_QUEUE.pop(0)
                    if partner in CONNECTED_CLIENTS:
                        CLIENT_PARTNERS[websocket] = partner
                        CLIENT_PARTNERS[partner] = websocket

                        await websocket.send(json.dumps({'type': 'matched', 'role': 'caller'}))
                        await partner.send(json.dumps({'type': 'matched', 'role': 'callee'}))
                    else:
                        WAITING_WS_QUEUE.append(websocket)
                        await websocket.send(json.dumps({'type': 'waiting'}))
                else:
                    WAITING_WS_QUEUE.append(websocket)
                    await websocket.send(json.dumps({'type': 'waiting'}))

            elif msg_type == 'next':
                disconnect_ws_partner(websocket)
                if websocket in WAITING_WS_QUEUE:
                    WAITING_WS_QUEUE.remove(websocket)

                if len(WAITING_WS_QUEUE) > 0:
                    partner = WAITING_WS_QUEUE.pop(0)
                    if partner in CONNECTED_CLIENTS:
                        CLIENT_PARTNERS[websocket] = partner
                        CLIENT_PARTNERS[partner] = websocket
                        await websocket.send(json.dumps({'type': 'matched', 'role': 'caller'}))
                        await partner.send(json.dumps({'type': 'matched', 'role': 'callee'}))
                    else:
                        WAITING_WS_QUEUE.append(websocket)
                        await websocket.send(json.dumps({'type': 'waiting'}))
                else:
                    WAITING_WS_QUEUE.append(websocket)
                    await websocket.send(json.dumps({'type': 'waiting'}))

            elif msg_type in ['offer', 'answer', 'candidate']:
                partner = CLIENT_PARTNERS.get(websocket)
                if partner and partner in CONNECTED_CLIENTS:
                    await partner.send(json.dumps(data))

            elif msg_type == 'hangup':
                disconnect_ws_partner(websocket)
                await websocket.send(json.dumps({'type': 'ended'}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        CONNECTED_CLIENTS.discard(websocket)
        if websocket in WAITING_WS_QUEUE:
            WAITING_WS_QUEUE.remove(websocket)
        disconnect_ws_partner(websocket)
        print("Client terputus dari WebSocket.")

def disconnect_ws_partner(ws):
    partner = CLIENT_PARTNERS.get(ws)
    if partner:
        asyncio.create_task(safe_send_hangup(partner))
        CLIENT_PARTNERS.pop(partner, None)
        CLIENT_PARTNERS.pop(ws, None)

async def safe_send_hangup(partner_ws):
    try:
        if partner_ws in CONNECTED_CLIENTS:
            await partner_ws.send(json.dumps({'type': 'peer_disconnected'}))
    except Exception:
        pass

async def start_websocket_server():
    port = int(os.environ.get("PORT", 3000))
    async with websockets.serve(websocket_handler, "0.0.0.0", port):
        print(f"🚀 WebSocket Signaling Server aktif di port {port}")
        await asyncio.Future()

# ==================== TELEGRAM BOT LOGIC ====================
def get_country_info(user) -> tuple[str, str]:
    lang_code = (user.language_code or "").lower()
    if "id" in lang_code:
        return "Indonesia", "Indonesia 🇮🇩"
    elif "en" in lang_code:
        return "Global / English", "Global 🌐"
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

def ban_user_anonymeet(user_id: int, hours: int = 3):
    if is_admin(user_id): return
    ANONYMEET_BANS_DB[str(user_id)] = time.time() + (hours * 3600)
    save_db(GLOBAL_DB)

def is_permanent_premium(user_id: int) -> bool:
    return is_admin(user_id) or user_id in PREMIUM_IDS

def is_premium_or_admin(user_id: int) -> bool:
    if is_permanent_premium(user_id): return True
    user = get_user_data(user_id)
    return time.time() < user.get("bonus_expire_time", 0)

def admin_url() -> str:
    username = str(ADMIN_USERNAME).strip().lstrip('@')
    return f"https://t.me/{username}?text={urllib.parse.quote('Halo Admin, saya ingin upgrade akun bot AI ke Premium.')}"

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
    if is_banned(user_id): return

    user = get_user_data(user_id)
    is_adm = is_admin(user_id)
    is_perm = is_permanent_premium(user_id)
    is_prem = is_premium_or_admin(user_id)

    status_text = "👑 ADMIN / PREMIUM" if is_prem else "👤 FREE (Terkunci)"
    points = user.get('points', 50)

    menu = [
        [InlineKeyboardButton("👥 Cari Pasangan (Anonymeet)", callback_data="anonymeet_start")],
        [InlineKeyboardButton("📞 Voice Random Call (Telepon Suara)", callback_data="voice_random_menu")],
        [InlineKeyboardButton("📍 Cari Pasangan Terdekat (GPS)", callback_data="anonymeet_nearby_menu")],
        [InlineKeyboardButton("❤️ Reaksi Channel Telegram", callback_data="reaction_menu")],
        [InlineKeyboardButton("🎵 Buat Musik AI", callback_data="music"), InlineKeyboardButton("🎥 Buat Video AI (Engine)", callback_data="video_menu")],
        [InlineKeyboardButton("📥 Download Video", callback_data="download_zip_menu"), InlineKeyboardButton("🎧 Download MP3 HD", callback_data="mp3_download_menu")],
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
        f"• Status: `{status_text}`\n"
        f"• Poin: `🪙 {points}`\n\n"
        "💡 *Pilih menu di bawah atau klik tombol untuk memulai:*"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(welcome_message, reply_markup=InlineKeyboardMarkup(menu), parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(welcome_message, reply_markup=InlineKeyboardMarkup(menu), parse_mode="Markdown")

async def trigger_find_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_obj = update.effective_message

    if WAITING_ANONYMOUS_QUEUE:
        partner_id = WAITING_ANONYMOUS_QUEUE.pop(0)
        if partner_id == user_id:
            WAITING_ANONYMOUS_QUEUE.append(user_id)
            await chat_obj.reply_text("🚀 Sedang mencari pasangan untuk Anda...\nMohon tunggu sebentar.")
            return

        ACTIVE_ANONYMOUS_CHATS[user_id] = partner_id
        ACTIVE_ANONYMOUS_CHATS[partner_id] = user_id

        await chat_obj.reply_text("✨ **Pasangan Anonim Ditemukan!** ✨\n\nSilakan kirim pesan. Ketik /next, /report, atau /stop.", parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=partner_id, text="✨ **Pasangan Anonim Ditemukan!** ✨\n\nSilakan kirim pesan. Ketik /next, /report, atau /stop.", parse_mode="Markdown")
        except Exception:
            pass
    else:
        WAITING_ANONYMOUS_QUEUE.append(user_id)
        await chat_obj.reply_text("🚀 **Sedang mencari pasangan anonim...** Mohon tunggu beberapa saat.", parse_mode="Markdown")

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
        await trigger_find_partner(update, context)
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

    if data == "anonymeet_nearby_menu":
        await query.answer()
        user["waiting_location_share"] = True
        loc_keyboard = [[KeyboardButton("📍 Bagikan Lokasi Saya Sekarang", request_location=True)]]
        await query.message.reply_text(
            "📍 **CARI PASANGAN TERDEKAT (GPS)**\n\nKlik tombol di bawah untuk membagikan lokasi:",
            reply_markup=ReplyKeyboardMarkup(loc_keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return

    if data == "menu_lacak_id":
        await query.answer()
        user["waiting_telegram_id_input"] = True
        await query.message.reply_text("🔍 Masukkan **Telegram ID** target yang ingin dilacak:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data="main_menu")]]))
        return

    if data == "menu_get_phone":
        await query.answer()
        contact_keyboard = [[KeyboardButton("📱 Bagikan Nomor Telepon", request_contact=True)]]
        await query.message.reply_text("📱 Klik tombol di bawah untuk cek nomor:", reply_markup=ReplyKeyboardMarkup(contact_keyboard, resize_keyboard=True, one_time_keyboard=True))
        return

    if data == "reaction_menu":
        await query.answer()
        user["waiting_reaction_link"] = True
        await query.message.reply_text("❤️ Kirimkan link postingan channel Telegram:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data="main_menu")]]))
        return

    if data == "music":
        await query.answer()
        user["waiting_prompt"] = True
        await query.message.reply_text("🎵 Tuliskan ide lagu Anda:")
        return

    if data == "video_menu":
        await query.answer()
        user["selected_video_engine"] = "gemini"
        user["waiting_custom_engine_prompt"] = True
        await query.message.reply_text("🎥 Masukkan ide / topik video AI:")
        return

    if data == "mp3_download_menu":
        await query.answer()
        user["waiting_mp3_link"] = True
        await query.message.reply_text("🎧 Kirimkan link musik untuk diunduh ke MP3:")
        return

    if data == "download_zip_menu":
        await query.answer()
        user["waiting_download_link"] = True
        await query.message.reply_text("📥 Kirimkan link video untuk diunduh:")
        return

    if data == "redeem_menu":
        await query.answer()
        user["waiting_redeem_input"] = True
        await query.message.reply_text("🎁 Masukkan kode redeem VIP Anda:")
        return

    if data == "suggestion_menu":
        await query.answer()
        user["waiting_suggestion_input"] = True
        await query.message.reply_text("💡 Ketik saran / fitur baru untuk bot ini:")
        return

    if data == "saldo":
        await query.answer()
        await query.message.reply_text(f"💳 **AKUN & POIN**\n• Status: `{user['status']}`\n• Poin: `🪙 {user.get('points', 0)}`", parse_mode="Markdown")
        return

    if data == "check_id":
        await query.answer()
        await query.message.reply_text(f"🆔 Telegram ID Anda: `{query.from_user.id}`", parse_mode="Markdown")
        return

    if data == "admin":
        if not is_admin(user_id): return
        await query.answer()
        await query.message.reply_text("👑 Panel Admin Aktif.", parse_mode="Markdown")
        return

    if data == "main_menu":
        await query.answer()
        await start(update, context)
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_banned(user_id): return

    user = get_user_data(user_id)
    text = update.message.text.strip() if update.message.text else ""

    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS[user_id]
        if text.lower() == "/stop":
            ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            await update.message.reply_text("🔴 Percakapan diakhiri.")
            try:
                await context.bot.send_message(chat_id=partner_id, text="🔴 Partner mengakhiri percakapan.")
            except Exception:
                pass
            return
        try:
            await context.bot.send_message(chat_id=partner_id, text=text)
        except Exception:
            pass
        return

    if user.get("waiting_suggestion_input"):
        user.pop("waiting_suggestion_input", None)
        SUGGESTIONS_DB.append({"user_id": user_id, "text": text})
        save_db(GLOBAL_DB)
        await update.message.reply_text("✅ Saran berhasil dikirim ke admin!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]]))
        return

    await update.message.reply_text("Silakan gunakan tombol menu atau ketik /start.")

async def post_init(application: Application) -> None:
    asyncio.create_task(start_websocket_server())
    await application.bot.set_my_commands([
        BotCommand("start", "Menu Utama"),
        BotCommand("menu", "Tampilkan Menu"),
    ])

def main() -> None:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 BOT & WEBSOCKET SIGNALLING SERVER BERJALAN LENGKAP!")
    app.run_polling()

if __name__ == "__main__":
    main()
