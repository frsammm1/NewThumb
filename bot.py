import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
import os, json, secrets, string, io
from datetime import datetime, timedelta
from aiohttp import web
import asyncio
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload, MediaFileUpload
from google.oauth2 import service_account
import tempfile

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
SUPPORT_USERNAME = os.environ.get('SUPPORT_USERNAME', 'your_username')
PORT = int(os.environ.get('PORT', '10000'))

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

MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2GB for Drive
TELEGRAM_LIMIT = 50 * 1024 * 1024   # 50MB for Telegram

def init_google_drive():
    global drive_service
    try:
        if GOOGLE_CREDENTIALS_JSON:
            logger.info("📄 Using JSON credentials")
            credentials_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        elif GOOGLE_CLIENT_EMAIL and GOOGLE_PRIVATE_KEY:
            logger.info("🔑 Using email + key")
            credentials_dict = {
                "type": "service_account",
                "client_email": GOOGLE_CLIENT_EMAIL,
                "private_key": GOOGLE_PRIVATE_KEY,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        else:
            logger.error("❌ No credentials!")
            return None
        
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict, scopes=['https://www.googleapis.com/auth/drive']
        )
        drive_service = build('drive', 'v3', credentials=credentials)
        logger.info("✅ Drive connected!")
        return drive_service
    except Exception as e:
        logger.error(f"❌ Drive error: {e}")
        return None

def upload_to_drive_chunked(file_data, filename, status_callback=None):
    """Upload large files with progress"""
    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
        temp_file.write(file_data)
        temp_file.close()
        
        file_metadata = {
            'name': filename,
            'parents': [GOOGLE_FOLDER_ID] if GOOGLE_FOLDER_ID else []
        }
        
        media = MediaFileUpload(
            temp_file.name,
            mimetype='video/mp4',
            resumable=True,
            chunksize=5*1024*1024  # 5MB chunks
        )
        
        request = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        )
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"Upload progress: {progress}%")
                if status_callback:
                    asyncio.create_task(status_callback(progress))
        
        os.unlink(temp_file.name)
        logger.info(f"✅ Uploaded: {response.get('id')}")
        return response.get('id')
        
    except Exception as e:
        logger.error(f"❌ Upload error: {e}")
        if 'temp_file' in locals():
            try:
                os.unlink(temp_file.name)
            except:
                pass
        return None

def download_from_drive_chunked(file_id):
    """Download with better error handling"""
    try:
        request = drive_service.files().get_media(fileId=file_id)
        
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
        downloader = MediaIoBaseDownload(temp_file, request, chunksize=5*1024*1024)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"Download: {progress}%")
        
        temp_file.close()
        
        with open(temp_file.name, 'rb') as f:
            data = f.read()
        
        os.unlink(temp_file.name)
        logger.info(f"✅ Downloaded: {len(data)} bytes")
        return data
        
    except Exception as e:
        logger.error(f"❌ Download error: {e}")
        if 'temp_file' in locals():
            try:
                os.unlink(temp_file.name)
            except:
                pass
        return None

def delete_from_drive(file_id):
    try:
        drive_service.files().delete(fileId=file_id).execute()
        return True
    except Exception as e:
        logger.error(f"Delete error: {e}")
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
        return True, f"{days}d"
    return True, f"{hours}h"

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
            kb.append([InlineKeyboardButton("⏱️ Sub", callback_data="my_sub")])
        else:
            kb.append([InlineKeyboardButton("💎 Buy", callback_data="buy_sub")])
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
        text = "🎬 <b>Video Editor - Admin</b>\n\n👑 Owner\n🔄 Keep-Alive: ✅\n☁️ Drive: ✅\n\nChoose:"
    else:
        if is_sub:
            text = f"🎬 <b>Video Editor</b>\n\n✅ Active ({status})\n\n<b>Features:</b>\n• Change thumbnails\n• Edit captions\n• Any size video!\n\nReady!"
        else:
            text = "🎬 <b>Video Editor</b>\n\n❌ No sub\n\n<b>Features:</b>\n• Thumbnails\n• Captions\n• Any size!\n\n💎 Get access!"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=create_main_menu(user_id))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    if data == "gen_key" and user_id == OWNER_ID:
        user_sessions[user_id] = {'mode': 'gen_key'}
        await query.edit_message_text(
            "🔑 <b>Generate Key</b>\n\nDuration:\n• <code>1d</code>\n• <code>7d</code>\n• <code>30d</code>\n\nSend:",
            parse_mode=ParseMode.HTML
        )
        return
    
    if data == "view_users" and user_id == OWNER_ID:
        total = len(users_db)
        active = len([u for u in subscriptions.values() if datetime.now() < datetime.fromisoformat(u['expiry'])])
        msg = f"👥 <b>Users</b>\n\nTotal: {total}\nActive: {active}"
        await query.edit_message_text(msg, parse_mode=ParseMode.HTML)
        return
    
    if data == "stats" and user_id == OWNER_ID:
        text = f"📊 <b>Stats</b>\n\n👥 Users: {len(users_db)}\n🔑 Keys: {len(auth_keys)}\n🔄 Uptime: {keep_alive_counter}s"
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
        return
    
    if data == "broadcast" and user_id == OWNER_ID:
        user_sessions[user_id] = {'mode': 'broadcast'}
        await query.edit_message_text("📢 <b>Broadcast</b>\n\nSend message:", parse_mode=ParseMode.HTML)
        return
    
    if data == "buy_sub":
        text = f"💎 <b>Get Sub</b>\n\nContact: @{SUPPORT_USERNAME}\n\n1. Contact\n2. Get key\n3. Activate!"
        kb = [[InlineKeyboardButton("📱 Contact", url=f"https://t.me/{SUPPORT_USERNAME}")]]
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data == "my_sub":
        is_sub, status = check_subscription(user_id)
        text = f"✅ <b>Subscription</b>\n\n⏱️ {status}" if is_sub else "❌ No sub"
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
            "📹 Send videos (any size!)\n\n"
            "Steps:\n"
            "1. Send videos\n"
            "2. Type: <code>done</code>\n"
            "3. Send thumbnail\n"
            "4. Done!\n\n"
            "⚡ Supports large files!"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
        return
    
    if data == "help":
        text = (
            "❓ <b>Help</b>\n\n"
            "<b>Features:</b>\n"
            "• Real thumbnail change\n"
            "• Any video size\n"
            "• Bulk processing\n"
            "• Caption editing\n\n"
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
    file_size = video.file_size
    
    if file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"❌ File too large! Max: {MAX_FILE_SIZE // (1024*1024)}MB")
        return
    
    status = await update.message.reply_text("⏳ Starting upload to Drive...")
    
    try:
        video_file = await context.bot.get_file(video.file_id)
        
        await status.edit_text(f"📥 Downloading... ({file_size // (1024*1024)}MB)")
        video_bytes = await video_file.download_as_bytearray()
        
        filename = f"v_{user_id}_{len(session['videos'])}_{int(datetime.now().timestamp())}.mp4"
        
        await status.edit_text("☁️ Uploading to Drive...")
        
        async def update_progress(progress):
            try:
                await status.edit_text(f"☁️ Uploading: {progress}%")
            except:
                pass
        
        drive_id = upload_to_drive_chunked(video_bytes, filename, update_progress)
        
        if not drive_id:
            await status.edit_text("❌ Upload failed! Try smaller video or try again.")
            return
        
        session['videos'].append({
            'drive_id': drive_id,
            'caption': update.message.caption or "",
            'duration': video.duration,
            'width': video.width,
            'height': video.height,
            'filename': filename,
            'size': file_size
        })
        
        count = len(session['videos'])
        size_mb = file_size // (1024*1024)
        await status.edit_text(
            f"✅ <b>Video {count} uploaded!</b>\n\n"
            f"📦 Size: {size_mb}MB\n"
            f"☁️ In Drive: Safe\n\n"
            f"Send more or: <code>done</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Video error: {e}")
        await status.edit_text(f"❌ Error: {str(e)}\n\nTry again or smaller video.")

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
        "✅ <b>Thumbnail saved!</b>\n\nReplace caption?\n• <code>yes</code>\n• <code>no</code>",
        parse_mode=ParseMode.HTML
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    text_lower = text.lower()
    
    # Auth key
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
            save_json(SUBSCRIPTIONS_FILE, subscriptions)
            save_json(AUTH_KEYS_FILE, auth_keys)
            await update.message.reply_text(
                f"🎉 <b>Activated!</b>\n\n✅ Duration: {key['duration_str']}\n\n/start",
                parse_mode=ParseMode.HTML
            )
            return
    
    # Broadcast
    if user_id in user_sessions and user_sessions[user_id].get('mode') == 'broadcast':
        await do_broadcast(update, context, update.message)
        if user_id in user_sessions:
            del user_sessions[user_id]
        return
    
    # Gen key
    if user_id in user_sessions and user_sessions[user_id].get('mode') == 'gen_key':
        try:
            dur = text.lower()
            hours = int(dur[:-1]) * 24 if dur.endswith('d') else int(dur[:-1])
            key = generate_auth_key()
            auth_keys[key] = {
                'duration_hours': hours,
                'duration_str': text,
                'created': datetime.now().isoformat(),
                'used': False
            }
            save_json(AUTH_KEYS_FILE, auth_keys)
            await update.message.reply_text(f"🔑 <code>{key}</code>\n\n⏱️ {text}", parse_mode=ParseMode.HTML)
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
        session['step'] = 'wait_thumb'
        await update.message.reply_text(
            f"✅ <b>{len(session['videos'])} ready!</b>\n\n📸 Send thumbnail",
            parse_mode=ParseMode.HTML
        )
        return
    
    if text_lower in ['yes', 'no'] and step == 'got_thumb':
        if text_lower == 'yes':
            session['step'] = 'wait_find'
            await update.message.reply_text("🔍 <b>Find:</b>", parse_mode=ParseMode.HTML)
        else:
            await process_videos(update, context, user_id)
        return
    
    if step == 'wait_find':
        session['find'] = text
        session['step'] = 'wait_replace'
        await update.message.reply_text(f"✅ Find: <code>{text}</code>\n\n📝 Replace:", parse_mode=ParseMode.HTML)
        return
    
    if step == 'wait_replace':
        session['replace'] = text
        await update.message.reply_text("⏳ Processing...", parse_mode=ParseMode.HTML)
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
        user_id, f"⏳ <b>Processing {total}...</b>", parse_mode=ParseMode.HTML
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
            
            video_data = download_from_drive_chunked(video['drive_id'])
            if not video_data:
                await context.bot.send_message(user_id, f"❌ Video {idx} download failed")
                continue
            
            caption = video['caption']
            if find and replace and caption:
                caption = caption.replace(find, replace)
            
            video_size = len(video_data)
            
            if video_size > TELEGRAM_LIMIT:
                await context.bot.send_message(
                    user_id,
                    f"⚠️ Video {idx} ({video_size//(1024*1024)}MB) too large for Telegram (max 50MB).\n"
                    f"Saved in Drive. Download manually if needed.",
                    parse_mode=ParseMode.HTML
                )
                continue
            
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
    success = fail = 0
    status = await context.bot.send_message(
        update.effective_user.id, "📡 Broadcasting...", parse_mode=ParseMode.HTML
    )
    for uid_str in users_db:
        try:
            tid = int(uid_str)
            if message.text:
                await context.bot.send_message(tid, f"📢 {message.text}")
            elif message.photo:
                await context.bot.send_photo(tid, message.photo[-1].file_id)
            success += 1
        except:
            fail += 1
    save_json(USER_DB_FILE, users_db)
    await status.edit_text(f"✅ Sent: {success}\n✗ Failed: {fail}", parse_mode=ParseMode.HTML)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if 'videos' in session:
            for video in session['videos']:
                delete_from_drive(video.get('drive_id'))
        del user_sessions[user_id]
    await update.message.reply_text("❌ Cancelled! /start")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

async def keep_alive_task():
    global keep_alive_counter
    while True:
        keep_alive_counter += 1
        if keep_alive_counter % 300 == 0:
            logger.info(f"🔄 Heartbeat #{keep_alive_counter // 300}")
        await asyncio.sleep(1)

async def health_check(request):
    return web.Response(
        text=f"🎬 Bot Running!\n�� {keep_alive_counter}s\n☁️ Drive: {'✅' if drive_service else '❌'}"
    )

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Server on port {PORT}")

async def main():
    if not BOT_TOKEN or not OWNER_ID:
        logger.error("❌ Missing BOT_TOKEN or OWNER_ID!")
        return
    
    logger.info("=" * 60)
    logger.info("🎬 VIDEO EDITOR BOT STARTING...")
    logger.info("=" * 60)
    
    drive = init_google_drive()
    if not drive:
        logger.error("❌ GOOGLE DRIVE FAILED!")
        logger.error("Set: GOOGLE_CREDENTIALS_JSON and GOOGLE_FOLDER_ID")
        return
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    
    await start_web_server()
    asyncio.create_task(keep_alive_task())
    
    logger.info("✅ BOT STARTED!")
    logger.info(f"👥 Users: {len(users_db)}")
    logger.info(f"☁️ Drive: Connected")
    logger.info(f"📦 Max size: 2GB (Drive), 50MB (Telegram)")
    logger.info("=" * 60)
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        await app.stop()

if __name__ == '__main__':
    asyncio.run(main())
