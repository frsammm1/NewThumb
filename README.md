# 🎬 Ultimate Video Editor Bot

**GUARANTEED to work!** Real thumbnail changes with Google Drive!

## ✅ Features
- Real thumbnail replacement
- Caption find & replace
- Bulk processing
- Subscription system
- Keep-alive (runs every 1 second!)
- Google Drive integration

## 🔧 Environment Variables

### Required (4):
```
BOT_TOKEN = your_bot_token
OWNER_ID = your_telegram_id
SUPPORT_USERNAME = your_username
```

### Google Drive (Choose ONE method):

**METHOD 1 (Easiest):**
```
GOOGLE_CREDENTIALS_JSON = {"type":"service_account","project_id":"..."}
GOOGLE_FOLDER_ID = 1ABC-XYZ123
```

**METHOD 2:**
```
GOOGLE_CLIENT_EMAIL = bot@project.iam.gserviceaccount.com
GOOGLE_PRIVATE_KEY = -----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n
GOOGLE_FOLDER_ID = 1ABC-XYZ123
```

## 📋 Setup
1. Create Google service account
2. Download JSON key
3. Create Drive folder
4. Share folder with service account
5. Set env vars on Render
6. Deploy!

## 🔄 Keep-Alive
- Runs every 1 second
- Logs every 5 minutes
- Extra ping every 30 seconds
- GUARANTEED to stay awake!

Works on Render free tier! ✅
