import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
from datetime import datetime, timedelta

# Настройки
TELEGRAM_TOKEN = "ВАШ_ТОКЕН_БОТА"
GOOGLE_SHEETS_CREDENTIALS = "credentials.json"  # JSON-ключ от Google API

# Подключение к Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_SHEETS_CREDENTIALS, scope)
client = gspread.authorize(creds)

# Хранилище для ссылок на таблицы (вместо БД используем словарь)
user_tables = {}

# Команда /set_table
def set_table(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    args = context.args

    if not args:
        update.message.reply_text("Укажите ссылку на Google Таблицу: /set_table <ССЫЛКА>")
        return

    try:
        spreadsheet_url = args[0]
        sheet = client.open_by_url(spreadsheet_url).sheet1
        user_tables[user_id] = spreadsheet_url  # Сохраняем ссылку
        update.message.reply_text("✅ Таблица привязана!")
    except Exception as e:
        update.message.reply_text(f"❌ Ошибка: {e}. Проверьте ссылку и доступ.")

# Проверка, есть ли таблица у пользователя
def get_user_sheet(user_id):
    if user_id not in user_tables:
        return None
    try:
        return client.open_by_url(user_tables[user_id]).sheet1
    except:
        return None

# Остальные функции (start, button_handler, handle_task_description...) остаются теми же,
# но везде заменяем `sheet` на `get_user_sheet(user_id)`.

# Пример изменения функции end_task:
def end_task(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id
    sheet = get_user_sheet(user_id)

    if not sheet:
        query.edit_message_text("❌ Таблица не привязана. Используйте /set_table <ССЫЛКА>")
        return

    # ...остальная логика записи в таблицу...

# Запуск бота
def main():
    updater = Updater(TELEGRAM_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("set_table", set_table, pass_args=True))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_task_description))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()