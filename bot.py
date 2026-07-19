import asyncio
import json
import logging
import os
import random
import threading
import urllib.request
import urllib.parse
from datetime import datetime, time
from functools import partial
import socket
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports import zoneinfo as ZoneInfo

from telegram import Update, InputFile
from telegram.ext import (
    Application,
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

from config import (
    TELEGRAM_BOT_TOKEN,
    PUBLIC_GROUP_ID,
    VIP_GROUP_ID,
    CHANNEL_ID,
    ADMIN_USERNAME,
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_RANGE,
    GOOGLE_SERVICE_ACCOUNT,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    GOOGLE_DRIVE_OAUTH_TOKEN_JSON,
    MENSAJES_SHEET_RANGE,
    MP_ACCESS_TOKEN,
    DRIVE_FOLDER_ID,
    LISTADO_SHEET_ID,
)
from mensajes import FALLBACK

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

MENSAJES = {}
STATS = {}
PENDING_GMAIL = {}
PROCESSED_PAYMENTS = set()

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

def _tiene_plan_sync(user_id: int) -> bool:
    try:
        service = _get_sheets_service()
        rows = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:D'
        ).execute()
        for row in rows.get('values', []):
            if row and row[0] == str(user_id) and len(row) > 3 and row[3]:
                return True
    except:
        pass
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

def _cargar_stats_listado_sync():
    global STATS
    if not LISTADO_SHEET_ID:
        return
    try:
        service = _get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=LISTADO_SHEET_ID, range='A:F'
        ).execute()
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
        existing = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:A'
        ).execute().get('values', [])
        if any(row and row[0] == str(user_id) for row in existing[1:]):
            return
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

def _solo_privado(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.type == 'private'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        return
    user = update.effective_user
    text = _bienvenida(user)
    if os.path.exists('bienvenida.png'):
        with open('bienvenida.png', 'rb') as f:
            msg = await update.message.reply_photo(photo=InputFile(f), caption=text, parse_mode='HTML')
    else:
        msg = await update.message.reply_text(text, parse_mode='HTML')
    await registrar_usuario(user.id, user.username or 'sin_username')
    context.application.create_task(eliminar_mensaje(msg, 7200))

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

async def nuevo_miembro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    for user in update.message.new_chat_members:
        if not user.is_bot:
            text = _bienvenida(user)
            if os.path.exists('bienvenida.png'):
                with open('bienvenida.png', 'rb') as f:
                    msg = await update.message.reply_photo(photo=InputFile(f), caption=text, parse_mode='HTML')
            else:
                msg = await update.message.reply_text(text, parse_mode='HTML')
            await registrar_usuario(user.id, user.username or 'sin_username')
            context.application.create_task(eliminar_mensaje(msg, 7200))

async def _crear_preferencia(user_id: int, precio: int, plan: str):
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
    }, headers={'Authorization': f'Bearer {MP_ACCESS_TOKEN}', 'Content-Type': 'application/json'})
    return pref.json()

async def semanal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("⚠️ Para contratar un plan, escríbeme en privado: @DriveVIPclubBot")
        return
    user = update.effective_user
    if not MP_ACCESS_TOKEN:
        await update.message.reply_text("❌ Sistema de pago no disponible. Contacta al admin.")
        return
    try:
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
        ok = await loop.run_in_executor(None, _compartir_drive_sync, text)
        await loop.run_in_executor(None, _actualizar_sheet_sync, user.id, 'C', text)
        if ok:
            del PENDING_GMAIL[user.id]
            if os.path.exists('demo_drive.png'):
                with open('demo_drive.png', 'rb') as f:
                    await update.message.reply_photo(
                        photo=InputFile(f),
                        caption=(
                    f"✅ Acceso concedido a {text}\n\n"
                    "Revisa DRIVE > COMPARTIDOS CONMIGO (no llega email).\n"
                    f"Link directo: https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID}\n"
                    "¡Disfruta!"
                ))
        else:
            await update.message.reply_text("❌ Error compartiendo el Drive. Contacta al admin.")

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
                context.application.create_task(eliminar_mensaje(message, 14400))
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
        context.bot_data.setdefault('promo_message_ids', set()).add(message.message_id)
    except Exception as e:
        logging.error(f"Error enviando mensaje automático: {e}")

CANAL_TEXTS = [
    "\u2728 {carpetas} modelos organizados A-Z en nuestro Drive.\n{videos} VIDEOS \u2022 {fotos} FOTOS\n\n\U0001F447 Accede hoy: @DriveVIPclubBot",
    "\U0001F4E6 \u00bfListo para ver lo que tenemos?\n{carpetas} modelos \u2022 {videos} videos \u2022 {fotos} fotos\n\n\U0001F447 Habla con @DriveVIPclubBot",
    "\U0001F525 Drive actualizado esta semana\n{videos} VIDEOS en HD\n{carpetas} modelos\n\n\U0001F447 \u00bfQuieres entrar? @DriveVIPclubBot",
    "\U0001F4CA DATO: tenemos planilla DETALLADA con todo el contenido.\nVes EXACTAMENTE lo que hay antes de pagar.\n\n\U0001F447 Pide el link: @DriveVIPclubBot",
    "\U0001F31F Desde $4.990 el acceso m\u00e1s completo.\nSin l\u00edmite de descargas, 24/7.\n\n\U0001F447 Compra aqu\u00ed: @DriveVIPclubBot",
]

async def mensaje_canal(context: ContextTypes.DEFAULT_TYPE) -> None:
    idx = context.bot_data.get('canal_idx', 0)
    text = CANAL_TEXTS[idx % len(CANAL_TEXTS)]
    for k, v in STATS.items():
        text = text.replace('{' + k + '}', v)
    context.bot_data['canal_idx'] = idx + 1
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
    except Exception as e:
        logging.error(f"Error enviando mensaje al canal: {e}")

CAPTIONS_SAMPLES = [
    "\U0001F4F7 Sample exclusivo de nuestro contenido.\n{carpetas} modelos \u2022 {videos} videos \u2022 +1TB\n\n\U0001F447 Quieres ver mas? @DriveVIPclubBot",
    "\U0001F525 Esto es solo una muestra.\nTenemos {carpetas} modelos organizados A-Z.\n\n\U0001F447 Accede hoy: @DriveVIPclubBot",
    "\U0001F48E Contenido HD todas las semanas.\nSin limite de descargas, 24/7.\n\n\U0001F447 Habla con @DriveVIPclubBot",
    "\U0001F4CA Planilla detallada con todo el contenido.\nVes EXACTAMENTE lo que hay antes de pagar.\n\n\U0001F447 Info: @DriveVIPclubBot",
    "\U0001F31F Desde $4.990 el plan semanal.\nMercadoPago, acceso inmediato.\n\n\U0001F447 Compra aqui: @DriveVIPclubBot",
    "\U0001F4E6 Actualizaciones todas las semanas.\nContenido fresco sin costo extra.\n\n\U0001F447 Unete: @DriveVIPclubBot",
]

def _list_drive_media(mime_prefix):
    try:
        drive = _get_drive_service()
        all_files = []
        page_token = None
        while True:
            r = drive.files().list(
                q=f"mimeType contains '{mime_prefix}/'",
                fields="files(id,name,size,mimeType)",
                pageSize=200,
                pageToken=page_token,
                orderBy="name"
            ).execute()
            all_files.extend(r.get("files", []))
            page_token = r.get("nextPageToken")
            if not page_token:
                break
        return all_files
    except Exception as e:
        logging.error(f"Error listing drive {mime_prefix}s: {e}")
        return []

def _extract_model(name):
    parts = name.split(" DriveVIPclub")
    return parts[0].strip() if len(parts) > 1 else "Otros"

def _group_by_model(files):
    groups = {}
    for f in files:
        model = _extract_model(f.get("name", ""))
        groups.setdefault(model, []).append(f)
    return groups

def _cache_drive_media():
    imgs = _list_drive_media("image")
    vids = [v for v in _list_drive_media("video") if int(v.get("size", 0)) <= 10 * 1024 * 1024]
    imgs_by_model = _group_by_model(imgs)
    vids_by_model = _group_by_model(vids)
    models = sorted(imgs_by_model.keys())
    logging.info(f"Drive media cached: {len(imgs)} images, {len(vids)} videos from {len(models)} models")
    return imgs_by_model, vids_by_model, models

async def publicar_muestra(context: ContextTypes.DEFAULT_TYPE) -> None:
    imgs_by = context.bot_data.get('drive_images')
    vids_by = context.bot_data.get('drive_videos')
    models = context.bot_data.get('drive_models')
    if not imgs_by or not vids_by:
        loop = asyncio.get_event_loop()
        imgs_by, vids_by, models = await loop.run_in_executor(None, _cache_drive_media)
        if not imgs_by:
            return
        context.bot_data['drive_images'] = imgs_by
        context.bot_data['drive_videos'] = vids_by
        context.bot_data['drive_models'] = models
    # Rotate models
    model_idx = context.bot_data.setdefault('model_idx', 0)
    model_idx = model_idx % len(models)
    context.bot_data['model_idx'] = model_idx + 1
    current_model = models[model_idx]
    # Decide type: 70% image, 30% video (if videos available)
    use_video = vids_by and random.random() < 0.3
    pool_by = vids_by if use_video else imgs_by
    pool = pool_by.get(current_model, [])
    if not pool:
        for m in models:
            if m == current_model:
                continue
            pool = pool_by.get(m, [])
            if pool:
                break
    if not pool:
        all_files = [f for lst in pool_by.values() for f in lst]
        pool = all_files
    used = context.bot_data.setdefault('used_images', set())
    available = [f for f in pool if f['id'] not in used]
    if not available:
        used.clear()
        available = pool
    chosen = random.choice(available)
    used.add(chosen['id'])
    caption = random.choice(CAPTIONS_SAMPLES)
    for k, v in STATS.items():
        caption = caption.replace('{' + k + '}', v)
    try:
        drive = _get_drive_service()
        data = drive.files().get_media(fileId=chosen['id']).execute()
        from io import BytesIO
        is_vid = chosen.get('mimeType', '').startswith('video/')
        if is_vid:
            generic = f"muestra.{'mp4' if is_vid else 'jpg'}"
            if is_vid:
                msg = await context.bot.send_video(
                    chat_id=CHANNEL_ID,
                    video=InputFile(BytesIO(data), filename=generic),
                    caption=caption
                )
            else:
                msg = await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=InputFile(BytesIO(data), filename=generic),
                    caption=caption
                )
            context.bot_data.setdefault('today_posts', set()).add(msg.message_id)
            logging.info(f"Muestra publicada ({len(context.bot_data['today_posts'])} hoy)")
    except Exception as e:
        logging.error(f"Error publicando muestra: {e}")
        used.discard(chosen['id'])

async def limpiar_dia(context: ContextTypes.DEFAULT_TYPE) -> None:
    mids = context.bot_data.get('today_posts', set())
    if not mids:
        return
    deleted = 0
    for mid in mids:
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=mid)
            deleted += 1
        except Exception:
            pass
    mids.clear()
    logging.info(f"Limpieza diaria: {deleted} mensajes eliminados")

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

def _poll_payments():
    while True:
        try:
            params = urllib.parse.urlencode({"status": "approved", "sort": "date_created", "criteria": "desc", "limit": 20})
            req = urllib.request.Request(
                f'https://api.mercadopago.com/v1/payments/search?{params}',
                headers={'Authorization': f'Bearer {MP_ACCESS_TOKEN}'}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            for pay in data.get('results', []):
                pid = pay.get('id')
                if not pid or pid in PROCESSED_PAYMENTS:
                    continue
                user_id_str = pay.get('external_reference', '')
                if not user_id_str or not user_id_str.isdigit():
                    continue
                user_id = int(user_id_str)
                plan = 'semanal'
                if float(pay.get('transaction_amount', 0)) >= 8000:
                    plan = 'mensual'
                hoy = datetime.now().date().isoformat()
                tiene_plan = _tiene_plan_sync(user_id)
                _actualizar_sheet_sync(user_id, 'D', plan)
                _actualizar_sheet_sync(user_id, 'E', hoy)
                PROCESSED_PAYMENTS.add(pid)
                logging.info(f"Pago aprobado para usuario {user_id}, plan {plan} (renovacion={tiene_plan})")
                from telegram import Bot
                bot_instance = Bot(TELEGRAM_BOT_TOKEN)
                if not tiene_plan:
                    PENDING_GMAIL[user_id] = True
                    bot_instance.send_message(
                        chat_id=user_id,
                        text="✅ ¡Pago confirmado! Ahora envíame tu correo Gmail para darte acceso al Drive."
                    )
                else:
                    bot_instance.send_message(
                        chat_id=user_id,
                        text=f"✅ ¡Pago recibido! Tu membresía {plan} se ha extendido desde hoy ({hoy}). ¡Disfruta!"
                    )
        except Exception as e:
            logging.error(f"Error polling payments: {e}")
        threading.Event().wait(30)

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
            conn.settimeout(5)
            conn.recv(4096)
            conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
            conn.close()
        except socket.timeout:
            pass
        except Exception:
            pass

def _self_ping():
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

async def verificar_proximos_vencer(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not DRIVE_FOLDER_ID:
        return
    loop = asyncio.get_event_loop()
    from datetime import timedelta
    try:
        service = await loop.run_in_executor(None, _get_sheets_service)
        rows = await loop.run_in_executor(
            None,
            lambda: service.spreadsheets().values().get(
                spreadsheetId=GOOGLE_SHEET_ID, range='Hoja 1!A:I'
            ).execute()
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

def main() -> None:
    _cargar_mensajes_sync()
    _cargar_stats_listado_sync()
    threading.Thread(target=_start_http, daemon=True).start()
    threading.Thread(target=_self_ping, daemon=True).start()
    threading.Thread(target=_poll_payments, daemon=True).start()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start",    start))
    application.add_handler(CommandHandler("precios",  precios))
    application.add_handler(CommandHandler("contenido", contenido))
    application.add_handler(CommandHandler("contacto", contacto))
    application.add_handler(CommandHandler("semanal",  semanal))
    application.add_handler(CommandHandler("mensual",  mensual))
    application.add_handler(CommandHandler("lista",    lista))
    application.add_handler(CommandHandler("ventajas", ventajas))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, nuevo_miembro)
    )
    application.add_handler(MessageReactionHandler(reaccion))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje)
    )
    job_queue = application.job_queue
    for hour, key in ((0, 'auto_00'), (8, 'auto_08'), (12, 'auto_12'), (16, 'auto_16'), (20, 'auto_20')):
        job_queue.run_daily(
            mensaje_automatico,
            time=time(hour, 0, tzinfo=TZ),
            data=key,
            name=key,
        )
    job_queue.run_daily(verificar_vencidos, time=time(4, 0, tzinfo=TZ))
    job_queue.run_daily(verificar_proximos_vencer, time=time(10, 0, tzinfo=TZ))
    for hour in (9, 13, 18, 21):
        job_queue.run_daily(mensaje_canal, time=time(hour, 0, tzinfo=TZ), name=f'canal_{hour}')
    job_queue.run_repeating(publicar_muestra, interval=1800, first=10, name='muestra_30min')
    job_queue.run_daily(limpiar_dia, time=time(0, 0, tzinfo=TZ), name='limpieza_diaria')
    async def refrescar_stats(context: ContextTypes.DEFAULT_TYPE) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _cargar_stats_listado_sync)
    job_queue.run_daily(refrescar_stats, time=time(6, 0, tzinfo=TZ))
    job_queue.run_daily(refrescar_stats, time=time(18, 0, tzinfo=TZ))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
