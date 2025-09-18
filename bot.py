import os
import logging
import tempfile
import asyncio
import re
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import yt_dlp
import sys

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot token from environment variable
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# User data storage
user_data = {}
download_progress = {}

# Define yt-dlp options with progress hook
def get_ydl_opts(format_type, output_path, chat_id):
    def progress_hook(d):
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '0%')
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            
            download_progress[chat_id] = {
                'status': 'downloading',
                'percent': percent,
                'speed': speed,
                'eta': eta,
                'last_update': time.time()
            }
        elif d['status'] == 'finished':
            download_progress[chat_id] = {
                'status': 'finished',
                'percent': '100%',
                'speed': 'N/A',
                'eta': '0s',
                'last_update': time.time()
            }
    
    common_opts = {
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': False,
        'progress_hooks': [progress_hook],
        'n_threads': 8,
        'retries': 5,
        'fragment_retries': 5,
        'cookiefile': './cookies.txt', # Use cookie file for authentication
    }
    
    if format_type == 'mp3':
        return {
            **common_opts,
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
            'extract_audio': True,
            'audio_format': 'mp3',
            'audio_quality': '0',
        }
    else:  # mp4
        return {
            **common_opts,
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'recode_video': 'mp4',
            'prefer_ffmpeg': True,
            'ffmpeg_location': 'ffmpeg', # Default path for Render
        }

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "🚀 *Welcome to Ultra-Fast YouTube Downloader Bot!*\n\n"
        "Send me a YouTube link and I'll download it for you at maximum speed!",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        "✨ *How to use:*\n"
        "1. Send me a YouTube URL\n"
        "2. Choose your preferred format (MP4/MP3)\n"
        "3. Watch real-time progress\n"
        "4. Receive your downloaded media!\n\n"
        "✅ All videos are supported.",
        parse_mode='Markdown'
    )

def create_progress_bar(percentage_str):
    """Create a visual progress bar."""
    try:
        percent_value = float(percentage_str.strip('%').split('.')[0])
    except (ValueError, IndexError):
        percent_value = 0
        
    bars = 10
    filled_bars = int(percent_value / 10)
    empty_bars = bars - filled_bars
    
    progress_bar = "🟦" * filled_bars + "⬜" * empty_bars
    return f"`{progress_bar}` {percentage_str}"

async def progress_updater(context: ContextTypes.DEFAULT_TYPE):
    """Update progress messages for all active downloads."""
    for chat_id, progress_data in list(download_progress.items()):
        try:
            if time.time() - progress_data.get('last_update', 0) > 300:
                del download_progress[chat_id]
                continue
            
            if progress_data['status'] == 'downloading':
                progress_bar = create_progress_bar(progress_data['percent'])
                status_text = (
                    f"📥 *Downloading...*\n\n"
                    f"{progress_bar}\n"
                    f"**Speed:** {progress_data['speed']}\n"
                    f"**ETA:** {progress_data['eta']}\n\n"
                    f"`Please wait, processing at maximum speed...`"
                )
                
                if chat_id in user_data and 'progress_message_id' in user_data[chat_id]:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=user_data[chat_id]['progress_message_id'],
                        text=status_text,
                        parse_mode='Markdown'
                    )
            
        except Exception as e:
            logger.error(f"Progress update error: {e}")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the YouTube URL sent by user."""
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    
    youtube_regex = r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/.+'
    if not re.match(youtube_regex, url):
        await update.message.reply_text(
            "❌ *Invalid YouTube URL!*\nPlease send a valid YouTube link.",
            parse_mode='Markdown'
        )
        return
    
    # Store user data
    user_data[chat_id] = {
        'url': url,
        'user_id': update.message.from_user.id,
        'chat_id': chat_id
    }
    
    # Ask for format choice
    keyboard = [
        [InlineKeyboardButton("🎥 MP4 Video", callback_data='format_mp4')],
        [InlineKeyboardButton("🎵 MP3 Audio", callback_data='format_mp3')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await update.message.reply_text(
        "🌌 *URL received!* Choose your desired format:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    user_data[chat_id]['format_message_id'] = message.message_id

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses."""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    data = query.data
    
    if data.startswith('format_'):
        if chat_id not in user_data:
            await query.edit_message_text("❌ Session expired. Please send the URL again.")
            return
            
        format_type = data.split('_')[1]
        user_data[chat_id]['format'] = format_type
        
        progress_message = await query.message.reply_text(
            "⚡ *Initializing ultra-fast download...*\n\n"
            "🚀 Preparing multi-threaded download...",
            parse_mode='Markdown'
        )
        
        user_data[chat_id]['progress_message_id'] = progress_message.message_id
        
        await download_media(context, chat_id)

async def download_media(context, chat_id):
    """Download the media with real progress tracking."""
    try:
        if chat_id not in user_data:
            return
            
        url = user_data[chat_id]['url']
        format_type = user_data[chat_id]['format']
        progress_message_id = user_data[chat_id]['progress_message_id']
        
        download_progress[chat_id] = {
            'status': 'starting',
            'percent': '0%',
            'speed': 'N/A',
            'eta': 'N/A',
            'last_update': time.time()
        }
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_template = os.path.join(tmp_dir, '%(title).100s.%(ext)s')
            
            def blocking_download():
                ydl_opts = get_ydl_opts(format_type, output_template, chat_id)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    title = info.get('title', 'download')
                    user_data[chat_id]['title'] = title
                    download_progress[chat_id]['status'] = 'downloading'
                    ydl.download([url])
                    return info
            
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_message_id,
                    text="📥 *Starting Download...*",
                    parse_mode='Markdown'
                )
                info = await asyncio.to_thread(blocking_download)
                title = user_data[chat_id]['title']
            except Exception as e:
                raise Exception(f"Download process failed: {e}")
            
            downloaded_file = None
            for file in os.listdir(tmp_dir):
                if file.endswith(('.mp4', '.mp3')):
                    downloaded_file = os.path.join(tmp_dir, file)
                    break
            
            if not downloaded_file or os.path.getsize(downloaded_file) == 0:
                raise Exception("Downloaded file not found or is empty.")
            
            # --- UPLOAD STAGE ---
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_message_id,
                text="📤 *Uploading to Telegram...*\n\n"
                     "✅ Download completed successfully!",
                parse_mode='Markdown'
            )
            
            file_size = os.path.getsize(downloaded_file)
            caption = f"🎥 {title}" if format_type == 'mp4' else f"🎵 {title}"
            
            try:
                if format_type == 'mp3':
                    await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=open(downloaded_file, 'rb'),
                        caption=caption,
                        parse_mode='Markdown',
                        title=title[:64],
                        performer="YouTube"
                    )
                else:
                    if file_size > 2000 * 1024 * 1024:
                        raise Exception("File too large for Telegram (2GB limit).")
                        
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=open(downloaded_file, 'rb'),
                        caption=caption,
                        supports_streaming=True,
                        parse_mode='Markdown',
                        width=info.get('width', 1280),
                        height=info.get('height', 720),
                        duration=info.get('duration', 0)
                    )
            except Exception as e:
                raise Exception(f"Upload to Telegram failed: {e}")
            
            await context.bot.delete_message(chat_id=chat_id, message_id=progress_message_id)
            
            keyboard = [
                [InlineKeyboardButton("🔄 Download Another", callback_data='another_download')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ *Download Complete!*\n\n"
                     f"**Title:** {title}\n"
                     f"**Format:** {format_type.upper()}\n"
                     f"**Size:** {file_size // (1024*1024)}MB",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
                
    except Exception as e:
        logger.error(f"Download/Upload error: {e}")
        
        error_msg = (
            "❌ *Download/Upload Failed!*\n\n"
            f"Reason: `{str(e)[:100]}`\n\n"
            "Please try a different video or try again later."
        )
        
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=user_data[chat_id]['progress_message_id'],
                text=error_msg,
                parse_mode='Markdown'
            )
        except:
            await context.bot.send_message(
                chat_id=chat_id,
                text=error_msg,
                parse_mode='Markdown'
            )
    finally:
        if chat_id in download_progress:
            del download_progress[chat_id]

async def another_download_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the 'download another' button press."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        text="✨ *Send me another YouTube URL*",
        parse_mode='Markdown'
    )

def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable not set. Please set it to your bot's token.")
        sys.exit(1)
        
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(button_handler, pattern='^format_'))
    application.add_handler(CallbackQueryHandler(another_download_handler, pattern='^another_download'))

    job_queue = application.job_queue
    job_queue.run_repeating(progress_updater, interval=3, first=5)

    logger.info("Bot starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
