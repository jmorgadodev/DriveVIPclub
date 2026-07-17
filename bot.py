import asyncio
import json
import logging
from datetime import datetime
from functools import partial

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build

logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO,
)

from config import (
    TELEGRAM_BOT_TOKEN,
    PUBLIC_GROUP_ID,
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_RANGE,
    GOOGLE_SERVICE_ACCOUNT,
    GOOGLE_SERVICE_ACCOUNT_JSON,
)
from mensajes import (
    MENSAJE_BIENVENIDA,
    MENSAJE_PRECIOS,
    MENSAJE_CONTENIDO,
    MENSAJE_CONTACTO,
    MENSAJE_AUTO_4H,
)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

def _get_sheets_service():
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        import base64
        info = json.loads(base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON))
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    elif GOOGLE_SERVICE_ACCOUNT:
        with open(GOOGLE_SERVICE_ACCOUNT) as f:
            info = json.load(f)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_authorized_user_file('token.json', scopes=SCOPES)
    return build('sheets', 'v4', credentials=creds)

def _registrar_usuario_sync(user_id: int, username: str) -> None:
    try:
        service = _get_sheets_service()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        values = [[user_id, username, '', 'pendiente', '', '', 'pendiente', now, now]]
        service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=GOOGLE_SHEET_RANGE,
            valueInputOption='RAW',
            body={'values': values},
        ).execute()
        logging.info(f"Usuario registrado en Sheets: {username} ({user_id})")
    except FileNotFoundError:
        logging.warning("Archivo de credenciales no encontrado — registro en Sheets omitido.")
    except Exception as e:
        logging.error(f"Error registrando usuario en Sheets: {e}")

async def registrar_usuario(user_id: int, username: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_registrar_usuario_sync, user_id, username))

async def eliminar_mensaje(msg, segundos: int) -> None:
    await asyncio.sleep(segundos)
    try:
        await msg.delete()
    except Exception:
        pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = await update.message.reply_text(MENSAJE_BIENVENIDA)
    await registrar_usuario(user.id, user.username or 'sin_username')
    context.application.create_task(eliminar_mensaje(msg, 7200))

async def precios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MENSAJE_PRECIOS)

async def contenido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MENSAJE_CONTENIDO)

async def contacto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MENSAJE_CONTACTO)

async def nuevo_miembro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    for user in update.message.new_chat_members:
        if not user.is_bot:
            msg = await update.message.reply_text(MENSAJE_BIENVENIDA)
            await registrar_usuario(user.id, user.username or 'sin_username')
            context.application.create_task(eliminar_mensaje(msg, 7200))

async def mensaje_automatico(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await context.bot.send_message(
            chat_id=PUBLIC_GROUP_ID,
            text=MENSAJE_AUTO_4H,
        )
    except Exception as e:
        print(f"Error enviando mensaje automático: {e}")

def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start",    start))
    application.add_handler(CommandHandler("precios",  precios))
    application.add_handler(CommandHandler("contenido", contenido))
    application.add_handler(CommandHandler("contacto", contacto))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, nuevo_miembro)
    )
    job_queue = application.job_queue
    job_queue.run_repeating(mensaje_automatico, interval=14400, first=10)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
