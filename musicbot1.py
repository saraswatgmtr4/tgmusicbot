import os
import logging
import asyncio
from pathlib import Path

from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import yt_dlp

# --------------------------
# Config
# --------------------------
# On Render: set BOT_TOKEN & BASE_URL as environment variables in the dashboard.
# Locally: falls back to input() so you don't need a .env file.
TOKEN = os.getenv("BOT_TOKEN") or input("Enter your bot token: ").strip()
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

# Render/Cloud environments expose $PORT; default to 8000 locally
PORT = int(os.getenv("PORT", "8000"))

# Use ephemeral /tmp for downloads (safe on Render free; files are not persisted)
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/tmp"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Keep it mp3-free (no ffmpeg) by sending the original best audio file as a document.
YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
    "quiet": True,
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("songbot")

# --------------------------
# Telegram application
# --------------------------
application = Application.builder().token(TOKEN).build()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hi! Send /song <name> to get the best-audio file.")

async def song_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /song <song name>")
        return

    query = " ".join(context.args)
    msg = await update.message.reply_text(f"Searching for “{query}”...")

    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(f"ytsearch:{query}", download=True)
            entry = info["entries"][0]
            filepath = Path(ydl.prepare_filename(entry))
            title = entry.get("title") or filepath.stem

        await msg.edit_text(f"Found: {title}\nUploading…")

        # We send as document to avoid format restrictions (no ffmpeg conversion).
        # Telegram will still preview/play many common audio formats.
        async with await context.bot._session_manager.get_file(filepath) as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=filepath.name,
                caption=title,
            )

        try:
            filepath.unlink(missing_ok=True)
        except Exception:
            pass

        await msg.edit_text("Done! 🎵")
    except Exception as e:
        logger.exception("Error in /song")
        await msg.edit_text(f"Error: {e}")

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("song", song_cmd))

# --------------------------
# FastAPI web server (webhook)
# --------------------------
app = FastAPI()

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/{token}")
async def telegram_webhook(token: str, request: Request):
    # Simple secret path using your bot token
    if token != TOKEN:
        return Response(status_code=403)
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    # Start PTB
    await application.initialize()
    await application.start()

    # Register Telegram webhook to your public URL on Render
    webhook_url = f"{BASE_URL}/{TOKEN}"
    await application.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook set to {webhook_url}")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await application.bot.delete_webhook()
    except Exception:
        pass
    await application.stop()
    await application.shutdown()

# Local dev: uvicorn main:app --reload
# On Render: Start Command uses uvicorn main:app --host 0.0.0.0 --port $PORT
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
