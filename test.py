from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TELEGRAM_TOKEN')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Бот запущен!")

def main():
    # Явное создание приложения
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Обязательные обработчики
    application.add_handler(CommandHandler("start", start))
    
    # Вебхук конфигурация
    application.run_webhook(
        listen="0.0.0.0",
        port=10000,
        webhook_url=f"https://kplusbot-timetrack.onrender.com/{TOKEN}"
    )

if __name__ == '__main__':
    main()
