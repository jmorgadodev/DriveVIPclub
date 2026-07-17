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
    VIP_GROUP_ID,
    ADMIN_USERNAME,
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_RANGE,
    GOOGLE_SERVICE_ACCOUNT,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    MENSAJES_SHEET_RANGE,
    MP_ACCESS_TOKEN,
    DRIVE_FOLDER_ID,
)
from mensajes import FALLBACK

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

MENSAJES = {}
PENDING_GMAIL = {}

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

def _get_drive_service():
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
    return build('drive', 'v3', credentials=creds)

def _compartir_drive_sync(email: str) -> bool:
    try:
        drive = _get_drive_service()
        drive.permissions().create(
            fileId=DRIVE_FOLDER_ID,
            body={'type': 'user', 'role': 'reader', 'emailAddress': email},
            sendNotificationEmail=False
        ).execute()
        logging.info(f"Drive compartido con {email}")
        return True
    except Exception as e:
        logging.error(f"Error compartiendo Drive con {email}: {e}")
        return False

def _revocar_drive_sync(email: str) -> bool:
    try:
        drive = _get_drive_service()
        perms = drive.permissions().list(fileId=DRIVE_FOLDER_ID, fields='permissions(id,emailAddress)').execute()
        for p in perms.get('permissions', []):
            if p.get('emailAddress') == email:
                drive.permissions().delete(fileId=DRIVE_FOLDER_ID, permissionId=p['id']).execute()
                logging.info(f"Acceso revocado a {email}")
                return True
        return False
    except Exception as e:
        logging.error(f"Error revocando Drive a {email}: {e}")
        return False

def _actualizar_sheet_sync(user_id: int, col_letter: str, value) -> None:
    try:
        service = _get_sheets_service()
        rows = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:A'
        ).execute()
        vals = rows.get('values', [])
        for i, row in enumerate(vals):
            if row and row[0] == str(user_id):
                cell_range = f"'Hoja 1'!{col_letter}{i+1}"
                service.spreadsheets().values().update(
                    spreadsheetId=GOOGLE_SHEET_ID,
                    range=cell_range,
                    valueInputOption='RAW',
                    body={'values': [[value]]}
                ).execute()
                return True
    except Exception as e:
        logging.error(f"Error actualizando Sheet para {user_id}: {e}")
    return False

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
    for user in update.message.new_chat_members:
        if not user.is_bot:
            msg = await update.message.reply_text(_bienvenida(user), parse_mode='HTML')
            await registrar_usuario(user.id, user.username or 'sin_username')
            context.application.create_task(eliminar_mensaje(msg, 7200))

async def comprar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not MP_ACCESS_TOKEN:
        await update.message.reply_text("❌ Sistema de pago no disponible. Contacta a {admin}.")
        return
    try:
        import requests as req
        pref = req.post('https://api.mercadopago.com/checkout/preferences', json={
            'items': [{
                'title': 'Membresía DriveVIPclub',
                'quantity': 1,
                'currency_id': 'CLP',
                'unit_price': 4990,
            }],
            'external_reference': str(user.id),
            'notification_url': 'https://drivevipclub.onrender.com/',
            'back_urls': {'success': 'https://t.me/DriveVIPclubBot', 'failure': 'https://t.me/DriveVIPclubBot'},
            'auto_return': 'approved',
        }, headers={'Authorization': f'Bearer {MP_ACCESS_TOKEN}', 'Content-Type': 'application/json'})
        data = pref.json()
        if 'init_point' in data:
            await update.message.reply_text(
                f"💎 Link de pago para {user.first_name or 'ti'}:\n\n{data['init_point']}\n\n"
                "✅ Paga y el bot te pedirá tu Gmail automáticamente."
            )
        else:
            await update.message.reply_text("❌ Error generando link. Contacta al admin.")
            logging.error(f"MP error: {data}")
    except Exception as e:
        await update.message.reply_text("❌ Error de conexión. Intenta más tarde.")
        logging.error(f"Error en /comprar: {e}")

async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if user.id in PENDING_GMAIL:
        if '@' not in text or '.' not in text:
            await update.message.reply_text("❌ Eso no parece un Gmail válido. Envíame tu correo electrónico (ej: usuario@gmail.com)")
            return
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, _compartir_drive_sync, text)
        await loop.run_in_executor(None, _actualizar_sheet_sync, user.id, 'C', text)
        if ok:
            del PENDING_GMAIL[user.id]
            await update.message.reply_text(
                f"✅ Acceso concedido a {text}\n"
                "Revisa tu Drive, la carpeta ya está compartida contigo. ¡Disfruta!"
            )
        else:
            await update.message.reply_text("❌ Error compartiendo el Drive. Contacta al admin.")
    elif update.message.chat.type == 'private':
        pass

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

def _procesar_webhook(data: bytes):
    try:
        body = data.split(b'\r\n\r\n', 1)[1] if b'\r\n\r\n' in data else b''
        raw = data.decode('utf-8', errors='replace')
        first_line = raw.split('\r\n')[0] if '\r\n' in raw else ''
        method = first_line.split(' ')[0] if ' ' in first_line else '' 
        path = first_line.split(' ')[1] if len(first_line.split(' ')) > 1 else ''
        if method != 'POST' or '/webhook' not in path:
            return
        # Respond 200 OK fast for IPN-style GET params too
        if raw.startswith('GET') and ('topic=payment' in raw or 'topic=merchant_order' in raw):
            pass
        import urllib.parse as up
        query = up.parse_qs(up.urlparse(raw.split('\r\n')[0].split(' ')[1] if '\r\n' in raw else '').query)
        payment_id = None
        if b'{"action"' in body or b'{"type"' in body:
            try:
                j = json.loads(body)
                payment_id = j.get('data', {}).get('id') or j.get('id')
            except:
                pass
        elif query.get('topic') == ['payment'] or query.get('topic') == ['merchant_order']:
            payment_id = query.get('id', [None])[0]
        if not payment_id:
            return
        threading.Thread(target=_procesar_pago, args=(payment_id,), daemon=True).start()
    except:
        pass

def _procesar_pago(payment_id):
    import urllib.request as ureq
    try:
        req = ureq.Request(
            f'https://api.mercadopago.com/v1/payments/{payment_id}',
            headers={'Authorization': f'Bearer {MP_ACCESS_TOKEN}'}
        )
        with ureq.urlopen(req, timeout=10) as resp:
            pay = json.loads(resp.read())
        if pay.get('status') != 'approved':
            return
        user_id_str = pay.get('external_reference', '')
        if not user_id_str or not user_id_str.isdigit():
            return
        user_id = int(user_id_str)
        plan = 'semanal'
        unit_price = pay.get('transaction_amount', 0)
        if unit_price and float(unit_price) >= 8000:
            plan = 'mensual'
        import datetime as dt
        hoy = dt.date.today().isoformat()
        _actualizar_sheet_sync(user_id, 'D', plan)
        _actualizar_sheet_sync(user_id, 'E', hoy)
        PENDING_GMAIL[user_id] = True
        logging.info(f"Pago aprobado para usuario {user_id}, plan {plan}")
        from telegram import Bot
        bot = Bot(TELEGRAM_BOT_TOKEN)
        bot.send_message(
            chat_id=user_id,
            text="✅ ¡Pago confirmado! Ahora envíame tu correo Gmail para darte acceso al Drive."
        )
    except Exception as e:
        logging.error(f"Error procesando pago {payment_id}: {e}")

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
            data = conn.recv(4096)
            if data:
                first = data.split(b'\r\n')[0].decode('utf-8', errors='replace')
                path = first.split(' ')[1] if len(first.split(' ')) > 1 else '/'
                if path.startswith('/webhook') or b'topic=payment' in data or b'data.id' in data:
                    conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
                    conn.close()
                    _procesar_webhook(data)
                else:
                    conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
                    conn.close()
        except socket.timeout:
            pass
        except Exception:
            pass

def _self_ping():
    import urllib.request
    url = 'https://drivevipclub.onrender.com/'
    while True:
        try:
            urllib.request.urlopen(url, timeout=10)
        except:
            pass
        threading.Event().wait(600)

async def verificar_vencidos(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not DRIVE_FOLDER_ID:
        return
    loop = asyncio.get_event_loop()
    try:
        service = await loop.run_in_executor(None, _get_sheets_service)
        rows = await loop.run_in_executor(
            None,
            lambda: service.spreadsheets().values().get(
                spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:I'
            ).execute()
        )
        rows = rows.get('values', [])
        for row in rows[1:]:
            if len(row) < 7:
                continue
            user_id = row[0]
            estado = row[6] if len(row) > 6 else ''
            email = row[2] if len(row) > 2 else ''
            if estado == 'vencido' and email and '@' in email:
                ok = await loop.run_in_executor(None, _revocar_drive_sync, email)
                if ok:
                    await loop.run_in_executor(None, _actualizar_sheet_sync, user_id, 'G', 'acceso_revocado')
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

def main() -> None:
    _cargar_mensajes_sync()
    t = threading.Thread(target=_start_http, daemon=True)
    t.start()
    threading.Thread(target=_self_ping, daemon=True).start()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start",    start))
    application.add_handler(CommandHandler("precios",  precios))
    application.add_handler(CommandHandler("contenido", contenido))
    application.add_handler(CommandHandler("contacto", contacto))
    application.add_handler(CommandHandler("comprar",  comprar))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, nuevo_miembro)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje)
    )
    job_queue = application.job_queue
    job_queue.run_repeating(mensaje_automatico, interval=14400, first=10)
    job_queue.run_repeating(verificar_vencidos, interval=1800, first=30)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
