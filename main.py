import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Конфигурация
TELEGRAM_TOKEN = "7635522928:AAEHZ8LkGtxuHAw87qaiBIstnhPLQq1HBbs"
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Хранилище данных пользователей
user_sessions = {}

def start(update: Update, context: CallbackContext):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    user_sessions[user_id] = {'spreadsheet_id': None, 'current_task': None}
    
    update.message.reply_text(
        "Привет! Отправь мне ссылку на Google-таблицу для учета времени",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Как получить ссылку?", callback_data="help_link")]
        ])
    )

def handle_spreadsheet_link(update: Update, context: CallbackContext):
    """Обработчик ссылки на таблицу"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Извлекаем ID таблицы из ссылки
    if "docs.google.com/spreadsheets" in text:
        spreadsheet_id = text.split('/d/')[1].split('/')[0]
        user_sessions[user_id]['spreadsheet_id'] = spreadsheet_id
        
        update.message.reply_text(
            "Таблица подключена! Что вы хотите сделать?",
            reply_markup=main_menu_keyboard()
        )
    else:
        update.message.reply_text("Пожалуйста, отправьте корректную ссылку на Google-таблицу")

def main_menu_keyboard():
    """Клавиатура главного меню"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Начать задачу", callback_data="start_task")],
        [InlineKeyboardButton("Отчет за неделю", callback_data="weekly_report")],
        [InlineKeyboardButton("Отчет за месяц", callback_data="monthly_report")]
    ])

def start_task(update: Update, context: CallbackContext):
    """Начало новой задачи"""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Записываем время начала
    now = datetime.now()
    user_sessions[user_id]['current_task'] = {
        'date': now.strftime("%Y-%m-%d"),
        'start_time': now.strftime("%H:%M:%S"),
        'task': None,
        'tags': []
    }
    
    query.edit_message_text("Введите описание задачи:")
    return "TASK_DESCRIPTION"

def task_description(update: Update, context: CallbackContext):
    """Обработчик описания задачи"""
    user_id = update.effective_user.id
    user_sessions[user_id]['current_task']['task'] = update.message.text
    
    update.message.reply_text(
        "Задача сохранена. Выберите действие:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Добавить теги", callback_data="add_tags")],
            [InlineKeyboardButton("Завершить задачу", callback_data="end_task")]
        ])
    )
    return "TASK_ACTIONS"

def end_task(update: Update, context: CallbackContext):
    """Завершение задачи и запись в таблицу"""
    query = update.callback_query
    user_id = query.from_user.id
    task_data = user_sessions[user_id]['current_task']
    
    # Рассчитываем продолжительность
    end_time = datetime.now()
    start_time = datetime.strptime(task_data['start_time'], "%H:%M:%S")
    duration = end_time - start_time
    hours = round(duration.total_seconds() / 3600, 2)
    
    # Подготовка данных для записи
    row = [
        task_data['date'],
        task_data['start_time'],
        end_time.strftime("%H:%M:%S"),
        hours,
        ", ".join(task_data['tags']),
        task_data['task']
    ]
    
    # Запись в Google Sheets
    try:
        credentials = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        service = build('sheets', 'v4', credentials=credentials)
        
        sheet = service.spreadsheets()
        sheet.values().append(
            spreadsheetId=user_sessions[user_id]['spreadsheet_id'],
            range="A1:F1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]}
        ).execute()
        
        query.edit_message_text(f"✅ Задача завершена!\nЗатрачено времени: {hours} ч.")
    except Exception as e:
        query.edit_message_text(f"Ошибка при записи в таблицу: {str(e)}")
    
    return -1

def generate_report(update: Update, context: CallbackContext, days: int):
    """Генерация отчета"""
    query = update.callback_query
    user_id = query.from_user.id
    
    try:
        credentials = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        service = build('sheets', 'v4', credentials=credentials)
        
        # Получение данных из таблицы
        result = service.spreadsheets().values().get(
            spreadsheetId=user_sessions[user_id]['spreadsheet_id'],
            range="A:F"
        ).execute()
        
        rows = result.get('values', [])
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Обработка данных
        report_data = []
        total_hours = 0
        tags_summary = {}
        tasks_summary = {}
        
        for row in rows:
            if len(row) < 6:
                continue
            
            try:
                row_date = datetime.strptime(row[0], "%Y-%m-%d")
                if start_date <= row_date <= end_date:
                    report_data.append(row)
                    hours = float(row[3])
                    total_hours += hours
                    
                    # Группировка по тегам
                    tags = row[4].split(', ') if len(row) > 4 and row[4] else ["Без тега"]
                    for tag in tags:
                        tags_summary[tag] = tags_summary.get(tag, 0) + hours
                    
                    # Группировка по задачам
                    task = row[5] if len(row) > 5 else "Без названия"
                    tasks_summary[task] = tasks_summary.get(task, 0) + hours
            except:
                continue
        
        # Формирование отчета
        report = f"📊 Отчет за {days} дней:\n"
        report += f"Всего часов: {round(total_hours, 2)}\n\n"
        
        report += "🔖 По тегам:\n"
        for tag, hours in tags_summary.items():
            report += f"- {tag}: {round(hours, 2)} ч.\n"
        
        report += "\n📝 По задачам:\n"
        for task, hours in tasks_summary.items():
            report += f"- {task}: {round(hours, 2)} ч.\n"
        
        query.edit_message_text(report)
    except Exception as e:
        query.edit_message_text(f"Ошибка при генерации отчета: {str(e)}")

def weekly_report(update: Update, context: CallbackContext):
    generate_report(update, context, 7)

def monthly_report(update: Update, context: CallbackContext):
    generate_report(update, context, 30)

def main():
    """Запуск бота"""
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    # Обработчики команд
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_spreadsheet_link))
    
    # Обработчики кнопок
    dp.add_handler(CallbackQueryHandler(start_task, pattern="^start_task$"))
    dp.add_handler(CallbackQueryHandler(weekly_report, pattern="^weekly_report$"))
    dp.add_handler(CallbackQueryHandler(monthly_report, pattern="^monthly_report$"))
    dp.add_handler(CallbackQueryHandler(end_task, pattern="^end_task$"))
    
    # Обработчики состояний
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, task_description))
    
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
