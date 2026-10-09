import os
import json
import time
import urllib.parse
import random
import math

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, BotCommand, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==================== KONFIGURASI UTAMA ====================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = 5794869044
ADMIN_USERNAME = "@Bymodz"

ADMIN_IDS = [5794869044]
PREMIUM_IDS = [5794869044]
# ============================================================

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

STRICT_ADULT_KEYWORDS = ["porno", "kontol", "memek", "ngentot", "colmek", "bokep", "nsfw", "vcs", "sangean"]

def get_country_info(user) -> tuple[str, str]:
    lang_code = (user.language_code or "").lower()
    if "id" in lang_code:
        return "Indonesia", "Indonesia 🇮🇩"
    return "Global", "Global 🌐"

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

def is_anonymeet_premium(user_id: int) -> bool:
    if is_admin(user_id) or user_id in PREMIUM_IDS: return True
    user = get_user_data(user_id)
    return time.time() < user.get("anonymeet_premium_expire", 0)

def admin_url() -> str:
    username = str(ADMIN_USERNAME).strip().lstrip('@')
    text_pesan = "Halo Admin, saya ingin membeli saldo atau paket Premium Anonymeet Pilihan Pasangan."
    return f"https://t.me/{username}?text={urllib.parse.quote(text_pesan)}"

def clear_user_flow(user: dict) -> None:
    for key in (
        "waiting_telegram_id_input", "waiting_custom_admin_notice",
        "waiting_location_share", "waiting_suggestion_input", "waiting_redeem_input",
        "waiting_custom_engine_prompt", "waiting_mp3_link", "waiting_download_link", "waiting_reaction_link"
    ):
        user.pop(key, None)

def get_user_data(user_id: int) -> dict:
    s_id = str(user_id)
    if s_id not in USERS_DB:
        is_adm = is_admin(user_id)
        USERS_DB[s_id] = {
            "status": "ADMIN" if is_adm else "FREE",
            "saldo": 50000 if is_adm else 0,
            "points": 99999 if is_adm else 50,
            "anonymeet_premium_expire": 0,
            "preferred_gender": "Semua"
        }
        save_db(GLOBAL_DB)
    return USERS_DB[s_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_banned(user_id): return

    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(chat_id=partner_id, text="🔴 Pasangan Anda menutup percakapan.")
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
        [InlineKeyboardButton("🎵 Buat Musik AI", callback_data="music"), InlineKeyboardButton("🎥 Buat Video AI", callback_data="video_menu")],
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
        await update.callback_query.message.edit_text(welcome_message, reply_markup=InlineKeyboardMarkup(menu), parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(welcome_message, reply_markup=InlineKeyboardMarkup(menu), parse_mode="Markdown")

async def trigger_find_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_obj = update.effective_message
    user = get_user_data(user_id)
    is_prem = is_anonymeet_premium(user_id)
    pref_gender = user.get("preferred_gender", "Semua")

    is_banned_anon, sisa_detik = is_anonymeet_banned(user_id)
    if is_banned_anon:
        menit_sisa = int(sisa_detik // 60) + 1
        await chat_obj.reply_text(f"❌ **Akses Anonymeet Ditangguhkan!**\nAnda diblokir selama {menit_sisa} menit lagi.")
        return

    global WAITING_ANONYMOUS_QUEUE
    WAITING_ANONYMOUS_QUEUE = [item for item in WAITING_ANONYMOUS_QUEUE if item["user_id"] != user_id]

    if WAITING_ANONYMOUS_QUEUE:
        partner_idx = -1
        for idx, queued in enumerate(WAITING_ANONYMOUS_QUEUE):
            if queued["user_id"] == user_id: continue
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
            admin_notice = GLOBAL_DB.get("admin_broadcast_text", "Selamat mengobrol!")

            match_text_1 = (
                f"✨ **Pasangan Anonim Ditemukan!** ✨\n\n"
                f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
                f"🔒 **Privasi Terjaga**\n"
                f"• Gender Partner: `{partner_gender}`\n"
                f"• Asal: `{partner_country}`\n\n"
                f"💬 /next (Ganti), /report (Laporkan), /stop (Keluar)."
            )
            match_text_2 = (
                f"✨ **Pasangan Anonim Ditemukan!** ✨\n\n"
                f"📢 **Pesan dari Admin:**\n> *{admin_notice}*\n\n"
                f"🔒 **Privasi Terjaga**\n"
                f"• Gender Partner: `{user_gender}`\n"
                f"• Asal: `{user_country}`\n\n"
                f"💬 /next (Ganti), /report (Laporkan), /stop (Keluar)."
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
            await chat_obj.reply_text("🚀 **Sedang mencari pasangan anonim...** Mohon tunggu sebentar.", parse_mode="Markdown")
    else:
        user_gender = detect_user_gender(update.effective_user)
        _, user_country = get_country_info(update.effective_user)
        WAITING_ANONYMOUS_QUEUE.append({"user_id": user_id, "gender": user_gender, "country": user_country, "pref": pref_gender})
        await chat_obj.reply_text("🚀 **Sedang mencari pasangan anonim...** Mohon tunggu sebentar.", parse_mode="Markdown")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    if is_banned(user_id): return
    user = get_user_data(user_id)
    data = query.data

    if data == "admin":
        if not is_admin(user_id):
            await query.answer("Akses ditolak!", show_alert=True)
            return
        await query.answer()
        current_notice = GLOBAL_DB.get("admin_broadcast_text", "-")
        await query.message.reply_text(
            "👑 **PANEL KONTROL ADMIN**\n\n"
            f"📢 Kata Kustom Aktif: *{current_notice}*\n\n"
            "• Tambah Poin: `/addpoint [ID] [JML]`\n"
            "• Tambah Saldo: `/addsaldo [ID] [JML]`\n"
            "• Buat Kode: `/createcode [HARI] [MAKS]`\n"
            "• Blokir: `/ban [ID]` | Buka: `/unban [ID]`",
            parse_mode="Markdown"
        )
        return

    if data == "anonymeet_start":
        await query.answer()
        if user_id in ACTIVE_ANONYMOUS_CHATS:
            await query.message.reply_text("⚠️ Anda sedang dalam sesi obrolan aktif! Ketik /stop untuk mengakhiri.")
            return
        if is_anonymeet_premium(user_id):
            await query.message.reply_text(
                "🟢 **ANONYMEET PREMIUM**\n\nPilih gender pasangan:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👨 Pria", callback_data="pref_pria"), InlineKeyboardButton("👩 Wanita", callback_data="pref_wanita")],
                    [InlineKeyboardButton("🌐 Random", callback_data="pref_semua")]
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
        await query.message.edit_text(f"✅ Preferensi disimpan: **{user['preferred_gender']}**. Mencari pasangan...")
        await trigger_find_partner(update, context)
        return

    if data == "info_anonymeet_premium":
        await query.answer()
        await query.message.reply_text(
            "💎 **PAKET PREMIUM ANONYMEET (PILIHAN PASANGAN)**\n\n"
            "• **7 Hari** ➔ Rp 5.000\n"
            "• **30 Hari (1 Bulan)** ➔ Rp 15.000\n"
            "• **90 Hari (3 Bulan)** ➔ Rp 35.000\n"
            "• **180 Hari (6 Bulan)** ➔ Rp 60.000\n"
            "• **365 Hari (1 Tahun)** ➔ Rp 100.000\n\n"
            "Silakan hubungi Admin untuk pembelian.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Hubungi Admin", url=admin_url())], [InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]]),
            parse_mode="Markdown"
        )
        return

    if data == "voice_random_menu":
        await query.answer()
        await query.message.reply_text(
            "📞 **VOICE RANDOM CALL**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎙️ Buka WebApp", web_app=WebAppInfo(url="https://bot-telegram-nine-alpha.vercel.app/"))], [InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]]),
            parse_mode="Markdown"
        )
        return

    if data == "anonymeet_nearby_menu":
        await query.answer()
        clear_user_flow(user)
        user["waiting_location_share"] = True
        loc_keyboard = [[KeyboardButton("📍 Bagikan Lokasi Saya Sekarang", request_location=True)]]
        await query.message.reply_text("📍 **CARI PASANGAN TERDEKAT (GPS)**\n\nKlik tombol di bawah:", reply_markup=ReplyKeyboardMarkup(loc_keyboard, resize_keyboard=True, one_time_keyboard=True))
        return

    if data == "check_id" or data == "saldo":
        await query.answer()
        await query.message.reply_text(f"🆔 ID: `{user_id}`\n💰 Saldo: Rp `{user['saldo']:,}`\n🪙 Poin: `{user.get('points',0)}`", parse_mode="Markdown")
        return

    if data == "main_menu":
        await query.answer()
        await start(update, context)
        return

    await query.answer("Fitur siap digunakan.")

async def stop_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(chat_id=partner_id, text="🔴 Partner mengakhiri percakapan.")
            except Exception:
                pass
        await update.message.reply_text("🔴 Sesi diakhiri. Ketik /start untuk kembali ke menu utama.")
    else:
        await update.message.reply_text("❌ Anda tidak sedang dalam sesi aktif. Ketik /start untuk memunculkan menu.")

async def next_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in ACTIVE_ANONYMOUS_CHATS:
        partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
        if partner_id in ACTIVE_ANONYMOUS_CHATS:
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            try:
                await context.bot.send_message(chat_id=partner_id, text="🔄 Partner beralih sesi.")
            except Exception:
                pass
    global WAITING_ANONYMOUS_QUEUE
    WAITING_ANONYMOUS_QUEUE = [item for item in WAITING_ANONYMOUS_QUEUE if item["user_id"] != user_id]
    await update.message.reply_text("🔄 Mencari pasangan baru...")
    await trigger_find_partner(update, context)

async def report_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ACTIVE_ANONYMOUS_CHATS:
        await update.message.reply_text("❌ Anda sedang tidak dalam sesi.")
        return
    partner_id = ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
    if partner_id in ACTIVE_ANONYMOUS_CHATS:
        ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
    await update.message.reply_text("✅ Laporan diterima.")
    try:
        await context.bot.send_message(chat_id=partner_id, text="⚠️ Sesi ditutup karena dilaporkan.")
    except Exception:
        pass

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user = get_user_data(user_id)
    if not user.get("waiting_location_share"): return
    user.pop("waiting_location_share", None)
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude

    matched_partner = None
    for idx, waiting_item in enumerate(WAITING_LOCATION_QUEUE):
        if calculate_distance(lat, lon, waiting_item["lat"], waiting_item["lon"]) <= 30.0:
            matched_partner = WAITING_LOCATION_QUEUE.pop(idx)
            break

    if matched_partner:
        partner_id = matched_partner["user_id"]
        ACTIVE_ANONYMOUS_CHATS[user_id] = partner_id
        ACTIVE_ANONYMOUS_CHATS[partner_id] = user_id
        match_text = "✨ **Pasangan Terdekat (GPS) Ditemukan!** ✨\nSilakan kirim pesan."
        await update.message.reply_text(match_text, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=partner_id, text=match_text, parse_mode="Markdown")
        except Exception:
            pass
    else:
        WAITING_LOCATION_QUEUE.append({"user_id": user_id, "lat": lat, "lon": lon})
        await update.message.reply_text("📍 Mencari pengguna terdekat...", reply_markup=ReplyKeyboardRemove())

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
        
        if any(kw in text.lower() for kw in STRICT_ADULT_KEYWORDS) and not is_admin(user_id):
            ban_user_anonymeet(user_id, hours=3)
            ACTIVE_ANONYMOUS_CHATS.pop(user_id, None)
            ACTIVE_ANONYMOUS_CHATS.pop(partner_id, None)
            await update.message.reply_text("❌ Terdeteksi kata terlarang. Akses diblokir 3 jam.")
            try:
                await context.bot.send_message(chat_id=partner_id, text="🔴 Sesi diakhiri karena partner melanggar aturan.")
            except Exception:
                pass
            return

        try:
            await context.bot.send_message(chat_id=partner_id, text=text)
        except Exception:
            pass
        return

    await update.message.reply_text("Silakan gunakan tombol menu atau ketik /start.")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id) or len(context.args) != 1: return
    b_id = int(context.args[0])
    if b_id not in BANNED_USERS_DB: BANNED_USERS_DB.append(b_id); save_db(GLOBAL_DB)
    await update.effective_message.reply_text(f"🚫 Berhasil memblokir ID `{b_id}`.", parse_mode="Markdown")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id) or len(context.args) != 1: return
    b_id = int(context.args[0])
    if b_id in BANNED_USERS_DB: BANNED_USERS_DB.remove(b_id); save_db(GLOBAL_DB)
    await update.effective_message.reply_text(f"✅ Berhasil membuka blokir ID `{b_id}`.", parse_mode="Markdown")

async def createcode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id) or len(context.args) != 2: return
    days, max_uses = int(context.args[0]), int(context.args[1])
    code = f"VIP-{random.randint(10000, 99999)}"
    REDEEM_CODES_DB[code] = {"days": days, "max_uses": max_uses, "used_by": []}
    save_db(GLOBAL_DB)
    await update.effective_message.reply_text(f"✅ Kode redeem dibuat: `{code}`", parse_mode="Markdown")

async def addpoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id) or len(context.args) != 2: return
    t_id, amt = int(context.args[0]), int(context.args[1])
    get_user_data(t_id)["points"] += amt; save_db(GLOBAL_DB)
    await update.effective_message.reply_text(f"✅ Berhasil menambah {amt} poin.")

async def addsaldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id) or len(context.args) != 2: return
    t_id, amt = int(context.args[0]), int(context.args[1])
    get_user_data(t_id)["saldo"] += amt; save_db(GLOBAL_DB)
    await update.effective_message.reply_text(f"✅ Berhasil menambah saldo Rp {amt:,} untuk ID `{t_id}`.", parse_mode="Markdown")

def main() -> None:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("next", next_chat_cmd))
    app.add_handler(CommandHandler("stop", stop_chat_cmd))
    app.add_handler(CommandHandler("report", report_chat_cmd))
    app.add_handler(CommandHandler("addpoint", addpoint))
    app.add_handler(CommandHandler("addsaldo", addsaldo))
    app.add_handler(CommandHandler("createcode", createcode))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 BOT BERJALAN SEMPURNA!")
    app.run_polling()

if __name__ == "__main__":
    main()
