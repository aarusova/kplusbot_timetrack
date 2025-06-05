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
from flask import Flask, jsonify

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("TEST OK")

def main():
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.run_webhook(listen="0.0.0.0", port=10000)
