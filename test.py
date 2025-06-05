async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("TEST OK")

def main():
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.run_webhook(listen="0.0.0.0", port=10000)
