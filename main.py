import os
import re
import logging
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
    ApplicationBuilder,
    TypeHandler
)
import json
from tempfile import NamedTemporaryFile


# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы для состояний разговора
START, TASK_DESCRIPTION, TASK_TAGS = range(3)

TOKEN = os.getenv('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("Токен не найден! Проверьте переменные окружения.")


# Настройки Google Sheets
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

initialized_sheets = {}

# Проверка наличия файла с учетными данными
def get_google_creds():
    creds_json = os.getenv('GOOGLE_CREDS_JSON')
    if not creds_json:
        raise ValueError("GOOGLE_CREDS_JSON не найден в переменных окружения!")
    
    try:
        creds_dict = json.loads(creds_json)
        return ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPES)
    except json.JSONDecodeError:
        raise ValueError("GOOGLE_CREDS_JSON содержит невалидный JSON")
    except Exception as e:
        raise ValueError(f"Ошибка загрузки Google creds: {str(e)}")

creds = get_google_creds()
SERVICE_ACCOUNT_EMAIL = creds.service_account_email
client = gspread.authorize(creds)

# Глобальные переменные для хранения состояния
user_sheets = {}  # {user_id: {'url': str, 'id': str}}
user_tasks = {}   # {user_id: {'start_time': datetime, 'description': str, 'tags': str}}

async def handle_webhook_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик входящих обновлений через вебхук"""
    await application.process_update(update)

def extract_spreadsheet_id(url):
    """Извлекает ID таблицы из различных форматов URL"""
    patterns = [
        r'/spreadsheets/d/([a-zA-Z0-9-_]+)',
        r'/d/([a-zA-Z0-9-_]+)',
        r'^([a-zA-Z0-9-_]+)$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None

def get_main_keyboard():
    """Возвращает основную клавиатуру"""
    keyboard = [
        [InlineKeyboardButton("Начать задачу", callback_data='task_start')],
        [InlineKeyboardButton("Закончить задачу", callback_data='task_end')],
        [InlineKeyboardButton("Отчет за неделю", callback_data='report_week')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def edit_message_without_reply_markup(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Редактирует сообщение, убирая клавиатуру"""
    if hasattr(update, 'callback_query'):
        await update.callback_query.edit_message_text(
            text=text,
            reply_markup=None
        )
    else:
        await update.message.reply_text(text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"⚡ Команда /start от {user.id} ({user.full_name})")
    
    await update.message.reply_text(
        "🔄 Бот активирован!\n"
        "Для работы с задачами используйте меню:",
        reply_markup=get_main_keyboard()
    )
    
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик отмены действий"""
    user_id = update.effective_user.id
    if user_id in user_tasks:
        del user_tasks[user_id]
    
    await update.message.reply_text(
        "Действие отменено.",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

async def handle_spreadsheet_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик ссылки/ID таблицы"""
    user_id = update.effective_user.id
    user_input = update.message.text.strip()
    
    try:
        # Извлекаем ID таблицы
        spreadsheet_id = extract_spreadsheet_id(user_input)
        if not spreadsheet_id:
            await update.message.reply_text(
                "❌ Не могу извлечь ID таблицы из вашей ссылки."
            )
            return START

        await update.message.reply_text("🔄 Проверяю доступ к таблице...")
        
        try:
            # Пробуем открыть таблицу
            spreadsheet = client.open_by_key(spreadsheet_id)
            worksheet = spreadsheet.sheet1
            
            # Проверяем, инициализирована ли уже таблица
            if spreadsheet_id not in initialized_sheets:
                headers = worksheet.row_values(1)
                required_headers = ['Дата', 'Начало', 'Конец', 'Часы', 'Задача', 'Теги']
                
                if not all(header in headers for header in required_headers):
                    # Если заголовков нет - создаем их
                    worksheet.insert_row(required_headers, index=1)
                    initialized_sheets[spreadsheet_id] = True
                else:
                    initialized_sheets[spreadsheet_id] = True
            
            # Если дошли сюда - доступ есть
            spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
            user_sheets[user_id] = {'url': spreadsheet_url, 'id': spreadsheet_id}
            
            await update.message.reply_text(
                f"✅ Успешно подключено к таблице: {spreadsheet.title}\n"
                f"🔗 {spreadsheet_url}\n\n"
                "Теперь вы можете начать работу с задачами!",
                reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END
            
        except gspread.exceptions.APIError as e:
            if "PERMISSION_DENIED" in str(e):
                await update.message.reply_text(
                    "🔐 Нет доступа к таблице. Необходимо:\n"
                    f"1. Откройте настройки доступа таблицы\n"
                    f"2. Добавьте email: {SERVICE_ACCOUNT_EMAIL}\n"
                    f"3. Установите права 'Редактор'"
                )
            else:
                await update.message.reply_text(f"🚨 Ошибка Google API: {str(e)}")
            return START
            
    except Exception as e:
        logger.error(f"Ошибка при обработке таблицы: {str(e)}", exc_info=True)
        await update.message.reply_text(
            f"⚠️ Критическая ошибка: {str(e)}\n"
            "Попробуйте позже или проверьте настройки доступа"
        )
        return START

async def task_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик начала задачи"""
    query = update.callback_query
    await query.answer()
    
    # Убираем кнопки из предыдущего сообщения
    await query.edit_message_reply_markup(reply_markup=None)
    
    user_id = update.effective_user.id
    if user_id not in user_sheets:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Сначала подключите Google таблицу через /start"
        )
        return ConversationHandler.END
    
    now = datetime.now()
    user_tasks[user_id] = {
        'start_time': now,
        'description': None,
        'tags': None
    }
    
    # Отправляем новое сообщение
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"⏱️ Задача начата в {now.strftime('%H:%M:%S')}\n"
             "Введите описание задачи:"
    )
    return TASK_DESCRIPTION


async def handle_task_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик описания задачи"""
    user_id = update.effective_user.id
    description = update.message.text
    
    if user_id not in user_tasks:
        await update.message.reply_text("❌ Нет активной задачи. Начните новую через /taskstart")
        return ConversationHandler.END
    
    user_tasks[user_id]['description'] = description
    
    await update.message.reply_text(
        "📝 Описание сохранено. Теперь введите теги через запятую:\n"
        "Пример: СОВхОС, интервью, логи, аналитика"
    )
    return TASK_TAGS


async def handle_task_tags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик тегов задачи"""
    user_id = update.effective_user.id
    tags = update.message.text
    
    if user_id not in user_tasks:
        await update.message.reply_text("❌ Нет активной задачи")
        return ConversationHandler.END
    
    user_tasks[user_id]['tags'] = tags
    
    # Сохраняем сообщение с кнопками подтверждения
    await update.message.reply_text(
        "Теги сохранены. Завершить задачу?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Да, завершить", callback_data='confirm_end')],
            [InlineKeyboardButton("Отмена", callback_data='cancel_end')]
        ])
    )
    return TASK_TAGS

async def save_task_with_tags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет задачу с тегами"""
    user_id = update.effective_user.id
    tags = update.message.text
    
    if user_id not in user_tasks:
        await update.message.reply_text("Нет активной задачи. Начните новую через /taskstart")
        return ConversationHandler.END
    
    user_tasks[user_id]['tags'] = tags
    return await end_task(update, context)

async def task_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик кнопки завершения задачи"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if user_id not in user_tasks:
        # 1. Убираем кнопки в исходном сообщении (текст остается)
        await query.edit_message_reply_markup(reply_markup=None)
        
        # 2. Отправляем новое сообщение с кнопками главного меню
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="ℹ️ Нет активной задачи для завершения",
            reply_markup=get_main_keyboard()  # Кнопки главного меню
        )
        return ConversationHandler.END
    
    # Остальная логика (если задача активна)
    if user_tasks[user_id]['description'] and not user_tasks[user_id]['tags']:
        await query.edit_message_text(
            "Введите теги через запятую:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Пропустить", callback_data='skip_tags')]
            ])
        )
        return TASK_TAGS
    
    return await end_task(update, context)
    
async def skip_tags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик пропуска тегов"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    if user_id in user_tasks:
        user_tasks[user_id]['tags'] = ''
    
    return await end_task(update, context)

async def end_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Финальное сохранение задачи"""
    user_id = update.effective_user.id
    
    if user_id not in user_tasks or not user_tasks[user_id]['description']:
        if hasattr(update, 'callback_query'):
            await update.callback_query.edit_message_text(
                "❌ Нет данных для сохранения",
                reply_markup=get_main_keyboard()
            )
        else:
            await update.message.reply_text(
                "❌ Нет данных для сохранения",
                reply_markup=get_main_keyboard()
            )
        return ConversationHandler.END
    
    task_data = user_tasks[user_id]
    end_time = datetime.now()
    start_time = task_data['start_time']
    duration = end_time - start_time
    hours = round(duration.total_seconds() / 3600, 2)
    
    try:
        spreadsheet_id = user_sheets[user_id]['id']
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.sheet1
        
        new_row = [
            start_time.strftime('%Y-%m-%d'),
            start_time.strftime('%H:%M:%S'),
            end_time.strftime('%H:%M:%S'),
            str(hours),
            task_data['description'],
            task_data.get('tags', '')
        ]
        
        worksheet.insert_row(new_row, index=2)
        
        message = (
            f"✅ Задача сохранена в таблицу!\n"
            f"📅 Дата: {new_row[0]}\n"
            f"⏱ Время: {new_row[1]} - {new_row[2]} ({hours} ч)\n"
            f"📝 Описание: {new_row[4]}"
        )
        
        if task_data.get('tags'):
            message += f"\n🏷 Теги: {new_row[5]}"
            
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}", exc_info=True)
        message = (
            f"❌ Ошибка при сохранении в таблицу!\n"
            f"Ошибка: {str(e)}"
        )
        
    finally:
        # Отправляем новое сообщение с результатом и кнопками
        if hasattr(update, 'callback_query'):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=message,
                reply_markup=get_main_keyboard()  # Добавляем кнопки главного меню
            )
            # Оставляем предыдущее сообщение нетронутым
        else:
            await update.message.reply_text(
                message,
                reply_markup=get_main_keyboard()  # Добавляем кнопки главного меню
            )
        
        if user_id in user_tasks:
            del user_tasks[user_id]
    
    return ConversationHandler.END

async def confirm_end_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение завершения задачи"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'confirm_end':
        # Редактируем сообщение с кнопками подтверждения
        await query.edit_message_text(
            query.message.text,  # Сохраняем оригинальный текст
            reply_markup=None  # Убираем кнопки подтверждения
        )
        return await end_task(update, context)
    else:
        # Возвращаемся в главное меню
        await query.edit_message_text(
            "Задача не завершена. Продолжайте работу!",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

async def report_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Генерирует отчет за неделю"""
    query = update.callback_query
    await query.answer()
    
    # Убираем кнопки из предыдущего сообщения
    await query.edit_message_reply_markup(reply_markup=None)
    
    user_id = update.effective_user.id
    if user_id not in user_sheets:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Сначала подключите Google таблицу через /start"
        )
        return
    
    try:
        # Получаем данные из таблицы
        spreadsheet_id = user_sheets[user_id]['id']
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.sheet1
        
        # Получаем все записи (пропускаем заголовок)
        records = worksheet.get_all_records()
        
        if not records:
            if hasattr(update, 'callback_query'):
                await update.callback_query.answer("В таблице нет данных для отчета", show_alert=True)
            else:
                await update.message.reply_text("📊 В таблице нет данных для отчета")
            return
        
        # Определяем период (последние 7 дней)
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=7)
        
        # Фильтруем записи за период
        filtered_data = []
        for row in records:
            try:
                row_date = datetime.strptime(row['Дата'], '%Y-%m-%d').date()
                if start_date <= row_date <= end_date:
                    filtered_data.append(row)
            except (ValueError, KeyError):
                continue
        
        if not filtered_data:
            if hasattr(update, 'callback_query'):
                await update.callback_query.answer("Нет данных за последнюю неделю", show_alert=True)
            else:
                await update.message.reply_text("📊 Нет данных за последнюю неделю")
            return
        
        # Считаем общее время
        total_hours = sum(float(row['Часы']) for row in filtered_data)
        
        # Собираем статистику по тегам
        tags_summary = {}
        for row in filtered_data:
            tags = [t.strip() for t in row['Теги'].split(',')] if row.get('Теги') else ['без тега']
            for tag in tags:
                tags_summary[tag] = tags_summary.get(tag, 0) + float(row['Часы'])
        
        # Собираем статистику по задачам
        tasks_summary = {}
        for row in filtered_data:
            task = row['Задача'][:30] + '...' if len(row['Задача']) > 30 else row['Задача']
            tasks_summary[task] = tasks_summary.get(task, 0) + float(row['Часы'])
        
        # Формируем отчет
        report_lines = [
            f"📊 Отчет за неделю ({start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')})",
            f"⏱ Всего времени: {total_hours:.1f} ч",
            "",
            "🏷 По тегам:"
        ]
        
        # Добавляем топ-5 тегов
        for tag, hours in sorted(tags_summary.items(), key=lambda x: x[1], reverse=True)[:5]:
            report_lines.append(f"• {tag}: {hours:.1f} ч")
        
        report_lines.extend(["", "📝 По задачам:"])
        
        # Добавляем топ-5 задач
        for task, hours in sorted(tasks_summary.items(), key=lambda x: x[1], reverse=True)[:5]:
            report_lines.append(f"• {task}: {hours:.1f} ч")
        
        # Формируем итоговый текст отчета
        report_text = "\n".join(report_lines)
        
        # Отправляем отчет как новое сообщение с кнопками главного меню
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=report_text,
            reply_markup=get_main_keyboard()
        )
            
    except Exception as e:
        logger.error(f"Ошибка формирования отчета: {e}", exc_info=True)
        error_msg = f"❌ Ошибка при формировании отчета: {str(e)}"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=error_msg,
            reply_markup=get_main_keyboard()
        )
           
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'task_start':
        await task_start(update, context)
    elif query.data == 'task_end':
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Обрабатываю завершение задачи..."
        )
        await task_end(update, context)
    elif query.data == 'report_week':
        await report_week(update, context)
    elif query.data == 'report_month':
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Формирую отчет за месяц..."
        )
        await report_week(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик ошибок"""
    logger.error("Ошибка в обработчике:", exc_info=context.error)
    
    if isinstance(update, Update):
        if update.message:
            await update.message.reply_text(
                "⚠️ Произошла ошибка. Напиши Насте."
            )

async def post_init(application: Application):
    await application.bot.set_webhook(f"https://kplusbot-timetrack.onrender.com/{TOKEN}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Получен /start от {update.effective_user.id}")  
    await update.message.reply_text("Бот запущен!")

def main() -> None:
    TOKEN = os.getenv('TELEGRAM_TOKEN')
    if not TOKEN:
        raise ValueError("Токен не найден!")

    # Используем ApplicationBuilder для webhook
    application = (
    ApplicationBuilder()
    .token(TOKEN)
    .post_init(post_init)
    .concurrent_updates(True)  # Важно для вебхуков
    .http_version("1.1")       # Совместимость с Render
    .get_updates_http_version("1.1")
    .build()
    )
    
    
    # Обработчик старта и подключения таблицы
    start_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            START: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_spreadsheet_url)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    # Обработчик задач
    task_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(task_start, pattern='^task_start$'),
        CallbackQueryHandler(task_end, pattern='^task_end$')
    ],
    states={
        TASK_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_task_description)],
        TASK_TAGS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_task_tags),
            CallbackQueryHandler(confirm_end_task, pattern='^(confirm_end|cancel_end)$'),
            CallbackQueryHandler(skip_tags, pattern='^skip_tags$')
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(start_conv_handler)
    application.add_handler(task_conv_handler)
    application.add_handler(CommandHandler('taskend', end_task))
    application.add_handler(CommandHandler('reportweek', report_week))
    application.add_handler(CommandHandler('reportmonth', report_week))  # Временная заглушка
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(error_handler)
    application.add_handler(TypeHandler(Update, handle_webhook_update))

    logger.info(f"🔧 Зарегистрировано обработчиков: {len(application.handlers)}")

    
    logger.info("Бот запускается...")
    application.run_webhook(
        listen="0.0.0.0",  # Слушаем все интерфейсы
        port=10000,        # Стандартный порт для Render
        webhook_url=f"https://kplusbot-timetrack.onrender.com/{TOKEN}",
    )

if __name__ == '__main__':
    main()
