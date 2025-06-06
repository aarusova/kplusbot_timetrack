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
    filters
)
import json
from tempfile import NamedTemporaryFile
import asyncio
import aiohttp

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы для состояний разговора
START, TASK_DESCRIPTION, TASK_TAGS = range(3)

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
        [InlineKeyboardButton("Отчёт за неделю", callback_data='report_week')]
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик команды /start"""
    await update.message.reply_text(
        "📊 Бот для учёта рабочего времени\n\n"
        f"1. Создайте Google таблицу\n"
        f"2. Предоставьте доступ сервисному аккаунту: {SERVICE_ACCOUNT_EMAIL}\n"
        f"3. Пришлите мне ссылку на таблицу или её ID\n\n"
        "Пример ссылки: https://docs.google.com/spreadsheets/d/ABC123/edit"
    )
    return START

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
                "❌ Не удаётся извлечь ID таблицы из вашей ссылки."
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
                    # Если заголовков нет - создаём их
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
                    "🔐 Нет доступа к таблице. Пожалуйста, убедитесь, что таблица открыта и предоставлен доступ сервисному аккаунту.\n"
                    f"Email аккаунта: {SERVICE_ACCOUNT_EMAIL}\n"
                    "Права доступа: Редактирование"
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
        # 1. Убираем кнопки в исходном сообщении (текст остаётся)
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

PING_INTERVAL_SECONDS = 120  # Интервал между пингами (2 минуты)

async def ping_server(application):
    """
    Периодически посылает запрос к вашему приложению, чтобы оно оставалось активным.
    """
    while True:
        async with aiohttp.ClientSession() as session:
            try:
                response = await session.get(f"{application.url}/healthcheck")
                print(f"Пинг отправлен. Статус ответа: {response.status}")
            except Exception as e:
                print(f"Ошибка при выполнении пинга: {e}")
            
        await asyncio.sleep(PING_INTERVAL_SECONDS)

# Запускаем пинг-сервер одновременно с ботом
def main() -> None:
    try:
        TOKEN = os.getenv('TELEGRAM_TOKEN')
        if not TOKEN:
            raise ValueError("Токен не найден! Проверьте переменные окружения.")

        application = Application.builder().token(TOKEN).build()

        # Начинаем регулярный пинг приложения
        asyncio.create_task(ping_server(application))

        # Все остальные ваши обработчики команд и сообщений остаются такими же.

        logger.info("Бот запущен успешно...")
        application.run_polling()

    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}", exc_info=True)
        raise
