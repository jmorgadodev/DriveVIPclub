import asyncio
import json
import logging
import os
import random
import threading
import urllib.request
import urllib.parse
from datetime import datetime, time, timedelta
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import socket
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports import zoneinfo as ZoneInfo

from telegram import Update, InputFile
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    filters,
)
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build

logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO,
)
logging.getLogger('httpx').setLevel(logging.WARNING)

from config import (
    TELEGRAM_BOT_TOKEN,
    PUBLIC_GROUP_ID,
    VIP_GROUP_ID,
    CHANNEL_ID,
    ADMIN_USERNAME,
    GOOGLE_SHEET_ID,
    GOOGLE_SERVICE_ACCOUNT,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    GOOGLE_DRIVE_OAUTH_TOKEN_JSON,
    MENSAJES_SHEET_RANGE,
    MP_ACCESS_TOKEN,
    DRIVE_FOLDER_ID,
    LISTADO_SHEET_ID,
)
from mensajes import FALLBACK

GROUP_LINK = "https://t.me/+-1gS1EfQMLNmMjdh"
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

MENSAJES = {}
STATS = {}
PENDING_GMAIL = {}
PROCESSED_PAYMENTS = set()

_SHEETS_SERVICE = None
_DRIVE_SERVICE = None
_SHEETS_LOCK = threading.Lock()
_DRIVE_LOCK = threading.Lock()
_SHEETS_API_LOCK = threading.Lock()
_DRIVE_API_LOCK = threading.Lock()
_GOOGLE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='google-api')
_PAYMENT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mercadopago')

WELCOME_IMAGE_URL = "https://raw.githubusercontent.com/jmorgadodev/DriveVIPclub/master/bienvenida.png"
LISTADO_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1K5lJLdMJfPH76JrV4uC9-QdDly8rLg8XAWxoecWAe3k/edit?gid=0#gid=0"
)
WELCOME_DELETE_SECONDS = 15 * 60
SCHEDULED_DELETE_SECONDS = 3 * 60 * 60
MAX_SAMPLE_VIDEO_BYTES = 20 * 1024 * 1024


def _execute_sheets(request):
    with _SHEETS_API_LOCK:
        return request.execute()


def _execute_drive(request):
    with _DRIVE_API_LOCK:
        return request.execute()


def _run_google_sync(func, *args):
    return _GOOGLE_EXECUTOR.submit(func, *args).result()

def _get_sheets_service():
    global _SHEETS_SERVICE
    if _SHEETS_SERVICE is None:
        with _SHEETS_LOCK:
            if _SHEETS_SERVICE is not None:
                return _SHEETS_SERVICE
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
            _SHEETS_SERVICE = build('sheets', 'v4', credentials=creds, cache_discovery=False)
    return _SHEETS_SERVICE

def _get_drive_service():
    global _DRIVE_SERVICE
    if _DRIVE_SERVICE is None:
        with _DRIVE_LOCK:
            if _DRIVE_SERVICE is not None:
                return _DRIVE_SERVICE
            if GOOGLE_DRIVE_OAUTH_TOKEN_JSON:
                import base64
                info = json.loads(base64.b64decode(GOOGLE_DRIVE_OAUTH_TOKEN_JSON))
                creds = Credentials.from_authorized_user_info(
                    info, scopes=['https://www.googleapis.com/auth/drive']
                )
            elif os.path.exists('.drive_token.json'):
                creds = Credentials.from_authorized_user_file(
                    '.drive_token.json', scopes=['https://www.googleapis.com/auth/drive']
                )
            elif GOOGLE_SERVICE_ACCOUNT_JSON:
                import base64
                info = json.loads(base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON))
                creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            elif GOOGLE_SERVICE_ACCOUNT:
                with open(GOOGLE_SERVICE_ACCOUNT) as f:
                    info = json.load(f)
                creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            else:
                creds = Credentials.from_authorized_user_file('token.json', scopes=SCOPES)
            _DRIVE_SERVICE = build('drive', 'v3', credentials=creds, cache_discovery=False)
    return _DRIVE_SERVICE

def _compartir_drive_sync(email: str) -> bool:
    try:
        drive = _get_drive_service()
        permissions = _execute_drive(drive.permissions().list(
            fileId=DRIVE_FOLDER_ID,
            fields='permissions(id,emailAddress)',
        )).get('permissions', [])
        if any(p.get('emailAddress') == email for p in permissions):
            return True
        _execute_drive(drive.permissions().create(
            fileId=DRIVE_FOLDER_ID,
            body={'type': 'user', 'role': 'reader', 'emailAddress': email},
            sendNotificationEmail=False
        ))
        logging.info(f"Drive compartido con {email}")
        return True
    except Exception as e:
        logging.error(f"Error compartiendo Drive con {email}: {e}")
        return False

def _revocar_drive_sync(email: str) -> bool:
    try:
        drive = _get_drive_service()
        perms = _execute_drive(drive.permissions().list(
            fileId=DRIVE_FOLDER_ID, fields='permissions(id,emailAddress)'
        ))
        for p in perms.get('permissions', []):
            if p.get('emailAddress') == email:
                _execute_drive(drive.permissions().delete(
                    fileId=DRIVE_FOLDER_ID, permissionId=p['id']
                ))
                logging.info(f"Acceso revocado a {email}")
                return True
        return False
    except Exception as e:
        logging.error(f"Error revocando Drive a {email}: {e}")
        return False

def _actualizar_sheet_sync(user_id: int, col_letter: str, value) -> None:
    try:
        service = _get_sheets_service()
        rows = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:A'
        ))
        vals = rows.get('values', [])
        for i, row in enumerate(vals):
            if row and row[0] == str(user_id):
                cell_range = f"'Hoja 1'!{col_letter}{i+1}"
                _execute_sheets(service.spreadsheets().values().update(
                    spreadsheetId=GOOGLE_SHEET_ID,
                    range=cell_range,
                    valueInputOption='RAW',
                    body={'values': [[value]]}
                ))
                return True
    except Exception as e:
        logging.error(f"Error actualizando Sheet para {user_id}: {e}")
    return False

def _parse_payment_ids(value):
    return {item for item in str(value or '').replace(',', '|').split('|') if item}


def _parse_sheet_date(value):
    for date_format in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(str(value), date_format).date()
        except ValueError:
            continue
    return None


def _cargar_estado_pagos_sync():
    service = _get_sheets_service()
    rows = _execute_sheets(service.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:K'
    )).get('values', [])

    if not rows or len(rows[0]) <= 10 or rows[0][10] != 'payment_ids':
        _execute_sheets(service.spreadsheets().values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="'Hoja 1'!K1",
            valueInputOption='RAW',
            body={'values': [['payment_ids']]},
        ))

    PROCESSED_PAYMENTS.clear()
    PENDING_GMAIL.clear()
    for row in rows[1:]:
        if not row:
            continue
        if len(row) > 10:
            PROCESSED_PAYMENTS.update(_parse_payment_ids(row[10]))
        user_id = row[0]
        email = row[2] if len(row) > 2 else ''
        plan = row[3] if len(row) > 3 else ''
        estado = row[6].strip().lower() if len(row) > 6 else ''
        if (
            user_id.isdigit()
            and plan
            and not email
            and estado not in ('vencido', 'acceso_revocado')
        ):
            PENDING_GMAIL[int(user_id)] = True

    while len(PROCESSED_PAYMENTS) > 2000:
        PROCESSED_PAYMENTS.pop()
    logging.info(
        f"Estado de pagos cargado: {len(PROCESSED_PAYMENTS)} pagos, "
        f"{len(PENDING_GMAIL)} Gmail pendientes"
    )


def _procesar_pago_sheet_sync(user_id, payment_id, plan, fecha, create_missing=True):
    service = _get_sheets_service()
    rows = _execute_sheets(service.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:K'
    )).get('values', [])
    payment_id = str(payment_id)

    for row_number, row in enumerate(rows[1:], start=2):
        if not row or row[0] != str(user_id):
            continue
        payment_ids = _parse_payment_ids(row[10] if len(row) > 10 else '')
        if payment_id in payment_ids:
            return {'status': 'duplicate'}

        tenia_plan = bool(len(row) > 3 and row[3])
        needs_email = not bool(len(row) > 2 and row[2])
        today = _parse_sheet_date(fecha)
        if not today:
            raise ValueError(f'Fecha de pago inválida: {fecha}')
        current_end = _parse_sheet_date(row[5] if len(row) > 5 else '')
        start_date = max(today, current_end) if current_end else today
        duration_days = 30 if plan == 'mensual' else 7
        expires_on = start_date + timedelta(days=duration_days)
        payment_ids.add(payment_id)
        persisted_ids = '|'.join(sorted(payment_ids)[-100:])
        _execute_sheets(service.spreadsheets().values().batchUpdate(
            spreadsheetId=GOOGLE_SHEET_ID,
            body={
                'valueInputOption': 'USER_ENTERED',
                'data': [
                    {
                        'range': f"'Hoja 1'!D{row_number}:E{row_number}",
                        'values': [[plan, start_date.isoformat()]],
                    },
                    {
                        'range': f"'Hoja 1'!K{row_number}",
                        'values': [[persisted_ids]],
                    },
                ],
            },
        ))
        return {
            'status': 'processed',
            'renewal': tenia_plan,
            'needs_email': needs_email,
            'expires_on': expires_on.isoformat(),
        }

    if create_missing:
        _registrar_usuario_sync(user_id, 'sin_username')
        return _procesar_pago_sheet_sync(
            user_id, payment_id, plan, fecha, create_missing=False
        )
    return {'status': 'missing_user'}

def _cargar_mensajes_sync():
    global MENSAJES
    try:
        service = _get_sheets_service()
        result = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID, range=MENSAJES_SHEET_RANGE
        ))
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

def _cargar_stats_listado_sync():
    global STATS
    if not LISTADO_SHEET_ID:
        return
    try:
        service = _get_sheets_service()
        result = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=LISTADO_SHEET_ID, range='A:F'
        ))
        rows = result.get('values', [])
        carpetas = max(0, len(rows) - 1)
        videos = 0
        fotos = 0
        for r in rows[1:]:
            if len(r) >= 3:
                try: videos += int(r[1].replace(',', ''))
                except: pass
                try: fotos += int(r[2].replace(',', ''))
                except: pass
        STATS = {
            'carpetas': f'{carpetas:,}'.replace(',', '.'),
            'videos': f'{videos:,}'.replace(',', '.'),
            'fotos': f'{fotos:,}'.replace(',', '.'),
            'tamano': f'+1 TB',
        }
        logging.info(f"Stats listado: {STATS['carpetas']} modelos, {STATS['videos']} videos, {STATS['fotos']} fotos")
    except Exception as e:
        logging.warning(f"No se pudieron cargar stats del listado: {e}")

def m(key):
    text = MENSAJES.get(key, FALLBACK.get(key, ''))
    for k, v in STATS.items():
        text = text.replace('{' + k + '}', v)
    return text

TZ = ZoneInfo("America/Santiago")

def _registrar_usuario_sync(user_id: int, username: str) -> None:
    try:
        service = _get_sheets_service()
        existing = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:A'
        )).get('values', [])
        if any(row and row[0] == str(user_id) for row in existing[1:]):
            return
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        row_num = len(existing) + 1
        _execute_sheets(service.spreadsheets().values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'Hoja 1!A{row_num}:E{row_num}',
            valueInputOption='USER_ENTERED',
            body={'values': [[str(user_id), username, '', '', '']]},
        ))
        _execute_sheets(service.spreadsheets().values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'Hoja 1!H{row_num}',
            valueInputOption='USER_ENTERED',
            body={'values': [[now]]},
        ))
        logging.info(f"Usuario registrado en Sheets: {username} ({user_id})")
    except FileNotFoundError:
        logging.warning("Archivo de credenciales no encontrado — registro en Sheets omitido.")
    except Exception as e:
        logging.error(f"Error registrando usuario en Sheets: {e}")

async def registrar_usuario(user_id: int, username: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_GOOGLE_EXECUTOR, partial(_registrar_usuario_sync, user_id, username))

async def eliminar_mensaje(msg, segundos: int) -> None:
    await asyncio.sleep(segundos)
    try:
        await msg.delete()
    except Exception as e:
        logging.warning(f"No se pudo eliminar mensaje {msg.message_id}: {e}")

def _bienvenida(user):
    name = user.mention_html() if user.username else user.first_name or 'Usuario'
    return m('bienvenida').format(user=name)

def _solo_privado(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.type == 'private'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _solo_privado(update):
            return
        user = update.effective_user
        if not user or not update.message:
            return
        msg = await _enviar_mensaje_bienvenida(
            context, update.effective_chat.id, _bienvenida(user)
        )
        context.application.create_task(
            eliminar_mensaje(msg, WELCOME_DELETE_SECONDS)
        )
    except Exception as e:
        logging.error(f"Error en start: {e}")

async def precios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("ℹ️ Escríbeme en privado para ver los precios: @DriveVIPclubBot")
        return
    await update.message.reply_text(m('precios'))

async def contenido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("ℹ️ Escríbeme en privado para ver el contenido: @DriveVIPclubBot")
        return
    await update.message.reply_text(m('contenido'))

async def contacto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("ℹ️ Escríbeme en privado para contactar al admin: @DriveVIPclubBot")
        return
    await update.message.reply_text(m('contacto'))

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("ℹ️ Escríbeme en privado para ver el listado: @DriveVIPclubBot")
        return
    await update.message.reply_text(m('lista'))

async def ventajas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("ℹ️ Escríbeme en privado para ver las ventajas: @DriveVIPclubBot")
        return
    text = m('ventajas')
    if os.path.exists('ventajas.png'):
        with open('ventajas.png', 'rb') as f:
            await update.message.reply_photo(photo=InputFile(f), caption=text)
    else:
        await update.message.reply_text(text)

async def _enviar_mensaje_bienvenida(context, chat_id, text):
    """Envía la foto por URL para no cargar el archivo en la RAM de Render."""
    return await context.bot.send_photo(
        chat_id=chat_id,
        photo=WELCOME_IMAGE_URL,
        caption=text,
        parse_mode='HTML',
    )

async def _bienvenida_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        cm = update.chat_member
        if not cm:
            return
        chat = cm.chat
        if chat.id not in (PUBLIC_GROUP_ID, VIP_GROUP_ID):
            return
        new = cm.new_chat_member
        old = cm.old_chat_member
        if not new or not old:
            return
        if new.user.is_bot:
            return
        old_status = old.status
        new_status = new.status
        logging.info(f"ChatMember update: user={new.user.id} old={old_status} new={new_status}")
        if new_status in ('left', 'kicked') and old_status != new_status:
            return
        if new_status in ('member', 'administrator') and old_status not in ('member', 'administrator'):
            user = new.user
            try:
                msg = await _enviar_mensaje_bienvenida(context, chat.id, _bienvenida(user))
                context.application.create_task(
                    eliminar_mensaje(msg, WELCOME_DELETE_SECONDS)
                )
            except Exception as e:
                logging.error(f"Error enviando bienvenida a {user.id} en {chat.id}: {e}")
    except Exception as e:
        logging.error(f"Error en _bienvenida_chat_member: {e}")


async def ocultar_salida(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or message.chat_id != PUBLIC_GROUP_ID or not message.left_chat_member:
        return
    try:
        await message.delete()
    except Exception as e:
        logging.warning(f"No se pudo ocultar la salida de {message.left_chat_member.id}: {e}")

def _crear_preferencia_sync(user_id: int, precio: int, plan: str):
    import requests as req
    pref = req.post('https://api.mercadopago.com/checkout/preferences', json={
        'items': [{
            'title': f'Membresía {plan} DriveVIPclub',
            'quantity': 1,
            'currency_id': 'CLP',
            'unit_price': precio,
        }],
        'external_reference': str(user_id),
        'notification_url': 'https://drivevipclub.onrender.com/',
        'back_urls': {'success': 'https://t.me/DriveVIPclubBot', 'failure': 'https://t.me/DriveVIPclubBot'},
        'auto_return': 'approved',
    }, headers={
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }, timeout=20)
    pref.raise_for_status()
    return pref.json()


async def _crear_preferencia(user_id: int, precio: int, plan: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _PAYMENT_EXECUTOR,
        partial(_crear_preferencia_sync, user_id, precio, plan),
    )

async def semanal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("⚠️ Para contratar un plan, escríbeme en privado: @DriveVIPclubBot")
        return
    user = update.effective_user
    if not MP_ACCESS_TOKEN:
        await update.message.reply_text("❌ Sistema de pago no disponible. Contacta al admin.")
        return
    try:
        await registrar_usuario(user.id, user.username or 'sin_username')
        data = await _crear_preferencia(user.id, 4990, 'Semanal')
        if 'init_point' in data:
            await update.message.reply_text(f"💎 Plan Semanal $4.990\n\n{data['init_point']}\n\n✅ Paga y el bot te pedirá tu Gmail.")
        else:
            await update.message.reply_text("❌ Error generando link. Contacta al admin.")
            logging.error(f"MP error: {data}")
    except Exception as e:
        await update.message.reply_text("❌ Error de conexión. Intenta más tarde.")
        logging.error(f"Error en /semanal: {e}")

async def mensual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("⚠️ Para contratar un plan, escríbeme en privado: @DriveVIPclubBot")
        return
    user = update.effective_user
    if not MP_ACCESS_TOKEN:
        await update.message.reply_text("❌ Sistema de pago no disponible. Contacta al admin.")
        return
    try:
        await registrar_usuario(user.id, user.username or 'sin_username')
        data = await _crear_preferencia(user.id, 8990, 'Mensual')
        if 'init_point' in data:
            await update.message.reply_text(f"💎 Plan Mensual $8.990\n\n{data['init_point']}\n\n✅ Paga y el bot te pedirá tu Gmail.")
        else:
            await update.message.reply_text("❌ Error generando link. Contacta al admin.")
            logging.error(f"MP error: {data}")
    except Exception as e:
        await update.message.reply_text("❌ Error de conexión. Intenta más tarde.")
        logging.error(f"Error en /mensual: {e}")

async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return
    if not _solo_privado(update):
        return
    text = update.message.text.strip()
    if user.id in PENDING_GMAIL:
        if '@' not in text or '.' not in text:
            await update.message.reply_text("❌ Eso no parece un Gmail válido. Envíame tu correo electrónico (ej: usuario@gmail.com)")
            return
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(_GOOGLE_EXECUTOR, _compartir_drive_sync, text)
        if not ok:
            await update.message.reply_text("❌ Error compartiendo el Drive. Contacta al admin.")
            return
        saved = await loop.run_in_executor(
            _GOOGLE_EXECUTOR, _actualizar_sheet_sync, user.id, 'C', text
        )
        if not saved:
            await update.message.reply_text(
                "⚠️ El acceso fue concedido, pero no pude guardar tu Gmail. "
                "Envíamelo nuevamente en unos minutos."
            )
            return
        del PENDING_GMAIL[user.id]
        caption = (
            f"✅ Acceso concedido a {text}\n\n"
            "Revisa DRIVE > COMPARTIDOS CONMIGO (no llega email).\n"
            f"Link directo: https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID}\n"
            "¡Disfruta!"
        )
        if os.path.exists('demo_drive.png'):
            with open('demo_drive.png', 'rb') as f:
                await update.message.reply_photo(photo=InputFile(f), caption=caption)
        else:
            await update.message.reply_text(caption)

async def mensaje_automatico(context: ContextTypes.DEFAULT_TYPE) -> None:
    key = context.job.data
    text = m(key)
    can_have_image = key in ('auto_08', 'auto_12', 'auto_16', 'auto_20')
    last_img = context.bot_data.get('last_promo_img', 0.0)
    now = datetime.now().timestamp()
    enough_time = (now - last_img) >= 14400
    try:
        if can_have_image and enough_time and os.path.exists('recordatorio.png') and random.random() < 0.5:
            try:
                with open('recordatorio.png', 'rb') as f:
                    message = await context.bot.send_photo(
                        chat_id=PUBLIC_GROUP_ID,
                        photo=InputFile(f),
                        caption=text,
                    )
                context.bot_data['last_promo_img'] = now
            except Exception:
                message = await context.bot.send_message(
                    chat_id=PUBLIC_GROUP_ID,
                    text=text,
                )
        else:
            message = await context.bot.send_message(
                chat_id=PUBLIC_GROUP_ID,
                text=text,
            )
        context.application.create_task(
            eliminar_mensaje(message, SCHEDULED_DELETE_SECONDS)
        )
        pm_ids = context.bot_data.setdefault('promo_message_ids', set())
        pm_ids.add(message.message_id)
        _trim_set(pm_ids, 500)
    except Exception as e:
        logging.error(f"Error enviando mensaje automático: {e}")


async def mensaje_listado(context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📊 LISTADO COMPLETO DEL DRIVE\n\n"
        "Revisa directamente todo el contenido disponible antes de pagar:\n\n"
        f"✅ <a href=\"{LISTADO_URL}\">ABRIR LISTADO COMPLETO</a>\n\n"
        "Encontrarás cada modelo, video y foto detallados y actualizados."
    )
    try:
        message = await context.bot.send_message(
            chat_id=PUBLIC_GROUP_ID,
            text=text,
            parse_mode='HTML',
            disable_web_page_preview=True,
        )
        context.application.create_task(
            eliminar_mensaje(message, SCHEDULED_DELETE_SECONDS)
        )
    except Exception as e:
        logging.error(f"Error enviando mensaje del listado: {e}")


CANAL_TEXTS = [
    f"\u2728 {{carpetas}} modelos organizados A-Z en nuestro Drive.\n{{videos}} VIDEOS \u2022 {{fotos}} FOTOS\n\n\U0001F447 \u00danete al grupo: {GROUP_LINK}",
    f"\U0001F4E6 \u00bfListo para ver lo que tenemos?\n{{carpetas}} modelos \u2022 {{videos}} videos \u2022 {{fotos}} fotos\n\n\U0001F447 Ingresa al grupo: {GROUP_LINK}",
    f"\U0001F525 Drive actualizado esta semana\n{{videos}} VIDEOS en HD\n{{carpetas}} modelos\n\n\U0001F447 \u00bfQuieres entrar? {GROUP_LINK}",
    f"\U0001F4CA DATO: tenemos planilla DETALLADA con todo el contenido.\nVes EXACTAMENTE lo que hay antes de pagar.\n\n\U0001F447 Pide el link en el grupo: {GROUP_LINK}",
    f"\U0001F31F Desde $4.990 el acceso m\u00e1s completo.\nSin l\u00edmite de descargas, 24/7.\n\n\U0001F447 Compra aqu\u00ed: {GROUP_LINK}",
]

async def mensaje_canal(context: ContextTypes.DEFAULT_TYPE) -> None:
    idx = context.bot_data.get('canal_idx', 0)
    text = CANAL_TEXTS[idx % len(CANAL_TEXTS)]
    for k, v in STATS.items():
        text = text.replace('{' + k + '}', v)
    context.bot_data['canal_idx'] = idx + 1
    try:
        message = await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
        context.application.create_task(
            eliminar_mensaje(message, SCHEDULED_DELETE_SECONDS)
        )
    except Exception as e:
        logging.error(f"Error enviando mensaje al canal: {e}")

CAPTIONS_SAMPLES = [
    f"\U0001F4F7 Muestra real del Drive.\n{{carpetas}} modelos \u2022 {{videos}} videos \u2022 {{tamano}}\n\n\U0001F916 Suscríbete con @DriveVIPclubBot\n\U0001F4AC Atención directa: {ADMIN_USERNAME}",
    f"\U0001F525 Esto es solo una muestra.\nTenemos {{carpetas}} modelos organizados de la A a la Z.\n\n\U0001F916 Suscríbete con @DriveVIPclubBot\n\U0001F4AC Atención directa: {ADMIN_USERNAME}",
    f"\U0001F48E Contenido nuevo todas las semanas.\nAcceso 24/7 y descargas sin límites.\n\n\U0001F916 Suscríbete con @DriveVIPclubBot\n\U0001F4AC Atención directa: {ADMIN_USERNAME}",
    f"\U0001F4CA Revisa nombres y cantidades en /lista antes de pagar.\nTransparencia total sobre el contenido.\n\n\U0001F916 Suscríbete con @DriveVIPclubBot\n\U0001F4AC Atención directa: {ADMIN_USERNAME}",
    f"\U0001F31F Plan semanal $4.990 \u2022 Plan mensual $8.990\nPago seguro mediante MercadoPago.\n\n\U0001F916 Suscríbete con @DriveVIPclubBot\n\U0001F4AC Atención directa: {ADMIN_USERNAME}",
    f"\U0001F4E6 El Drive se actualiza todas las semanas.\nEncuentra cada carpeta rápidamente.\n\n\U0001F916 Suscríbete con @DriveVIPclubBot\n\U0001F4AC Atención directa: {ADMIN_USERNAME}",
]

def _list_folder_files(folder_id, fields="nextPageToken,files(id,name,size,mimeType)"):
    """Lista todos los archivos dentro de una carpeta (con paginación)."""
    drive = _get_drive_service()
    results = []
    pt = None
    while True:
        r = _execute_drive(drive.files().list(
            q=f"'{folder_id}' in parents",
            fields=fields,
            pageSize=200,
            pageToken=pt,
            orderBy="name"
        ))
        results.extend(r.get("files", []))
        pt = r.get("nextPageToken")
        if not pt:
            break
    return results

def _find_all_folders(name):
    """Encuentra TODAS las carpetas con un nombre dado accesibles por la SA."""
    drive = _get_drive_service()
    folders = []
    pt = None
    while True:
        r = _execute_drive(drive.files().list(
            q=f"name='{name}' and mimeType='application/vnd.google-apps.folder'",
            fields="nextPageToken,files(id)",
            pageSize=200,
            pageToken=pt
        ))
        folders.extend(r.get("files", []))
        pt = r.get("nextPageToken")
        if not pt:
            break
    return sorted(folders, key=lambda folder: folder['id'])

def _cache_drive_folders():
    """Lightweight: only cache folder ID lists, not file metadata."""
    logging.info("Scanning Drive folders...")
    fotos = _find_all_folders("Fotos")
    videos_folders = _find_all_folders("Videos")
    logging.info(f"Drive folders cached: {len(fotos)} Fotos, {len(videos_folders)} Videos")
    return fotos, videos_folders

async def _obtener_media_horaria(pool, media_type, loop, slot):
    """Elige una carpeta y archivo estables para cada bloque horario."""
    pool_size = len(pool)
    start_idx = slot % pool_size
    for offset in range(pool_size):
        idx = (start_idx + offset) % pool_size
        folder = pool[idx]
        files = await loop.run_in_executor(_GOOGLE_EXECUTOR, _list_folder_files, folder["id"])
        candidates = [f for f in files if media_type in f.get("mimeType", "")]
        if media_type == "video":
            candidates = [
                f for f in candidates
                if int(f.get("size", 0)) <= MAX_SAMPLE_VIDEO_BYTES
            ]
        if not candidates:
            continue
        candidates.sort(key=lambda f: f['id'])
        file_idx = (slot // pool_size) % len(candidates)
        return candidates[file_idx]
    return None

async def publicar_muestra(context: ContextTypes.DEFAULT_TYPE) -> None:
    fotos_folders = context.bot_data.get('fotos_folders')
    vids_folders = context.bot_data.get('videos_folders')
    if not fotos_folders:
        loop = asyncio.get_event_loop()
        fotos_folders, vids_folders = await loop.run_in_executor(_GOOGLE_EXECUTOR, _cache_drive_folders)
        if not fotos_folders:
            return
        context.bot_data['fotos_folders'] = fotos_folders
        context.bot_data['videos_folders'] = vids_folders
    loop = asyncio.get_event_loop()
    slot = int(datetime.now(TZ).timestamp() // 3600)
    # Tres videos distribuidos por cada diez publicaciones.
    use_video = vids_folders and slot % 10 in (2, 5, 8)
    chosen = None
    if use_video:
        chosen = await _obtener_media_horaria(vids_folders, 'video', loop, slot)
    if not chosen:
        chosen = await _obtener_media_horaria(fotos_folders, 'image', loop, slot)
    if not chosen:
        return
    caption = CAPTIONS_SAMPLES[slot % len(CAPTIONS_SAMPLES)]
    for k, v in STATS.items():
        caption = caption.replace('{' + k + '}', v)
    drive = _get_drive_service()
    from io import BytesIO
    is_vid = chosen.get('mimeType', '').startswith('video/')
    try:
        data = await loop.run_in_executor(
            _GOOGLE_EXECUTOR,
            lambda: _execute_drive(drive.files().get_media(fileId=chosen['id']))
        )
        if is_vid:
            msg = await context.bot.send_video(
                chat_id=PUBLIC_GROUP_ID,
                video=InputFile(BytesIO(data), filename="muestra.mp4"),
                caption=caption
            )
        else:
            msg = await context.bot.send_photo(
                chat_id=PUBLIC_GROUP_ID,
                photo=InputFile(BytesIO(data), filename="muestra.jpg"),
                caption=caption
            )
    except Exception as e:
        if not is_vid:
            logging.error(f"Error publicando muestra: {e}")
            return
        logging.warning(f"No se pudo publicar el video ({e}); enviando foto de respaldo")
        fallback = await _obtener_media_horaria(fotos_folders, 'image', loop, slot)
        if not fallback:
            return
        try:
            data = await loop.run_in_executor(
                _GOOGLE_EXECUTOR,
                lambda: _execute_drive(drive.files().get_media(fileId=fallback['id']))
            )
            msg = await context.bot.send_photo(
                chat_id=PUBLIC_GROUP_ID,
                photo=InputFile(BytesIO(data), filename="muestra.jpg"),
                caption=caption,
            )
        except Exception as fallback_error:
            logging.error(f"Error publicando foto de respaldo: {fallback_error}")
            return
    sample_ids = context.bot_data.setdefault('group_sample_ids', set())
    sample_ids.add(msg.message_id)
    promo_ids = context.bot_data.setdefault('promo_message_ids', set())
    promo_ids.add(msg.message_id)
    _trim_set(promo_ids, 500)
    logging.info("Muestra publicada en el grupo")


async def limpiar_muestras_grupo(context: ContextTypes.DEFAULT_TYPE) -> None:
    sample_ids = context.bot_data.get('group_sample_ids', set())
    deleted = 0
    for message_id in list(sample_ids):
        try:
            await context.bot.delete_message(
                chat_id=PUBLIC_GROUP_ID,
                message_id=message_id,
            )
            deleted += 1
        except Exception as e:
            logging.warning(f"No se pudo eliminar la muestra {message_id}: {e}")
    promo_ids = context.bot_data.get('promo_message_ids')
    if promo_ids:
        promo_ids.difference_update(sample_ids)
    sample_ids.clear()
    logging.info(f"Limpieza de muestras del grupo: {deleted} eliminadas")

async def reaccion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reaction = update.message_reaction
    if not reaction or reaction.chat.id != PUBLIC_GROUP_ID or not reaction.user:
        return
    if not reaction.new_reaction:
        return
    if reaction.message_id not in context.bot_data.get('promo_message_ids', set()):
        return

    user = reaction.user
    seen = context.bot_data.setdefault('reaction_contacts', set())
    key = (reaction.message_id, user.id)
    if key in seen:
        return
    seen.add(key)
    _trim_set(seen, 2000)

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text="👋 Vi tu reacción. ¿Quieres ver los planes? Usa /precios o escríbenos tu consulta.",
        )
    except Exception:
        await context.bot.send_message(
            chat_id=PUBLIC_GROUP_ID,
            reply_to_message_id=reaction.message_id,
            text=(
                f"{user.mention_html()} para escribirte primero abre "
                "<a href=\"https://t.me/DriveVIPclubBot?start=interes\">@DriveVIPclubBot</a> "
                "y presiona Iniciar."
            ),
            parse_mode='HTML',
        )

PORT = int(os.getenv('PORT', '10000'))

def _buscar_pagos_aprobados_sync():
    params = urllib.parse.urlencode({
        'status': 'approved',
        'sort': 'date_created',
        'criteria': 'desc',
        'limit': 20,
    })
    request = urllib.request.Request(
        f'https://api.mercadopago.com/v1/payments/search?{params}',
        headers={'Authorization': f'Bearer {MP_ACCESS_TOKEN}'},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read()).get('results', [])


async def _poll_payments(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not MP_ACCESS_TOKEN:
        return
    loop = asyncio.get_running_loop()
    try:
        payments = await loop.run_in_executor(
            _PAYMENT_EXECUTOR, _buscar_pagos_aprobados_sync
        )
    except Exception as e:
        logging.error(f"Error consultando pagos: {e}")
        return

    for payment in payments:
        payment_id = str(payment.get('id') or '')
        if not payment_id or payment_id in PROCESSED_PAYMENTS:
            continue
        user_id_str = str(payment.get('external_reference') or '')
        if not user_id_str.isdigit():
            continue
        user_id = int(user_id_str)
        plan = 'mensual' if float(payment.get('transaction_amount', 0)) >= 8000 else 'semanal'
        hoy = datetime.now(TZ).date().isoformat()

        try:
            result = await loop.run_in_executor(
                _GOOGLE_EXECUTOR,
                _procesar_pago_sheet_sync,
                user_id,
                payment_id,
                plan,
                hoy,
            )
        except Exception as e:
            logging.error(f"Error guardando pago {payment_id}: {e}")
            continue

        if result['status'] == 'missing_user':
            logging.error(f"Pago {payment_id} sin usuario registrable: {user_id}")
            continue
        PROCESSED_PAYMENTS.add(payment_id)
        _trim_set(PROCESSED_PAYMENTS, 2000)
        if result['status'] == 'duplicate':
            continue

        renewal = result['renewal']
        needs_email = result['needs_email']
        expires_on = result['expires_on']
        logging.info(
            f"Pago aprobado para usuario {user_id}, plan {plan} "
            f"(renovacion={renewal})"
        )
        try:
            if not needs_email:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"✅ ¡Pago recibido! Tu membresía {plan} se ha extendido "
                        f"hasta {expires_on}. ¡Disfruta!"
                    ),
                )
            else:
                PENDING_GMAIL[user_id] = True
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "✅ ¡Pago confirmado! Ahora envíame tu correo Gmail "
                        "para darte acceso al Drive."
                    ),
                )
        except Exception as e:
            logging.error(f"Pago {payment_id} guardado, pero no se pudo avisar: {e}")

def _trim_set(s, max_size=1000):
    while len(s) > max_size:
        s.pop()

def _start_http():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', PORT))
    s.listen(5)
    s.settimeout(1)
    logging.info(f"Health server on port {PORT}")
    while True:
        try:
            conn, _ = s.accept()
        except socket.timeout:
            continue
        except Exception:
            continue
        try:
            conn.settimeout(5)
            conn.recv(4096)
            conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

def _self_ping():
    url = 'https://drivevipclub.onrender.com/'
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10):
                pass
        except:
            pass
        threading.Event().wait(600)

async def verificar_vencidos(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not DRIVE_FOLDER_ID:
        return
    loop = asyncio.get_event_loop()
    try:
        service = await loop.run_in_executor(_GOOGLE_EXECUTOR, _get_sheets_service)
        rows = await loop.run_in_executor(
            _GOOGLE_EXECUTOR,
            lambda: _execute_sheets(service.spreadsheets().values().get(
                spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:I'
            ))
        )
        rows = rows.get('values', [])
        for row in rows[1:]:
            if len(row) < 7:
                continue
            user_id = row[0]
            estado = row[6] if len(row) > 6 else ''
            email = row[2] if len(row) > 2 else ''
            if estado == 'vencido' and email and '@' in email:
                ok = await loop.run_in_executor(_GOOGLE_EXECUTOR, _revocar_drive_sync, email)
                if ok:
                    await loop.run_in_executor(_GOOGLE_EXECUTOR, _actualizar_sheet_sync, user_id, 'G', 'acceso_revocado')
                    try:
                        await context.bot.send_message(chat_id=int(user_id), text="⚠️ Tu membresía ha vencido. El acceso al Drive fue revocado.")
                    except:
                        pass
            elif estado == 'activo':
                email = row[2] if len(row) > 2 else ''
                if not email and user_id.isdigit():
                    uid = int(user_id)
                    if uid not in PENDING_GMAIL:
                        PENDING_GMAIL[uid] = True
                        try:
                            await context.bot.send_message(chat_id=uid, text="📩 Tu pago está confirmado. Envíame tu correo Gmail para activar el acceso.")
                        except:
                            pass
    except Exception as e:
        logging.error(f"Error en verificar_vencidos: {e}")

async def verificar_proximos_vencer(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not DRIVE_FOLDER_ID:
        return
    loop = asyncio.get_event_loop()
    from datetime import timedelta
    try:
        service = await loop.run_in_executor(_GOOGLE_EXECUTOR, _get_sheets_service)
        rows = await loop.run_in_executor(
            _GOOGLE_EXECUTOR,
            lambda: _execute_sheets(service.spreadsheets().values().get(
                spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:I'
            ))
        )
        rows = rows.get('values', [])
        hoy = datetime.now().date()
        manana = hoy + timedelta(days=1)
        notified_key = f'pre_expiry_{hoy.isoformat()}'
        notified = context.bot_data.setdefault(notified_key, set())
        for row in rows[1:]:
            if len(row) < 7:
                continue
            user_id = row[0]
            estado = row[6] if len(row) > 6 else ''
            fecha_fin_str = row[5] if len(row) > 5 else ''
            if estado != 'activo' or not fecha_fin_str or not user_id.isdigit():
                continue
            try:
                for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d'):
                    try:
                        fecha_fin = datetime.strptime(fecha_fin_str, fmt).date()
                        break
                    except ValueError:
                        continue
                else:
                    continue
            except:
                continue
            if fecha_fin != manana:
                continue
            uid = int(user_id)
            if uid in notified:
                continue
            notified.add(uid)
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=(
                        "⏰ Tu membresía vence MAÑANA.\n\n"
                        "Renueva ahora y no pierdas el acceso:\n"
                        "💎 /semanal ($4.990) — 7 días\n"
                        "💎 /mensual ($8.990) — 30 días\n\n"
                        "Sigue todo igual, solo se extiende tu fecha."
                    )
                )
                logging.info(f"Aviso pre-vencimiento enviado a {uid}")
            except Exception as e:
                logging.warning(f"No se pudo avisar a {uid}: {e}")
    except Exception as e:
        logging.error(f"Error en verificar_proximos_vencer: {e}")

async def test_drive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        return
    msg = await update.message.reply_text("Probando conexion con Drive...")
    try:
        loop = asyncio.get_event_loop()
        fotos, vids = await loop.run_in_executor(_GOOGLE_EXECUTOR, _cache_drive_folders)
        total_imgs = 0
        total_vids = 0
        for f in fotos:
            files = await loop.run_in_executor(_GOOGLE_EXECUTOR, _list_folder_files, f["id"])
            total_imgs += sum(1 for x in files if "image" in x.get("mimeType", ""))
        for v in vids:
            files = await loop.run_in_executor(_GOOGLE_EXECUTOR, _list_folder_files, v["id"])
            total_vids += sum(
                1 for x in files
                if "video" in x.get("mimeType", "")
                and int(x.get("size", 0)) <= MAX_SAMPLE_VIDEO_BYTES
            )
        await msg.edit_text(
            f"Drive OK\n\nImagenes: {total_imgs}\n"
            f"Videos <=20MB: {total_vids}\n"
            f"Carpetas Fotos: {len(fotos)}\n"
            f"Carpetas Videos: {len(vids)}"
        )
    except Exception as e:
        await msg.edit_text(f"Error: {e}\n\nRevisa GOOGLE_SERVICE_ACCOUNT_JSON en Render")

def main() -> None:
    _run_google_sync(_cargar_mensajes_sync)
    _run_google_sync(_cargar_stats_listado_sync)
    _run_google_sync(_cargar_estado_pagos_sync)
    threading.Thread(target=_start_http, daemon=True).start()
    threading.Thread(target=_self_ping, daemon=True).start()
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .media_write_timeout(60)
        .build()
    )
    application.add_handler(CommandHandler("start",    start))
    application.add_handler(CommandHandler("precios",  precios))
    application.add_handler(CommandHandler("contenido", contenido))
    application.add_handler(CommandHandler("contacto", contacto))
    application.add_handler(CommandHandler("semanal",  semanal))
    application.add_handler(CommandHandler("mensual",  mensual))
    application.add_handler(CommandHandler("lista",    lista))
    application.add_handler(CommandHandler("ventajas", ventajas))
    application.add_handler(CommandHandler("testdrive", test_drive))
    application.add_handler(
        ChatMemberHandler(_bienvenida_chat_member, chat_member_types=ChatMemberHandler.CHAT_MEMBER)
    )
    application.add_handler(
        MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, ocultar_salida)
    )
    application.add_handler(MessageReactionHandler(reaccion))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje)
    )
    job_queue = application.job_queue
    job_queue.run_repeating(
        _poll_payments,
        interval=30,
        first=5,
        name='poll_payments',
    )
    job_queue.run_daily(verificar_vencidos, time=time(4, 0, tzinfo=TZ))
    job_queue.run_daily(verificar_proximos_vencer, time=time(10, 0, tzinfo=TZ))
    for hour in (10, 15, 20):
        job_queue.run_daily(mensaje_canal, time=time(hour, 0, tzinfo=TZ), name=f'canal_{hour}')
    for hour in range(24):
        job_queue.run_daily(
            publicar_muestra,
            time=time(hour, 5, tzinfo=TZ),
            name=f'muestra_{hour}',
        )
    job_queue.run_daily(
        limpiar_muestras_grupo,
        time=time(0, 0, tzinfo=TZ),
        name='limpieza_muestras_grupo',
    )
    async def refrescar_stats(context: ContextTypes.DEFAULT_TYPE) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_GOOGLE_EXECUTOR, _cargar_stats_listado_sync)
    job_queue.run_daily(refrescar_stats, time=time(6, 0, tzinfo=TZ))
    job_queue.run_daily(refrescar_stats, time=time(18, 0, tzinfo=TZ))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
