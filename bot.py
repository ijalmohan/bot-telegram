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
        await asyncio.Future()  # Menjaga server tetap berjalan

# ==================== TELEGRAM BOT HANDLERS ====================
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
        [InlineKeyboardButton("🆔 Cek ID Telegram", callback_data="check_id"), InlineKeyboardButton("💬 Hubungi Admin", url=admin_url())],
    ]
    if is_adm:
        menu.append([InlineKeyboardButton("👑 PANEL KONTROL ADMIN", callback_data="admin")])

    welcome_message = (
        "🔥 **WELCOME TO BYMODZ ANONYMEET & VOICE CALL** 🔥\n\n"
        f"• Status: `{status_text}`\n"
        f"• Poin: `🪙 {points}`\n\n"
        "💡 *Pilih menu di bawah untuk mulai:*"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(welcome_message, reply_markup=InlineKeyboardMarkup(menu), parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(welcome_message, reply_markup=InlineKeyboardMarkup(menu), parse_mode="Markdown")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    if is_banned(user_id): return

    data = query.data
    if data == "anonymeet_start":
        await query.answer()
        await query.message.reply_text("Fitur Anonymeet teks aktif.")
        return

    if data == "voice_random_menu":
        await query.answer()
        webapp_url = "https://bot-telegram-nine-alpha.vercel.app/"
        await query.message.reply_text(
            "📞 **VOICE RANDOM CALL (TELEPON SUARA)**\n\n"
            "Tekan tombol di bawah untuk mulai menelepon suara secara acak.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎙️ Mulai Mengobrol", web_app=WebAppInfo(url=webapp_url))],
                [InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == "main_menu":
        await query.answer()
        await start(update, context)
        return

async def post_init(application: Application) -> None:
    # Menjalankan WebSocket server secara background bersamaan dengan bot Telegram
    asyncio.create_task(start_websocket_server())
    await application.bot.set_my_commands([
        BotCommand("start", "Mulai ulang / Menu Utama"),
        BotCommand("menu", "Tampilkan panel menu"),
    ])

def main() -> None:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CallbackQueryHandler(button))

    print("🤖 BOT & WEBSOCKET SIGNALLING SERVER BERJALAN BERSAMAAN!")
    app.run_polling()

if __name__ == "__main__":
    main()
