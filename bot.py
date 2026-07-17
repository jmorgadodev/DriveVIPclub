import asyncio
import json
import logging
import os
import random
import threading
from datetime import datetime, time
from functools import partial
import socket
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports import zoneinfo as ZoneInfo

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
    ADMIN_USERNAME,
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_RANGE,
    GOOGLE_SERVICE_ACCOUNT,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    MENSAJES_SHEET_RANGE,
)
from mensajes import FALLBACK

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

MENSAJES = {}

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

def _cargar_mensajes_sync():
    global MENSAJES
    try:
        service = _get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID, range=MENSAJES_SHEET_RANGE
        ).execute()
        rows = result.get('values', [])
        raw = {}
        for row in rows[1:]:
            if len(row) >= 2 and row[0].strip():
                raw[row[0].strip()] = row[1]
        MENSAJES = {k: v.replace('{admin}', ADMIN_USERNAME) for k, v in raw.items()}
        logging.info(f"Mensajes cargados desde Sheets: {list(MENSAJES.keys())}")
    except Exception as e:
        logging.warning(f"No se pudieron cargar mensajes desde Sheets ({e}), usando fallback.")
        MENSAJES = {k: v.replace('{admin}', ADMIN_USERNAME) for k, v in FALLBACK.items()}

def m(key):
    return MENSAJES.get(key, FALLBACK.get(key, ''))

TZ = ZoneInfo("America/Santiago")
OFFLINE_START = time(22, 0)
OFFLINE_END = time(8, 0)

def _is_offline():
    now = datetime.now(TZ).time()
    if OFFLINE_START <= now or now < OFFLINE_END:
        return True
    return False

def _registrar_usuario_sync(user_id: int, username: str) -> None:
    try:
        service = _get_sheets_service()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        values = [[user_id, username, '', '', '', '', '', now]]
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

def offline_filter(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if _is_offline():
            await update.message.reply_text(m('offline'))
            return
        await handler(update, context)
    return wrapper

def _bienvenida(user):
    name = user.mention_html() if user.username else user.first_name or 'Usuario'
    return m('bienvenida').format(user=name)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = await update.message.reply_text(_bienvenida(user), parse_mode='HTML')
    await registrar_usuario(user.id, user.username or 'sin_username')
    context.application.create_task(eliminar_mensaje(msg, 7200))

async def precios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(m('precios'))

async def contenido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(m('contenido'))

async def contacto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(m('contacto'))

async def nuevo_miembro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_offline():
        return
    for user in update.message.new_chat_members:
        if not user.is_bot:
            msg = await update.message.reply_text(_bienvenida(user), parse_mode='HTML')
            await registrar_usuario(user.id, user.username or 'sin_username')
            context.application.create_task(eliminar_mensaje(msg, 7200))

AUTO_KEYS = ['auto_4h', 'auto_noche', 'auto_finde']

async def mensaje_automatico(context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_offline():
        return
    idx = context.bot_data.get('auto_idx', 0)
    key = AUTO_KEYS[idx % len(AUTO_KEYS)]
    context.bot_data['auto_idx'] = idx + 1
    try:
        await context.bot.send_message(
            chat_id=PUBLIC_GROUP_ID,
            text=m(key),
        )
    except Exception as e:
        print(f"Error enviando mensaje automático: {e}")

PORT = int(os.getenv('PORT', '10000'))

def _start_http():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', PORT))
    s.listen(5)
    s.settimeout(1)
    logging.info(f"Health server on port {PORT}")
    while True:
        try:
            conn, _ = s.accept()
            conn.settimeout(5)
            data = conn.recv(1024)
            if data:
                conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
            conn.close()
        except socket.timeout:
            pass
        except Exception:
            pass

def main() -> None:
    _cargar_mensajes_sync()
    t = threading.Thread(target=_start_http, daemon=True)
    t.start()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start",    offline_filter(start)))
    application.add_handler(CommandHandler("precios",  offline_filter(precios)))
    application.add_handler(CommandHandler("contenido", offline_filter(contenido)))
    application.add_handler(CommandHandler("contacto", offline_filter(contacto)))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, nuevo_miembro)
    )
    job_queue = application.job_queue
    job_queue.run_repeating(mensaje_automatico, interval=14400, first=10)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
