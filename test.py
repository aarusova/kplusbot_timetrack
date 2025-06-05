from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = "7635522928:AAEHZ8LkGtxuHAw87qaiBIstnhPLQq1HBbs"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот работает! ✅")

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    
    app.run_webhook(
        listen="0.0.0.0",
        port=10000,
        webhook_url=f"https://kplusbot-timetrack.onrender.com/{TOKEN}"
    )

if __name__ == "__main__":
    main()
