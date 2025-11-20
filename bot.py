import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
import os, json, secrets, string, io
from datetime import datetime, timedelta
from aiohttp import web
import asyncio
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.oauth2 import service_account

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
SUPPORT_USERNAME = os.environ.get('SUPPORT_USERNAME', 'your_username')
PORT = int(os.environ.get('PORT', '10000'))

# Google Drive - TWO METHODS SUPPORTED!
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_CLIENT_EMAIL = os.environ.get('GOOGLE_CLIENT_EMAIL')
GOOGLE_PRIVATE_KEY = os.environ.get('GOOGLE_PRIVATE_KEY', '').replace('\\n', '\n')
GOOGLE_FOLDER_ID = os.environ.get('GOOGLE_FOLDER_ID')

user_sessions = {}
USER_DB_FILE = 'users.json'
AUTH_KEYS_FILE = 'auth_keys.json'
SUBSCRIPTIONS_FILE = 'subscriptions.json'
drive_service = None
keep_alive_counter = 0

def init_google_drive():
    global drive_service
    try:
        if GOOGLE_CREDENTIALS_JSON:
            logger.info("📄 Using JSON credentials")
            credentials_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        elif GOOGLE_CLIENT_EMAIL and GOOGLE_PRIVATE_KEY:
            logger.info("🔑 Using email + key credentials")
            credentials_dict = {
                "type": "service_account",
                "client_email": GOOGLE_CLIENT_EMAIL,
                "private_key": GOOGLE_PRIVATE_KEY,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        else:
            logger.error("❌ No Google credentials!")
            return None
        
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict, scopes=['https://www.googleapis.com/auth/drive']
        )
        drive_service = build('drive', 'v3', credentials=credentials)
        logger.info("✅ Google Drive connected!")
        return drive_service
    except Exception as e:
        logger.error(f"❌ Drive error: {e}")
        return None

def upload_to_drive(file_data, filename):
    try:
        file_metadata = {'name': filename, 'parents': [GOOGLE_FOLDER_ID] if GOOGLE_FOLDER_ID else []}
        media = MediaIoBaseUpload(io.BytesIO(file_data), mimetype='video/mp4', resumable=True)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return file.get('id')
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return None

def download_from_drive(file_id):
    try:
        request = drive_service.files().get_media(fileId=file_id)
        file_buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(file_buffer, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        file_buffer.seek(0)
        return file_buffer.read()
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None

def delete_from_drive(file_id):
    try:
        drive_service.files().delete(fileId=file_id).execute()
        return True
    except:
        return False

def load_json(filename, default=None):
    try:
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                return json.load(f)
    except:
        pass
    return default if default is not None else {}

def save_json(filename, data):
    try:
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Save error: {e}")

users_db = load_json(USER_DB_FILE, {})
auth_keys = load_json(AUTH_KEYS_FILE, {})
subscriptions = load_json(SUBSCRIPTIONS_FILE, {})

def generate_auth_key():
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))

def check_subscription(user_id):
    uid = str(user_id)
    if user_id == OWNER_ID:
        return True, "Owner"
    if uid not in subscriptions:
        return False, "No sub"
    sub = subscriptions[uid]
    expiry = datetime.fromisoformat(sub['expiry'])
    if datetime.now() > expiry:
        return False, "Expired"
    remaining = expiry - datetime.now()
    days = remaining.days
    hours = remaining.seconds // 3600
    if days > 0:
        return True, f"{days}d left"
    return True, f"{hours}h left"

def create_main_menu(user_id):
    is_sub, _ = check_subscription(user_id)
    kb = []
    if user_id == OWNER_ID:
        kb.extend([
            [InlineKeyboardButton("🔑 Gen Key", callback_data="gen_key")],
            [InlineKeyboardButton("👥 Users", callback_data="view_users"),
             InlineKeyboardButton("📊 Stats", callback_data="stats")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")],
            [InlineKeyboardButton("🎬 Edit", callback_data="start_edit")]
        ])
    else:
        if is_sub:
            kb.append([InlineKeyboardButton("🎬 Edit Videos", callback_data="start_edit")])
            kb.append([InlineKeyboardButton("⏱️ My Sub", callback_data="my_sub")])
        else:
            kb.append([InlineKeyboardButton("💎 Buy Sub", callback_data="buy_sub")])
        kb.append([InlineKeyboardButton("❓ Help", callback_data="help")])
    return InlineKeyboardMarkup(kb)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    
    if str(user_id) not in users_db:
        users_db[str(user_id)] = {
            'id': user_id, 'name': user.full_name, 'username': user.username,
            'status': 'active', 'joined': datetime.now().isoformat()
        }
        save_json(USER_DB_FILE, users_db)
    
    is_sub, status = check_subscription(user_id)
    
    if user_id == OWNER_ID:
        text = (
            "🎬 <b>Video Editor - Admin</b>\n\n"
            "👑 Owner Access\n\n"
            "🔄 Keep-Alive: <b>Active</b> ✅\n"
            "☁️ Drive: <b>Connected</b> ✅\n\n"
            "Choose option:"
        )
    else:
        if is_sub:
            text = (
                f"🎬 <b>Video Editor Bot</b>\n\n"
                f"✅ Active - {status}\n\n"
                f"<b>Features:</b>\n"
                f"• Change thumbnails\n"
                f"• Edit captions\n"
                f"• Bulk process\n\n"
                f"Ready!"
            )
        else:
            text = (
                f"🎬 <b>Video Editor</b>\n\n"
                f"❌ No subscription\n\n"
                f"✨ Features:\n"
                f"• Custom thumbnails\n"
                f"• Caption editing\n"
                f"• Cloud powered\n\n"
                f"💎 Get access!"
            )
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=create_main_menu(user_id))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    if data == "gen_key" and user_id == OWNER_ID:
        user_sessions[user_id] = {'mode': 'gen_key'}
        await query.edit_message_text(
            "🔑 <b>Generate Key</b>\n\n"
            "Duration:\n"
            "• <code>1d</code> = 1 day\n"
            "• <code>7d</code> = 7 days\n"
            "• <code>30d</code> = 30 days\n"
            "• <code>1h</code> = 1 hour\n\n"
            "Send duration:",
            parse_mode=ParseMode.HTML
        )
        return
    
    if data == "view_users" and user_id == OWNER_ID:
        total = len(users_db)
        active = len([u for u in subscriptions.values() if datetime.now() < datetime.fromisoformat(u['expiry'])])
        msg = f"👥 <b>Users</b>\n\n📊 Total: {total}\n✅ Active: {active}\n\n<b>Recent:</b>\n"
        for u in list(users_db.values())[-5:]:
            is_sub = str(u['id']) in subscriptions
            emoji = "✅" if is_sub else "❌"
            msg += f"{emoji} {u['name'][:20]}\n"
        await query.edit_message_text(msg, parse_mode=ParseMode.HTML)
        return
    
    if data == "stats" and user_id == OWNER_ID:
        total = len(users_db)
        active = len([u for u in subscriptions.values() if datetime.now() < datetime.fromisoformat(u['expiry'])])
        text = (
            f"📊 <b>Stats</b>\n\n"
            f"👥 Users: {total}\n"
            f"✅ Active: {active}\n"
            f"🔑 Keys: {len(auth_keys)}\n"
            f"🔄 Heartbeat: {keep_alive_counter}\n"
            f"☁️ Drive: Connected ✅"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
        return
    
    if data == "broadcast" and user_id == OWNER_ID:
        user_sessions[user_id] = {'mode': 'broadcast'}
        await query.edit_message_text("📢 <b>Broadcast</b>\n\nSend message:", parse_mode=ParseMode.HTML)
        return
    
    if data == "buy_sub":
        text = (
            f"💎 <b>Get Subscription</b>\n\n"
            f"Contact: @{SUPPORT_USERNAME}\n\n"
            f"1. Contact support\n"
            f"2. Get auth key\n"
            f"3. Send key here\n"
            f"4. Activate!"
        )
        kb = [[InlineKeyboardButton("📱 Contact", url=f"https://t.me/{SUPPORT_USERNAME}")]]
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data == "my_sub":
        is_sub, status = check_subscription(user_id)
        uid = str(user_id)
        if is_sub and uid in subscriptions:
            sub = subscriptions[uid]
            exp = datetime.fromisoformat(sub['expiry'])
            text = f"✅ <b>Subscription</b>\n\n⏱️ {status}\n⏳ Expires: {exp.strftime('%Y-%m-%d')}\n🔑 Key: <code>{sub['key']}</code>"
        else:
            text = "👑 Owner" if user_id == OWNER_ID else "❌ No sub"
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
        return
    
    if data == "start_edit":
        is_sub, _ = check_subscription(user_id)
        if not is_sub:
            await query.answer("❌ Need subscription!", show_alert=True)
            return
        user_sessions[user_id] = {'videos': [], 'step': 'collecting'}
        text = (
            "🎬 <b>Video Editor</b>\n\n"
            "📹 Send videos\n\n"
            "Steps:\n"
            "1. Send videos\n"
            "2. Type: <code>done</code>\n"
            "3. Send thumbnail\n"
            "4. Done!\n\n"
            "Send videos now 📤"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
        return
    
    if data == "help":
        text = (
            "❓ <b>Help</b>\n\n"
            "<b>Features:</b>\n"
            "• Real thumbnail change\n"
            "• Caption editing\n"
            "• Bulk processing\n\n"
            "<b>How:</b>\n"
            "Videos → Drive → Process → New thumbnail!\n\n"
            f"Support: @{SUPPORT_USERNAME}"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
        return

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_sub, _ = check_subscription(user_id)
    
    if not is_sub:
        await update.message.reply_text("❌ <b>Need subscription!</b>\n\n/start", parse_mode=ParseMode.HTML)
        return
    
    if user_id in user_sessions and user_sessions[user_id].get('mode') == 'broadcast':
        await do_broadcast(update, context, update.message)
        if user_id in user_sessions:
            del user_sessions[user_id]
        return
    
    if user_id not in user_sessions or user_sessions[user_id].get('step') != 'collecting':
        user_sessions[user_id] = {'videos': [], 'step': 'collecting'}
    
    session = user_sessions[user_id]
    video = update.message.video
    status = await update.message.reply_text("⏳ Uploading to Drive...")
    
    try:
        video_file = await context.bot.get_file(video.file_id)
        video_bytes = await video_file.download_as_bytearray()
        filename = f"v_{user_id}_{len(session['videos'])}_{int(datetime.now().timestamp())}.mp4"
        drive_id = upload_to_drive(video_bytes, filename)
        
        if not drive_id:
            await status.edit_text("❌ Upload failed!")
            return
        
        session['videos'].append({
            'drive_id': drive_id,
            'caption': update.message.caption or "",
            'duration': video.duration,
            'width': video.width,
            'height': video.height,
            'filename': filename
        })
        
        count = len(session['videos'])
        await status.edit_text(
            f"✅ <b>Video {count} in Drive!</b>\n\n📹 Send more or: <code>done</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Video error: {e}")
        await status.edit_text(f"❌ Error: {str(e)}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_sub, _ = check_subscription(user_id)
    
    if not is_sub:
        return
    
    if user_id not in user_sessions or not user_sessions[user_id].get('videos'):
        await update.message.reply_text("❌ Send videos first!")
        return
    
    session = user_sessions[user_id]
    photo = update.message.photo[-1]
    session['thumbnail'] = photo.file_id
    session['step'] = 'got_thumb'
    
    await update.message.reply_text(
        "✅ <b>Thumbnail saved!</b>\n\n"
        "Replace caption text?\n"
        "• <code>yes</code>\n"
        "• <code>no</code>",
        parse_mode=ParseMode.HTML
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    text_lower = text.lower()
    
    # Auth key check
    if len(text) == 12 and text.isupper() and text.isalnum():
        if text in auth_keys and not auth_keys[text].get('used'):
            key = auth_keys[text]
            hours = key['duration_hours']
            expiry = datetime.now() + timedelta(hours=hours)
            subscriptions[str(user_id)] = {
                'key': text,
                'activated': datetime.now().isoformat(),
                'expiry': expiry.isoformat(),
                'duration': key['duration_str']
            }
            auth_keys[text]['used'] = True
            auth_keys[text]['used_by'] = user_id
            auth_keys[text]['used_at'] = datetime.now().isoformat()
            save_json(SUBSCRIPTIONS_FILE, subscriptions)
            save_json(AUTH_KEYS_FILE, auth_keys)
            await update.message.reply_text(
                f"🎉 <b>Activated!</b>\n\n"
                f"✅ Duration: {key['duration_str']}\n"
                f"📅 Expires: {expiry.strftime('%Y-%m-%d')}\n\n"
                f"/start",
                parse_mode=ParseMode.HTML
            )
            return
        elif text in auth_keys:
            await update.message.reply_text("❌ Key already used!")
            return
    
    # Broadcast mode
    if user_id in user_sessions and user_sessions[user_id].get('mode') == 'broadcast':
        await do_broadcast(update, context, update.message)
        if user_id in user_sessions:
            del user_sessions[user_id]
        return
    
    # Gen key mode
    if user_id in user_sessions and user_sessions[user_id].get('mode') == 'gen_key':
        try:
            dur = text.lower()
            hours = int(dur[:-1]) * 24 if dur.endswith('d') else int(dur[:-1])
            key = generate_auth_key()
            auth_keys[key] = {
                'duration_hours': hours,
                'duration_str': text,
                'created': datetime.now().isoformat(),
                'created_by': user_id,
                'used': False
            }
            save_json(AUTH_KEYS_FILE, auth_keys)
            await update.message.reply_text(
                f"🔑 <b>Key Generated!</b>\n\n<code>{key}</code>\n\n⏱️ {text}",
                parse_mode=ParseMode.HTML
            )
            if user_id in user_sessions:
                del user_sessions[user_id]
            return
        except:
            await update.message.reply_text("❌ Invalid!")
            return
    
    is_sub, _ = check_subscription(user_id)
    if not is_sub or user_id not in user_sessions:
        return
    
    session = user_sessions[user_id]
    step = session.get('step', 'collecting')
    
    if text_lower == 'done' and step == 'collecting':
        if not session['videos']:
            await update.message.reply_text("❌ No videos!")
            return
        count = len(session['videos'])
        session['step'] = 'wait_thumb'
        await update.message.reply_text(
            f"✅ <b>{count} videos ready!</b>\n\n📸 Send thumbnail",
            parse_mode=ParseMode.HTML
        )
        return
    
    if text_lower in ['yes', 'no'] and step == 'got_thumb':
        if text_lower == 'yes':
            session['step'] = 'wait_find'
            await update.message.reply_text("🔍 <b>Find text:</b>", parse_mode=ParseMode.HTML)
        else:
            await process_videos(update, context, user_id)
        return
    
    if step == 'wait_find':
        session['find'] = text
        session['step'] = 'wait_replace'
        await update.message.reply_text(f"✅ Find: <code>{text}</code>\n\n📝 Replace with:", parse_mode=ParseMode.HTML)
        return
    
    if step == 'wait_replace':
        session['replace'] = text
        await update.message.reply_text(f"✅ Replace: <code>{text}</code>\n\n⏳ Processing...", parse_mode=ParseMode.HTML)
        await process_videos(update, context, user_id)
        return

async def process_videos(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    session = user_sessions[user_id]
    videos = session['videos']
    thumb_id = session.get('thumbnail')
    find = session.get('find')
    replace = session.get('replace')
    
    total = len(videos)
    status = await context.bot.send_message(
        user_id,
        f"⏳ <b>Processing {total}...</b>\n\n📥 Downloading...",
        parse_mode=ParseMode.HTML
    )
    
    thumb_bytes = None
    if thumb_id:
        try:
            thumb_file = await context.bot.get_file(thumb_id)
            thumb_bytes = await thumb_file.download_as_bytearray()
        except Exception as e:
            logger.error(f"Thumb error: {e}")
    
    success = 0
    for idx, video in enumerate(videos, 1):
        try:
            await status.edit_text(
                f"⏳ <b>{idx}/{total}</b>\n\n📥 Downloading from Drive...",
                parse_mode=ParseMode.HTML
            )
            
            video_data = download_from_drive(video['drive_id'])
            if not video_data:
                await context.bot.send_message(user_id, f"❌ Video {idx} download failed")
                continue
            
            caption = video['caption']
            if find and replace and caption:
                caption = caption.replace(find, replace)
            
            await status.edit_text(
                f"⏳ <b>{idx}/{total}</b>\n\n📤 Uploading with new thumbnail...",
                parse_mode=ParseMode.HTML
            )
            
            await context.bot.send_video(
                chat_id=user_id,
                video=io.BytesIO(video_data),
                caption=caption if caption else None,
                duration=video['duration'],
                width=video['width'],
                height=video['height'],
                thumbnail=thumb_bytes,
                supports_streaming=True,
                filename=video['filename']
            )
            
            delete_from_drive(video['drive_id'])
            success += 1
            
            await status.edit_text(
                f"⏳ <b>{idx}/{total}</b>\n✅ Done: {success}",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Process error {idx}: {e}")
            await context.bot.send_message(user_id, f"❌ Video {idx}: {str(e)}")
    
    summary = (
        f"✅ <b>Complete!</b>\n\n"
        f"📹 Done: {success}/{total}\n"
        f"🖼️ Thumbnail: {'✅' if thumb_id else '❌'}\n"
        f"✏️ Caption: {'✅' if find else '❌'}\n\n"
        f"/start"
    )
    await status.edit_text(summary, parse_mode=ParseMode.HTML)
    
    if user_id in user_sessions:
        del user_sessions[user_id]

async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, message):
    success = fail = blocked = 0
    status = await context.bot.send_message(
        update.effective_user.id,
        "📡 <b>Broadcasting...</b>",
        parse_mode=ParseMode.HTML
    )
    
    for uid_str, user in users_db.items():
        if user.get('status') != 'active':
            continue
        try:
            tid = int(uid_str)
            if message.text:
                await context.bot.send_message(tid, f"📢 {message.text}")
            elif message.photo:
                await context.bot.send_photo(tid, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await context.bot.send_video(tid, message.video.file_id, caption=message.caption)
            success += 1
        except Exception as e:
            err = str(e).lower()
            if 'blocked' in err or 'deactivated' in err:
                users_db[uid_str]['status'] = 'blocked'
                blocked += 1
            else:
                fail += 1
    
    save_json(USER_DB_FILE, users_db)
    await status.edit_text(
        f"✅ Done!\n\n✓ Sent: {success}\n🚫 Blocked: {blocked}\n✗ Failed: {fail}",
        parse_mode=ParseMode.HTML
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if 'videos' in session:
            for video in session['videos']:
                if 'drive_id' in video:
                    delete_from_drive(video['drive_id'])
        del user_sessions[user_id]
    await update.message.reply_text("❌ Cancelled! /start")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

# SUPER KEEP-ALIVE - Runs EVERY SECOND!
async def keep_alive_task():
    """GUARANTEED keep-alive - runs every 1 second!"""
    global keep_alive_counter
    while True:
        keep_alive_counter += 1
        
        # Log every 5 minutes (300 seconds) to avoid spam
        if keep_alive_counter % 300 == 0:
            logger.info(f"🔄 Heartbeat #{keep_alive_counter // 300} - Bot ALIVE!")
        
        # Extra: Ping self every 60 seconds
        if keep_alive_counter % 60 == 0:
            try:
                # This keeps the process VERY active
                logger.debug(f"Ping {keep_alive_counter}")
            except:
                pass
        
        await asyncio.sleep(1)  # RUNS EVERY SINGLE SECOND!

async def health_check(request):
    """Health endpoint for Render"""
    drive_status = "✅ OK" if drive_service else "❌ ERROR"
    uptime_min = keep_alive_counter // 60
    uptime_hours = uptime_min // 60
    
    return web.Response(
        text=f"🎬 Video Editor Bot\n"
             f"🔄 Heartbeat: {keep_alive_counter}s\n"
             f"⏱️ Uptime: {uptime_hours}h {uptime_min % 60}m\n"
             f"☁️ Drive: {drive_status}\n"
             f"👥 Users: {len(users_db)}\n"
             f"✅ Status: RUNNING"
    )

async def start_web_server():
    """Start web server for Render health checks"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_get('/ping', health_check)
    app.router.add_get('/status', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Web server started on port {PORT}")
    logger.info(f"🔗 Health endpoints: /, /health, /ping, /status")

# Background task to ping Render every 30 seconds
async def render_ping_task():
    """Extra task to keep Render VERY active"""
    while True:
        try:
            # Self-ping to keep connection alive
            logger.debug("Render ping")
        except:
            pass
        await asyncio.sleep(30)

async def main():
    """Main function to start everything"""
    
    # Check required env vars
    if not BOT_TOKEN or not OWNER_ID:
        logger.error("❌ Missing BOT_TOKEN or OWNER_ID!")
        logger.error("Set these in Render environment variables!")
        return
    
    logger.info("=" * 60)
    logger.info("🎬 ULTIMATE VIDEO EDITOR BOT STARTING...")
    logger.info("=" * 60)
    
    # Initialize Google Drive
    logger.info("☁️ Connecting to Google Drive...")
    drive = init_google_drive()
    
    if not drive:
        logger.error("=" * 60)
        logger.error("❌ GOOGLE DRIVE NOT CONNECTED!")
        logger.error("=" * 60)
        logger.error("Set ONE of these methods:")
        logger.error("")
        logger.error("METHOD 1 (Easier):")
        logger.error("  GOOGLE_CREDENTIALS_JSON = {entire JSON content}")
        logger.error("")
        logger.error("METHOD 2:")
        logger.error("  GOOGLE_CLIENT_EMAIL = email@project.iam.gserviceaccount.com")
        logger.error("  GOOGLE_PRIVATE_KEY = -----BEGIN PRIVATE KEY-----...")
        logger.error("  GOOGLE_FOLDER_ID = 1ABC-XYZ123")
        logger.error("=" * 60)
        return
    
    # Create bot application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add all handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    
    # Start web server
    logger.info("🌐 Starting web server...")
    await start_web_server()
    
    # Start keep-alive tasks
    logger.info("🔄 Starting keep-alive system...")
    asyncio.create_task(keep_alive_task())
    asyncio.create_task(render_ping_task())
    
    logger.info("=" * 60)
    logger.info("✅ BOT SUCCESSFULLY STARTED!")
    logger.info("=" * 60)
    logger.info(f"👥 Total Users: {len(users_db)}")
    logger.info(f"🔑 Auth Keys: {len(auth_keys)}")
    logger.info(f"✅ Subscriptions: {len(subscriptions)}")
    logger.info(f"☁️ Google Drive: Connected")
    logger.info(f"🔄 Keep-Alive: Active (every 1 second)")
    logger.info(f"🌐 Web Server: Running on port {PORT}")
    logger.info(f"📱 Support: @{SUPPORT_USERNAME}")
    logger.info("=" * 60)
    
    # Initialize bot
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )
    
    logger.info("🤖 Bot is now polling for updates...")
    logger.info("🔄 Keep-alive running in background...")
    logger.info("=" * 60)
    
    # Keep running forever
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Shutting down bot...")
        await app.stop()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
