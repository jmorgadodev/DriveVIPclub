import asyncio
import gc
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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
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
    FIXED_LIST_MESSAGE_ID,
    GOOGLE_SHEET_ID,
    GOOGLE_SERVICE_ACCOUNT,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    GOOGLE_DRIVE_OAUTH_TOKEN_JSON,
    MENSAJES_SHEET_RANGE,
    MP_ACCESS_TOKEN,
    DRIVE_FOLDER_ID,
    LISTADO_SHEET_ID,
    PAYPAL_CLIENT_ID,
    PAYPAL_CLIENT_SECRET,
    PAYPAL_LINK,
    DEMO_FOLDER_ID,
)
from mensajes import FALLBACK

GROUP_LINK = "https://t.me/+-1gS1EfQMLNmMjdh"
SHEET_VENTAS = "Ventas"
SHEET_DEMOS = "Demos"
SHEET_DASHBOARD = "Dashboard"
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

MENSAJES = {}
STATS = {}
PENDING_GMAIL = {}
PENDING_DEMO_GMAIL = {}
DEMO_EXPIRY = {}
PROCESSED_PAYMENTS = set()
PENDING_DELETIONS = {}

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
MAX_SALES_ROWS = 5000
MAX_PENDING_DELETIONS = 500
ADMIN_URL = f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"
SALES_MENU = InlineKeyboardMarkup([
    [
        InlineKeyboardButton(
            "Ver planes",
            url="https://t.me/DriveVIPclubBot?start=planes",
        ),
        InlineKeyboardButton(
            "Revisar listado",
            url="https://t.me/DriveVIPclubBot?start=lista",
        ),
    ],
    [
        InlineKeyboardButton("🇨🇱 MP /semanal", callback_data="cmd_semanal"),
        InlineKeyboardButton("🇨🇱 MP /mensual", callback_data="cmd_mensual"),
    ],
    [
        InlineKeyboardButton("🌍 PayPal $10 USD", url=PAYPAL_LINK),
    ],
    [InlineKeyboardButton("🎬 Demo gratis 10 min", url="https://t.me/DriveVIPclubBot?start=demo")],
    [InlineKeyboardButton("Hablar con el admin", url=ADMIN_URL)],
])


def _execute_sheets(request):
    try:
        with _SHEETS_API_LOCK:
            return request.execute()
    finally:
        del request
        gc.collect()


def _execute_drive(request):
    try:
        with _DRIVE_API_LOCK:
            return request.execute()
    finally:
        del request
        gc.collect()


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
            sendNotificationEmail=True,
            emailMessage='¡Bienvenido a DriveVIPclub! Haz recibido acceso a la carpeta exclusiva con todo el contenido. Disfruta.'

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

def _compartir_drive_demo_sync(email: str) -> bool:
    if not DEMO_FOLDER_ID:
        logging.error("DEMO_FOLDER_ID no configurado")
        return False
    try:
        drive = _get_drive_service()
        perms = _execute_drive(drive.permissions().list(
            fileId=DEMO_FOLDER_ID,
            fields='permissions(id,emailAddress)',
        )).get('permissions', [])
        if any(p.get('emailAddress') == email for p in perms):
            return True
        _execute_drive(drive.permissions().create(
            fileId=DEMO_FOLDER_ID,
            body={'type': 'user', 'role': 'reader', 'emailAddress': email},
            sendNotificationEmail=True,
            emailMessage='Has recibido acceso de prueba a DriveVIPclub. Revisa las carpetas y disfruta la muestra.',
        ))
        logging.info(f"Demo Drive compartido con {email}")
        return True
    except Exception as e:
        logging.error(f"Error compartiendo Demo Drive con {email}: {e}")
        return False


def _revocar_drive_demo_sync(email: str) -> bool:
    if not DEMO_FOLDER_ID:
        return False
    try:
        drive = _get_drive_service()
        perms = _execute_drive(drive.permissions().list(
            fileId=DEMO_FOLDER_ID, fields='permissions(id,emailAddress)'
        ))
        for p in perms.get('permissions', []):
            if p.get('emailAddress') == email:
                _execute_drive(drive.permissions().delete(
                    fileId=DEMO_FOLDER_ID, permissionId=p['id']
                ))
                logging.info(f"Demo Drive revocado a {email}")
                return True
        return False
    except Exception as e:
        logging.error(f"Error revocando Demo Drive a {email}: {e}")
        return False


def _inicializar_planilla_sync():
    try:
        service = _get_sheets_service()
        spreadsheet = _execute_sheets(service.spreadsheets().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            fields='sheets(properties(sheetId,title))',
        ))
        sheets = {s['properties']['title']: s['properties']['sheetId'] for s in spreadsheet.get('sheets', [])}

        requests = []

        # Rename Hoja 1 → Ventas if still exists
        if 'Hoja 1' in sheets and 'Ventas' not in sheets:
            requests.append({
                'updateSheetProperties': {
                    'properties': {'sheetId': sheets['Hoja 1'], 'title': 'Ventas'},
                    'fields': 'title',
                }
            })
            sheets['Ventas'] = sheets.pop('Hoja 1')

        # Create Demos sheet if missing
        if 'Demos' not in sheets:
            r = _execute_sheets(service.spreadsheets().batchUpdate(
                spreadsheetId=GOOGLE_SHEET_ID,
                body={'requests': [{
                    'addSheet': {
                        'properties': {'title': 'Demos'}
                    }
                }]},
            ))
            demo_sheet_id = r['replies'][0]['addSheet']['properties']['sheetId']
            # Header row
            _execute_sheets(service.spreadsheets().values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range="'Demos'!A1:F1",
                valueInputOption='RAW',
                body={'values': [['user_id', 'username', 'email', 'requested_at', 'expires_at', 'status']]},
            ))
            requests.append({
                'repeatCell': {
                    'range': {'sheetId': demo_sheet_id, 'startRowIndex': 0, 'endRowIndex': 1},
                    'cell': {
                        'userEnteredFormat': {
                            'backgroundColor': {'red': 0.2, 'green': 0.2, 'blue': 0.2},
                            'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                            'horizontalAlignment': 'CENTER',
                        }
                    },
                    'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)',
                }
            })
        else:
            demo_sheet_id = sheets['Demos']

        # Format Ventas header if not done
        if 'Ventas' in sheets:
            requests.append({
                'repeatCell': {
                    'range': {'sheetId': sheets['Ventas'], 'startRowIndex': 0, 'endRowIndex': 1},
                    'cell': {
                        'userEnteredFormat': {
                            'backgroundColor': {'red': 0.2, 'green': 0.2, 'blue': 0.2},
                            'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                            'horizontalAlignment': 'CENTER',
                        }
                    },
                    'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)',
                }
            })
            # Border for Ventas
            requests.append({
                'updateBorders': {
                    'range': {'sheetId': sheets['Ventas'], 'startRowIndex': 0, 'endRowIndex': 1},
                    'top': {'style': 'SOLID', 'width': 2, 'color': {'red': 0, 'green': 0, 'blue': 0}},
                    'bottom': {'style': 'SOLID', 'width': 2, 'color': {'red': 0, 'green': 0, 'blue': 0}},
                }
            })

        if requests:
            _execute_sheets(service.spreadsheets().batchUpdate(
                spreadsheetId=GOOGLE_SHEET_ID,
                body={'requests': requests},
            ))
        logging.info("Planilla inicializada: Ventas + Demos + Dashboard")
    except Exception as e:
        logging.warning(f"Error inicializando planilla: {e}")


def _guardar_demo_sync(user_id: int, username: str, email: str, expires_at: str, status: str = 'activo') -> bool:
    try:
        service = _get_sheets_service()
        _execute_sheets(service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="'Demos'!A:F",
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': [[
                str(user_id), username, email,
                datetime.now(TZ).isoformat(timespec='seconds'),
                expires_at, status,
            ]]},
        ))
        return True
    except Exception as e:
        logging.error(f"Error guardando demo: {e}")
        return False


def _actualizar_demo_status_sync(user_id: int, status: str) -> None:
    try:
        service = _get_sheets_service()
        rows = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="'Demos'!A:F",
        )).get('values', [])
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == str(user_id):
                _execute_sheets(service.spreadsheets().values().update(
                    spreadsheetId=GOOGLE_SHEET_ID,
                    range=f"'Demos'!F{i}",
                    valueInputOption='RAW',
                    body={'values': [[status]]},
                ))
                return
    except Exception as e:
        logging.error(f"Error actualizando status demo: {e}")


def _demo_ya_usada_sync(user_id: int) -> bool:
    try:
        service = _get_sheets_service()
        rows = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="'Demos'!A:F",
        )).get('values', [])
        for row in rows[1:]:
            if row and row[0] == str(user_id):
                return True
        return False
    except Exception as e:
        logging.error(f"Error consultando demo: {e}")
        return False


def _actualizar_sheet_sync(user_id: int, col_letter: str, value) -> None:
    try:
        service = _get_sheets_service()
        rows = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'Ventas'!A1:A{MAX_SALES_ROWS + 1}",
        ))
        vals = rows.get('values', [])
        for i, row in enumerate(vals):
            if row and row[0] == str(user_id):
                cell_range = f"'Ventas'!{col_letter}{i+1}"
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
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"'Ventas'!A1:K{MAX_SALES_ROWS + 1}",
    )).get('values', [])

    if not rows or len(rows[0]) <= 10 or rows[0][10] != 'payment_ids':
        _execute_sheets(service.spreadsheets().values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="'Ventas'!K1",
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
        notes = row[9].strip().lower() if len(row) > 9 else ''
        if (
            user_id.isdigit()
            and plan
            and not email
            and estado not in ('vencido', 'acceso_revocado')
            and notes != 'acceso_revocado'
        ):
            PENDING_GMAIL[int(user_id)] = True

    while len(PROCESSED_PAYMENTS) > 2000:
        PROCESSED_PAYMENTS.pop()
    logging.info(
        f"Estado de pagos cargado: {len(PROCESSED_PAYMENTS)} pagos, "
        f"{len(PENDING_GMAIL)} Gmail pendientes"
    )


def _procesar_pago_sheet_sync(
    user_id,
    payment_id,
    plan,
    fecha,
    username='',
    create_missing=True,
):
    service = _get_sheets_service()
    rows = _execute_sheets(service.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"'Ventas'!A1:K{MAX_SALES_ROWS + 1}",
    )).get('values', [])
    payment_id = str(payment_id)
    today = _parse_sheet_date(fecha)
    if not today:
        raise ValueError(f'Fecha de pago inválida: {fecha}')

    for row_number, row in enumerate(rows[1:], start=2):
        if not row or row[0] != str(user_id):
            continue
        payment_ids = _parse_payment_ids(row[10] if len(row) > 10 else '')
        if payment_id in payment_ids:
            return {'status': 'duplicate'}

        tenia_plan = bool(len(row) > 3 and row[3])
        needs_email = not bool(len(row) > 2 and row[2])
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
                        'range': f"'Ventas'!D{row_number}:E{row_number}",
                        'values': [[plan, start_date.isoformat()]],
                    },
                    {
                        'range': f"'Ventas'!K{row_number}",
                        'values': [[persisted_ids]],
                    },
                    {
                        'range': f"'Ventas'!I{row_number}",
                        'values': [['bot']],
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
        if len(rows) - 1 >= MAX_SALES_ROWS:
            raise RuntimeError('Ventas alcanzó el límite de configurado')
        row_number = max(2, len(rows) + 1)
        duration_days = 30 if plan == 'mensual' else 7
        expires_on = today + timedelta(days=duration_days)
        registered_at = datetime.now(TZ).isoformat(timespec='seconds')
        _execute_sheets(service.spreadsheets().values().batchUpdate(
            spreadsheetId=GOOGLE_SHEET_ID,
            body={
                'valueInputOption': 'USER_ENTERED',
                'data': [
                    {
                        'range': f"'Ventas'!A{row_number}:E{row_number}",
                        'values': [[
                            str(user_id),
                            username or 'sin_username',
                            '',
                            plan,
                            today.isoformat(),
                        ]],
                    },
                    {
                        'range': f"'Ventas'!H{row_number}:K{row_number}",
                        'values': [[registered_at, 'bot', '', payment_id]],
                    },
                ],
            },
        ))
        return {
            'status': 'processed',
            'renewal': False,
            'needs_email': True,
            'expires_on': expires_on.isoformat(),
        }
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

def _obtener_stats_listado_sync():
    if not LISTADO_SHEET_ID:
        return {}
    service = _get_sheets_service()
    result = _execute_sheets(service.spreadsheets().values().get(
        spreadsheetId=LISTADO_SHEET_ID, range='A:F'
    ))
    rows = result.get('values', [])
    carpetas = max(0, len(rows) - 1)
    videos = 0
    fotos = 0
    for row in rows[1:]:
        if len(row) >= 3:
            try:
                videos += int(row[1].replace(',', ''))
            except (TypeError, ValueError):
                pass
            try:
                fotos += int(row[2].replace(',', ''))
            except (TypeError, ValueError):
                pass
    return {
        'carpetas': f'{carpetas:,}'.replace(',', '.'),
        'videos': f'{videos:,}'.replace(',', '.'),
        'fotos': f'{fotos:,}'.replace(',', '.'),
        'tamano': '+1 TB',
    }


def _guardar_stats_cache_sync(stats):
    service = _get_sheets_service()
    rows = [['key', 'value']] + [[key, stats[key]] for key in (
        'carpetas', 'videos', 'fotos', 'tamano'
    )]
    rows.append(['updated_at', datetime.now(TZ).isoformat(timespec='seconds')])
    _execute_sheets(service.spreadsheets().values().update(
        spreadsheetId=GOOGLE_SHEET_ID,
        range='Estadisticas!A1:B6',
        valueInputOption='RAW',
        body={'values': rows},
    ))


def _actualizar_stats_semanales_sync():
    global STATS
    try:
        stats = _obtener_stats_listado_sync()
        if not stats:
            return False
        _guardar_stats_cache_sync(stats)
        STATS = stats
        logging.info(
            f"Stats semanales actualizados: {STATS['carpetas']} modelos, "
            f"{STATS['videos']} videos, {STATS['fotos']} fotos"
        )
        return True
    except Exception as e:
        logging.warning(f"No se pudieron actualizar stats semanales: {e}")
        return False


def _cargar_stats_cache_sync():
    global STATS
    try:
        service = _get_sheets_service()
        rows = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='Estadisticas!A1:B6',
        )).get('values', [])
        cached = {
            row[0]: row[1]
            for row in rows[1:]
            if len(row) >= 2 and row[0] in ('carpetas', 'videos', 'fotos', 'tamano')
        }
        if len(cached) != 4:
            return _actualizar_stats_semanales_sync()
        STATS = cached
        logging.info(
            f"Stats cacheados: {STATS['carpetas']} modelos, "
            f"{STATS['videos']} videos, {STATS['fotos']} fotos"
        )
        return True
    except Exception as e:
        logging.warning(f"No se pudieron cargar stats cacheados: {e}")
        return _actualizar_stats_semanales_sync()

def m(key):
    text = MENSAJES.get(key, FALLBACK.get(key, ''))
    for k, v in STATS.items():
        text = text.replace('{' + k + '}', v)
    return text

TZ = ZoneInfo("America/Santiago")


def _registrar_evento_sync(user_id: int, username: str, event: str, source: str) -> None:
    try:
        service = _get_sheets_service()
        _execute_sheets(service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='Embudo!A:E',
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': [[
                datetime.now(TZ).isoformat(timespec='seconds'),
                str(user_id),
                username,
                event,
                source,
            ]]},
        ))
    except Exception as e:
        logging.warning(f"No se pudo registrar evento {event} para {user_id}: {e}")


async def registrar_evento(user, event: str, source: str = '') -> None:
    if not user:
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        _GOOGLE_EXECUTOR,
        _registrar_evento_sync,
        user.id,
        user.username or '',
        event,
        source,
    )

async def eliminar_mensaje(msg, segundos: int) -> None:
    await asyncio.sleep(segundos)
    try:
        await msg.delete()
    except Exception as e:
        logging.warning(f"No se pudo eliminar mensaje {msg.message_id}: {e}")


def _cargar_eliminaciones_sync() -> None:
    PENDING_DELETIONS.clear()
    try:
        service = _get_sheets_service()
        rows = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'Eliminaciones!A2:C{MAX_PENDING_DELETIONS + 1}',
        )).get('values', [])
        for row in rows:
            if len(row) < 3:
                continue
            try:
                key = (int(row[0]), int(row[1]))
                PENDING_DELETIONS[key] = float(row[2])
            except (TypeError, ValueError):
                continue
        logging.info(f"Eliminaciones pendientes cargadas: {len(PENDING_DELETIONS)}")
    except Exception as e:
        logging.warning(f"No se pudieron cargar eliminaciones pendientes: {e}")


def _guardar_eliminacion_sync(chat_id: int, message_id: int, delete_at: float) -> None:
    try:
        service = _get_sheets_service()
        _execute_sheets(service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='Eliminaciones!A:C',
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': [[str(chat_id), str(message_id), str(delete_at)]]},
        ))
    except Exception as e:
        logging.warning(f"No se pudo persistir eliminación {message_id}: {e}")


def _reemplazar_eliminaciones_sync(rows=None) -> None:
    try:
        service = _get_sheets_service()
        _execute_sheets(service.spreadsheets().values().clear(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='Eliminaciones!A2:C',
        ))
        if rows:
            _execute_sheets(service.spreadsheets().values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f'Eliminaciones!A2:C{len(rows) + 1}',
                valueInputOption='RAW',
                body={'values': rows},
            ))
    except Exception as e:
        logging.warning(f"No se pudieron guardar eliminaciones pendientes: {e}")


async def programar_eliminacion_persistente(msg, segundos: int) -> None:
    key = (msg.chat_id, msg.message_id)
    if key not in PENDING_DELETIONS and len(PENDING_DELETIONS) >= MAX_PENDING_DELETIONS:
        logging.error("Límite de eliminaciones pendientes alcanzado; borrando bienvenida ahora")
        await msg.delete()
        return
    delete_at = datetime.now(TZ).timestamp() + segundos
    PENDING_DELETIONS[key] = delete_at
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        _GOOGLE_EXECUTOR,
        _guardar_eliminacion_sync,
        msg.chat_id,
        msg.message_id,
        delete_at,
    )


async def limpiar_eliminaciones_pendientes(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(TZ).timestamp()
    due = [key for key, delete_at in PENDING_DELETIONS.items() if delete_at <= now]
    if not due:
        return
    changed = False
    for chat_id, message_id in due:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            PENDING_DELETIONS.pop((chat_id, message_id), None)
            changed = True
            logging.info(f"Bienvenida {message_id} eliminada en {chat_id}")
        except Exception as e:
            if 'message to delete not found' in str(e).lower():
                PENDING_DELETIONS.pop((chat_id, message_id), None)
                changed = True
            else:
                logging.warning(f"No se pudo eliminar bienvenida {message_id}: {e}")
    if changed:
        rows = [
            [str(chat_id), str(message_id), str(delete_at)]
            for (chat_id, message_id), delete_at in sorted(
                PENDING_DELETIONS.items(),
                key=lambda item: item[1],
            )
        ]
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            _GOOGLE_EXECUTOR,
            _reemplazar_eliminaciones_sync,
            rows,
        )

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
        source = context.args[0] if context.args else 'directo'
        await registrar_evento(user, 'bot_start', source)
        if source == 'planes':
            await registrar_evento(user, 'view_prices', 'sample_button')
            await update.message.reply_text(m('precios'), reply_markup=SALES_MENU)
            return
        if source == 'lista':
            await registrar_evento(user, 'view_list', 'sample_button')
            await update.message.reply_text(
                m('lista'),
                reply_markup=SALES_MENU,
                disable_web_page_preview=True,
            )
            return
        if source == 'demo':
            await registrar_evento(user, 'demo_from_menu', 'sample_button')
            await update.message.reply_text(
                "🎬 ¿Quieres probar el contenido antes de comprar?\n\n"
                "Usa /demo para obtener 10 minutos de acceso gratuito "
                "a la carpeta DEMO con contenido limitado de muestra."
            )
            return
        msg = await _enviar_mensaje_bienvenida(
            context, update.effective_chat.id, _bienvenida(user)
        )
        await programar_eliminacion_persistente(msg, WELCOME_DELETE_SECONDS)
    except Exception as e:
        logging.error(f"Error en start: {e}")

async def precios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("ℹ️ Escríbeme en privado para ver los precios: @DriveVIPclubBot")
        return
    await registrar_evento(update.effective_user, 'view_prices', 'command')
    await update.message.reply_text(m('precios'), reply_markup=SALES_MENU)

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
    await registrar_evento(update.effective_user, 'view_list', 'command')
    await update.message.reply_text(
        m('lista'),
        reply_markup=SALES_MENU,
        disable_web_page_preview=True,
    )

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
        reply_markup=SALES_MENU,
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
            if chat.id == PUBLIC_GROUP_ID:
                await registrar_evento(new.user, 'group_leave', 'public_group')
            return
        if new_status in ('member', 'administrator') and old_status not in ('member', 'administrator'):
            user = new.user
            if chat.id == PUBLIC_GROUP_ID:
                await registrar_evento(user, 'group_join', 'public_group')
            try:
                msg = await _enviar_mensaje_bienvenida(context, chat.id, _bienvenida(user))
                await programar_eliminacion_persistente(
                    msg,
                    WELCOME_DELETE_SECONDS,
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
        logging.info(f"Salida oculta de {message.left_chat_member.id}")
    except Exception as e:
        logging.warning(f"No se pudo ocultar la salida de {message.left_chat_member.id}: {e}")


async def ocultar_entrada(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or message.chat_id not in (PUBLIC_GROUP_ID, VIP_GROUP_ID):
        return
    if not message.new_chat_members:
        return
    try:
        await message.delete()
        logging.info(f"Entrada oculta de {[u.id for u in message.new_chat_members]}")
    except Exception as e:
        logging.warning(f"No se pudo ocultar entrada: {e}")

def _crear_preferencia_sync(user_id: int, precio: int, plan: str, username: str):
    import requests as req
    pref = req.post('https://api.mercadopago.com/checkout/preferences', json={
        'items': [{
            'title': f'Membresía {plan} DriveVIPclub',
            'quantity': 1,
            'currency_id': 'CLP',
            'unit_price': precio,
        }],
        'external_reference': str(user_id),
        'metadata': {'telegram_username': username},
        'notification_url': 'https://drivevipclub.onrender.com/',
        'back_urls': {'success': 'https://t.me/DriveVIPclubBot', 'failure': 'https://t.me/DriveVIPclubBot'},
        'auto_return': 'approved',
        'payment_methods': {
            'excluded_payment_methods': [],
            'excluded_payment_types': [],
            'installments': 1,
        },
    }, headers={
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }, timeout=20)
    pref.raise_for_status()
    return pref.json()


async def _crear_preferencia(user_id: int, precio: int, plan: str, username: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _PAYMENT_EXECUTOR,
        partial(_crear_preferencia_sync, user_id, precio, plan, username),
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
        await registrar_evento(user, 'plan_selected', 'semanal')
        data = await _crear_preferencia(
            user.id,
            4990,
            'Semanal',
            user.username or 'sin_username',
        )
        if 'init_point' in data:
            await registrar_evento(user, 'payment_link_created', 'semanal')
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
        await registrar_evento(user, 'plan_selected', 'mensual')
        data = await _crear_preferencia(
            user.id,
            8990,
            'Mensual',
            user.username or 'sin_username',
        )
        if 'init_point' in data:
            await registrar_evento(user, 'payment_link_created', 'mensual')
            await update.message.reply_text(f"💎 Plan Mensual $8.990\n\n{data['init_point']}\n\n✅ Paga y el bot te pedirá tu Gmail.")
        else:
            await update.message.reply_text("❌ Error generando link. Contacta al admin.")
            logging.error(f"MP error: {data}")
    except Exception as e:
        await update.message.reply_text("❌ Error de conexión. Intenta más tarde.")
        logging.error(f"Error en /mensual: {e}")

PAYPAL_ORDER_IDS = {}

def _cargar_ordenes_paypal_sync():
    global PAYPAL_ORDER_IDS
    PAYPAL_ORDER_IDS.clear()
    try:
        service = _get_sheets_service()
        rows = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='PayPalOrders!A:D',
        )).get('values', [])
        for row in rows[1:]:
            if len(row) < 4:
                continue
            order_id, user_id_str, status, _ = row
            if status != 'processed' and user_id_str.isdigit():
                PAYPAL_ORDER_IDS[order_id] = int(user_id_str)
        logging.info(f"Órdenes PayPal pendientes: {len(PAYPAL_ORDER_IDS)}")
    except Exception as e:
        logging.warning(f"No se pudo cargar PayPalOrders: {e}")


def _crear_orden_paypal_sync(user_id, username):
    import base64
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        return None, "PayPal no configurado"
    try:
        auth = base64.b64encode(f'{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}'.encode()).decode()
        token_req = urllib.request.Request(
            'https://api-m.paypal.com/v1/oauth2/token',
            data=b'grant_type=client_credentials',
            headers={
                'Authorization': f'Basic {auth}',
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
        )
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            token = json.loads(resp.read()).get('access_token')
        if not token:
            return None, "Error obteniendo token PayPal"
        custom_id = f"dvc_{user_id}"
        order_body = json.dumps({
            'intent': 'CAPTURE',
            'purchase_units': [{
                'amount': {'currency_code': 'USD', 'value': '10.00'},
                'description': 'DriveVIPclub Mensual',
                'custom_id': custom_id,
                'invoice_id': f'DVC-{user_id}-{int(datetime.now().timestamp())}',
            }],
            'payment_source': {
                'paypal': {
                    'experience_context': {
                        'payment_method_preference': 'IMMEDIATE_PAYMENT_REQUIRED',
                        'landing_page': 'LOGIN',
                        'user_action': 'PAY_NOW',
                        'return_url': 'https://t.me/DriveVIPclubBot',
                        'cancel_url': 'https://t.me/DriveVIPclubBot',
                    }
                }
            },
        }).encode()
        order_req = urllib.request.Request(
            'https://api-m.paypal.com/v2/checkout/orders',
            data=order_body,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
        )
        with urllib.request.urlopen(order_req, timeout=15) as resp:
            order = json.loads(resp.read())
        order_id = order.get('id')
        approve_link = None
        for link in order.get('links', []):
            if link.get('rel') == 'payer-action':
                approve_link = link['href']
                break
        if not order_id or not approve_link:
            return None, "Error: no se obtuvo link de pago PayPal"
        return {'order_id': order_id, 'approve_url': approve_link, 'custom_id': custom_id}, None
    except Exception as e:
        return None, f"Error creando orden PayPal: {e}"


def _guardar_orden_paypal_sync(order_id, user_id, custom_id):
    try:
        service = _get_sheets_service()
        now = datetime.now(TZ).isoformat(timespec='seconds')
        _execute_sheets(service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='PayPalOrders!A:D',
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': [[order_id, str(user_id), 'pending', now]]},
        ))
    except Exception as e:
        logging.warning(f"Error guardando orden PayPal {order_id}: {e}")


def _actualizar_orden_paypal_sync(order_id, status):
    try:
        service = _get_sheets_service()
        rows = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='PayPalOrders!A:D',
        )).get('values', [])
        for i, row in enumerate(rows[1:], start=2):
            if len(row) >= 1 and row[0] == order_id:
                _execute_sheets(service.spreadsheets().values().update(
                    spreadsheetId=GOOGLE_SHEET_ID,
                    range=f'PayPalOrders!C{i}',
                    valueInputOption='RAW',
                    body={'values': [[status]]},
                ))
                return True
    except Exception as e:
        logging.warning(f"Error actualizando orden PayPal {order_id}: {e}")
    return False


async def paypal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("⚠️ Para pagar con PayPal, escríbeme en privado: @DriveVIPclubBot")
        return
    user = update.effective_user
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        await update.message.reply_text("❌ Sistema PayPal no disponible. Contacta al admin.")
        return
    await registrar_evento(user, 'plan_selected', 'paypal')
    try:
        loop = asyncio.get_running_loop()
        result, error = await loop.run_in_executor(
            _PAYMENT_EXECUTOR,
            partial(_crear_orden_paypal_sync, user.id, user.username or 'sin_username'),
        )
        if error or not result:
            await update.message.reply_text(f"❌ {error}")
            return
        await loop.run_in_executor(
            _GOOGLE_EXECUTOR,
            _guardar_orden_paypal_sync,
            result['order_id'], user.id, result['custom_id'],
        )
        PAYPAL_ORDER_IDS[result['order_id']] = user.id
        text = (
            "🌍 PAGO POR PAYPAL — $10 USD\n\n"
            "✅ Membresía mensual (30 días)\n\n"
            f"🔗 Link de pago personal:\n{result['approve_url']}\n\n"
            "📌 Pasos:\n"
            "1. Abre el link y paga $10 USD\n"
            "2. Vuelve a este chat y envíame tu Gmail\n"
            "3. El bot te dará acceso automáticamente ✅"
        )
        await update.message.reply_text(text, disable_web_page_preview=True)
        await registrar_evento(user, 'payment_link_created', 'paypal')
    except Exception as e:
        await update.message.reply_text("❌ Error de conexión. Intenta más tarde.")
        logging.error(f"Error en /paypal: {e}")


def _obtener_demo_samples_sync() -> list | None:
    """Selecciona 2 videos (<=100MB) y 3 fotos del DEMO_FOLDER_ID, los descarga y devuelve [(data, mime, name), ...]."""
    if not DEMO_FOLDER_ID:
        return None
    try:
        creators = _list_folder_files(DEMO_FOLDER_ID, "nextPageToken,files(id,name,mimeType)")
        creators = [c for c in creators if c.get('mimeType') == 'application/vnd.google-apps.folder']
        if not creators:
            return None
        import random, math
        random.shuffle(creators)
        fotos = []
        videos = []
        for c in creators:
            files = _list_folder_files(c['id'], "nextPageToken,files(id,name,size,mimeType)")
            for f in files:
                mt = f.get('mimeType', '')
                size = int(f.get('size', 0))
                if mt.startswith('video/') and size <= 100 * 1024 * 1024:
                    videos.append({'id': f['id'], 'name': f.get('name', 'video.mp4'), 'mimeType': mt})
                elif mt.startswith('image/'):
                    fotos.append({'id': f['id'], 'name': f.get('name', 'foto.jpg'), 'mimeType': mt})
            if len(videos) >= 2 and len(fotos) >= 3:
                break
        if len(videos) < 2 or len(fotos) < 3:
            logging.warning(f"Demo samples insuficientes: {len(videos)} videos, {len(fotos)} fotos")
            return None
        random.shuffle(videos)
        random.shuffle(fotos)
        selected = videos[:2] + fotos[:3]
        drive = _get_drive_service()
        result = []
        for s in selected:
            data = _execute_drive(drive.files().get_media(fileId=s['id']))
            result.append((data, s['mimeType'], s['name']))
        return result
    except Exception as e:
        logging.error(f"Error obteniendo muestras demo: {e}")
        return None


async def demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _solo_privado(update):
        await update.message.reply_text("ℹ️ Escríbeme en privado para solicitar una demo: @DriveVIPclubBot")
        return
    user = update.effective_user
    if not user:
        return
    if not DEMO_FOLDER_ID:
        await update.message.reply_text("❌ Demo no disponible. Contacta al admin.")
        return
    uid = user.id
    if uid in DEMO_EXPIRY:
        remaining = int(DEMO_EXPIRY[uid] - datetime.now().timestamp())
        if remaining > 0:
            await update.message.reply_text(
                f"⏳ Ya tienes una demo activa. Expira en {remaining // 60} min {remaining % 60} seg."
            )
            return
        else:
            del DEMO_EXPIRY[uid]
    if uid in PENDING_DEMO_GMAIL:
        await update.message.reply_text("Ya tienes una demo en proceso. Envíame tu correo Gmail para activar la carpeta completa.")
        return
    if uid in PENDING_GMAIL:
        await update.message.reply_text(
            "Ya tienes un pago pendiente. Envíame tu Gmail para activar tu membresía."
        )
        return
    loop = asyncio.get_event_loop()
    ya_uso = await loop.run_in_executor(_GOOGLE_EXECUTOR, _demo_ya_usada_sync, uid)
    if ya_uso:
        await update.message.reply_text(
            "⏰ Ya utilizaste tu demo gratuita.\n\n"
            "Para acceder al contenido COMPLETO elige un plan:\n"
            "💎 /semanal ($4.990) — 7 días\n"
            "💎 /mensual ($8.990) — 30 días\n\n"
            "¡En segundos tienes acceso a TODO!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🇨🇱 Semanal $4.990", callback_data="cmd_semanal")],
                [InlineKeyboardButton("🇨🇱 Mensual $8.990", callback_data="cmd_mensual")],
            ]),
        )
        return
    await registrar_evento(user, 'demo_solicitada', 'demo')
    await update.message.reply_text(
        "🎬 DEMO GRATIS — 10 MINUTOS\n\n"
        "Aquí tienes 5 archivos de muestra para que veas la calidad:\n"
        "📹 2 videos + 📸 3 fotos\n"
        "Esto es solo una pequeña parte de lo que hay en el Drive..."
    )
    samples = await loop.run_in_executor(_GOOGLE_EXECUTOR, _obtener_demo_samples_sync)
    if not samples:
        await update.message.reply_text("❌ Error preparando muestras. Contacta al admin.")
        return
    now_str = datetime.now(TZ).isoformat(timespec='seconds')
    await loop.run_in_executor(
        _GOOGLE_EXECUTOR, _guardar_demo_sync,
        uid, user.username or 'sin_username', 'pendiente', now_str, 'muestras_enviadas',
    )
    from io import BytesIO
    for data, mime, name in samples:
        try:
            if mime.startswith('video/'):
                await update.message.reply_video(video=InputFile(BytesIO(data), filename=name))
            else:
                await update.message.reply_photo(photo=InputFile(BytesIO(data), filename=name))
        except Exception as e:
            logging.warning(f"Error enviando sample demo {name}: {e}")
    stats_text = ""
    if STATS:
        stats_text = (
            f"📊 El Drive completo tiene:\n"
            f"• {STATS.get('carpetas', '?')} modelos\n"
            f"• {STATS.get('videos', '?')} videos\n"
            f"• {STATS.get('fotos', '?')} fotos\n"
            f"• {STATS.get('tamano', '?')} en total\n\n"
        )
    PENDING_DEMO_GMAIL[uid] = True
    await update.message.reply_text(
        f"¿Te gustó lo que viste?\n\n"
        f"{stats_text}"
        f"La carpeta DEMO completa tiene {STATS.get('carpetas', '?')} carpetas organizadas A-Z "
        f"con mucho más contenido.\n\n"
        f"📧 Para acceder a la carpeta DEMO completa por 10 minutos, "
        f"envíame tu correo Gmail y te comparto el acceso:\n\n"
        f"(Solo usaremos tu correo para darte acceso, sin compromiso)"
    )


async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return
    if not _solo_privado(update):
        return
    text = update.message.text.strip()
    if user.id in PENDING_DEMO_GMAIL:
        if '@' not in text or '.' not in text:
            await update.message.reply_text("❌ Eso no parece un Gmail válido. Envíame tu correo electrónico (ej: usuario@gmail.com)")
            return
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(_GOOGLE_EXECUTOR, _compartir_drive_demo_sync, text)
        if not ok:
            await update.message.reply_text("❌ Error compartiendo la demo. Contacta al admin.")
            return
        del PENDING_DEMO_GMAIL[user.id]
        demo_emails = context.bot_data.setdefault('demo_emails', {})
        demo_emails[user.id] = text
        DEMO_EXPIRY[user.id] = datetime.now().timestamp() + 10 * 60
        expires_str = (datetime.now() + timedelta(minutes=10)).isoformat(timespec='seconds')
        await loop.run_in_executor(
            _GOOGLE_EXECUTOR, _guardar_demo_sync,
            user.id, user.username or 'sin_username', text, expires_str,
        )
        caption = (
            "🎬 DEMO COMPLETA ACTIVADA — 10 MIN\n\n"
            f"Acceso concedido a {text}\n\n"
            "Revisa DRIVE > COMPARTIDOS CONMIGO (te llega un email).\n"
            f"Link directo: https://drive.google.com/drive/folders/{DEMO_FOLDER_ID}\n\n"
            "⏰ Tienes 10 minutos para explorar la carpeta DEMO completa.\n"
            "Cuando venza el tiempo, se revocará tu acceso.\n\n"
            "Para ver TODO el contenido, elige un plan al terminar la demo."
        )
        if os.path.exists('demo_drive.png'):
            with open('demo_drive.png', 'rb') as f:
                await update.message.reply_photo(photo=InputFile(f), caption=caption)
        else:
            await update.message.reply_text(caption)
        await registrar_evento(user, 'demo_access_granted', 'demo')
        await context.bot.send_message(
            chat_id=PUBLIC_GROUP_ID,
            text=(
                f"👤 Demo solicitada por @{user.username or user.id}\n"
                f"ID: {user.id}\n"
                f"Email: {text}\n"
                f"{ADMIN_USERNAME}"
            ),
        )
        return
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
        await registrar_evento(user, 'access_granted', 'gmail_received')
        return

    text_lower = text.lower()
    if 'paypal' in text_lower:
        await update.message.reply_text(
            "🌍 Para pagar con PayPal usa /paypal\n\n"
            "Recibirás un link personal para pagar $10 USD por 30 días. "
            "El bot detectará el pago automáticamente."
        )
        return

    await registrar_evento(user, 'private_message', 'free_text')
    await update.message.reply_text(
        "¿Qué te gustaría revisar? Elige una opción para continuar:",
        reply_markup=SALES_MENU,
    )

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
    f"\U0001F4F7 Muestra real del Drive.\n{{carpetas}} modelos \u2022 {{videos}} videos \u2022 {{tamano}}\n\n\U0001F3AC Prueba gratis con /demo\n\U0001F916 Suscríbete con @DriveVIPclubBot\n\U0001F4AC Atención directa: {ADMIN_USERNAME}",
    f"\U0001F525 Esto es solo una muestra.\nTenemos {{carpetas}} modelos organizados de la A a la Z.\n\n\U0001F916 Suscríbete con @DriveVIPclubBot\n\U0001F4AC Atención directa: {ADMIN_USERNAME}",
    f"\U0001F48E Contenido nuevo todas las semanas.\nAcceso 24/7 y descargas sin límites.\n\n\U0001F916 Suscríbete con @DriveVIPclubBot\n\U0001F3AC Prueba gratis con /demo\n\U0001F4AC Atención directa: {ADMIN_USERNAME}",
    f"\U0001F4CA Revisa el listado con /lista y prueba antes con /demo.\nTransparencia total sobre el contenido antes de pagar.\n\n\U0001F916 Suscríbete con @DriveVIPclubBot\n\U0001F4AC Atención directa: {ADMIN_USERNAME}",
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
                caption=caption,
                reply_markup=SALES_MENU,
            )
        else:
            msg = await context.bot.send_photo(
                chat_id=PUBLIC_GROUP_ID,
                photo=InputFile(BytesIO(data), filename="muestra.jpg"),
                caption=caption,
                reply_markup=SALES_MENU,
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
                reply_markup=SALES_MENU,
            )
            is_vid = False
        except Exception as fallback_error:
            logging.error(f"Error publicando foto de respaldo: {fallback_error}")
            return
    # Rotation: keep max 3 posts visible, delete oldest when new arrives
    rotating = context.bot_data.setdefault('rotating_samples', [])
    if len(rotating) >= 3:
        oldest = rotating.pop(0)
        try:
            await context.bot.delete_message(chat_id=PUBLIC_GROUP_ID, message_id=oldest)
        except Exception:
            pass
    rotating.append(msg.message_id)
    promo_ids = context.bot_data.setdefault('promo_message_ids', set())
    promo_ids.add(msg.message_id)
    _trim_set(promo_ids, 500)
    try:
        if is_vid:
            channel_message = await context.bot.send_video(
                chat_id=CHANNEL_ID,
                video=msg.video.file_id,
                caption=caption,
                reply_markup=SALES_MENU,
            )
        else:
            channel_message = await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=msg.photo[-1].file_id,
                caption=caption,
                reply_markup=SALES_MENU,
            )
        context.application.create_task(
            eliminar_mensaje(channel_message, SCHEDULED_DELETE_SECONDS)
        )
        logging.info("Muestra publicada en el grupo y el canal")
    except Exception as e:
        logging.warning(f"Muestra publicada en el grupo, pero no en el canal: {e}")


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


async def actualizar_stats_semanales(context: ContextTypes.DEFAULT_TYPE) -> None:
    loop = asyncio.get_running_loop()
    updated = await loop.run_in_executor(
        _GOOGLE_EXECUTOR,
        _actualizar_stats_semanales_sync,
    )
    if not updated:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=PUBLIC_GROUP_ID,
            message_id=FIXED_LIST_MESSAGE_ID,
            text=m('lista'),
            reply_markup=SALES_MENU,
            disable_web_page_preview=True,
        )
        logging.info(f"Mensaje fijado {FIXED_LIST_MESSAGE_ID} actualizado")
    except Exception as e:
        if 'Message is not modified' in str(e):
            logging.info(f"Mensaje fijado {FIXED_LIST_MESSAGE_ID} ya estaba actualizado")
        else:
            logging.error(f"No se pudo actualizar el mensaje fijado: {e}")

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
        username = str(
            (payment.get('metadata') or {}).get('telegram_username') or ''
        )
        hoy = datetime.now(TZ).date().isoformat()

        try:
            result = await loop.run_in_executor(
                _GOOGLE_EXECUTOR,
                _procesar_pago_sheet_sync,
                user_id,
                payment_id,
                plan,
                hoy,
                username,
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

        await loop.run_in_executor(
            _GOOGLE_EXECUTOR,
            _registrar_evento_sync,
            user_id,
            '',
            'payment_approved',
            plan,
        )

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

def _obtener_token_paypal_sync():
    import base64
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        return None
    try:
        auth = base64.b64encode(f'{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}'.encode()).decode()
        req = urllib.request.Request(
            'https://api-m.paypal.com/v1/oauth2/token',
            data=b'grant_type=client_credentials',
            headers={
                'Authorization': f'Basic {auth}',
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get('access_token')
    except Exception as e:
        logging.warning(f"Error obteniendo token PayPal: {e}")
        return None


def _buscar_pagos_paypal_sync(token):
    if not token:
        return []
    now = datetime.utcnow()
    start = (now - timedelta(hours=2)).strftime('%Y-%m-%dT00:00:00-0700')
    end = now.strftime('%Y-%m-%dT23:59:59-0700')
    params = urllib.parse.urlencode({
        'start_date': start,
        'end_date': end,
        'fields': 'all',
        'page_size': 50,
        'transaction_status': 'S',
    })
    try:
        req = urllib.request.Request(
            f'https://api-m.paypal.com/v1/reporting/transactions?{params}',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get('transaction_details', [])
    except Exception as e:
        logging.warning(f"Error buscando pagos PayPal: {e}")
        return []


PAYPAL_PROCESSED = set()


def _procesar_pago_paypal_sheet_sync(user_id, transaction_id, amount, fecha):
    service = _get_sheets_service()
    rows = _execute_sheets(service.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"'Ventas'!A1:K{MAX_SALES_ROWS + 1}",
    )).get('values', [])
    tid = str(transaction_id)
    today = _parse_sheet_date(fecha)
    if not today:
        raise ValueError(f'Fecha de pago inválida: {fecha}')

    for row_number, row in enumerate(rows[1:], start=2):
        if not row or row[0] != str(user_id):
            continue
        payment_ids = _parse_payment_ids(row[10] if len(row) > 10 else '')
        if tid in payment_ids:
            return {'status': 'duplicate'}
        tenia_plan = bool(len(row) > 3 and row[3])
        needs_email = not bool(len(row) > 2 and row[2])
        current_end = _parse_sheet_date(row[5] if len(row) > 5 else '')
        start_date = max(today, current_end) if current_end else today
        expires_on = start_date + timedelta(days=30)
        payment_ids.add(tid)
        persisted_ids = '|'.join(sorted(payment_ids)[-100:])
        _execute_sheets(service.spreadsheets().values().batchUpdate(
            spreadsheetId=GOOGLE_SHEET_ID,
            body={
                'valueInputOption': 'USER_ENTERED',
                'data': [
                    {
                        'range': f"'Ventas'!D{row_number}:E{row_number}",
                        'values': [['mensual', start_date.isoformat()]],
                    },
                    {
                        'range': f"'Ventas'!I{row_number}:K{row_number}",
                        'values': [['paypal', '', persisted_ids]],
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

    if len(rows) - 1 >= MAX_SALES_ROWS:
        raise RuntimeError('Hoja 1 alcanzó el límite de ventas configurado')
    row_number = max(2, len(rows) + 1)
    expires_on = today + timedelta(days=30)
    registered_at = datetime.now(TZ).isoformat(timespec='seconds')
    _execute_sheets(service.spreadsheets().values().batchUpdate(
        spreadsheetId=GOOGLE_SHEET_ID,
        body={
            'valueInputOption': 'USER_ENTERED',
            'data': [
                {
                    'range': f"'Ventas'!A{row_number}:E{row_number}",
                    'values': [[
                        str(user_id), 'sin_username', '', 'mensual', today.isoformat(),
                    ]],
                },
                {
                    'range': f"'Ventas'!H{row_number}:K{row_number}",
                    'values': [[registered_at, 'paypal', '', tid]],
                },
            ],
        },
    ))
    return {
        'status': 'processed',
        'renewal': False,
        'needs_email': True,
        'expires_on': expires_on.isoformat(),
    }


def _cargar_estado_pagos_paypal_sync():
    global PAYPAL_PROCESSED
    PAYPAL_PROCESSED.clear()
    try:
        service = _get_sheets_service()
        rows = _execute_sheets(service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'Ventas'!A1:K{MAX_SALES_ROWS + 1}",
        )).get('values', [])
        for row in rows[1:]:
            if not row or len(row) <= 10:
                continue
            origen = row[8] if len(row) > 8 else ''
            if origen == 'paypal':
                PAYPAL_PROCESSED.update(_parse_payment_ids(row[10]))
    except Exception as e:
        logging.warning(f"Error cargando estado PayPal: {e}")


def _verificar_orden_paypal_sync(token, order_id):
    try:
        req = urllib.request.Request(
            f'https://api-m.paypal.com/v2/checkout/orders/{order_id}',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logging.warning(f"Error verificando orden PayPal {order_id}: {e}")
        return None


def _capturar_orden_paypal_sync(token, order_id):
    try:
        req = urllib.request.Request(
            f'https://api-m.paypal.com/v2/checkout/orders/{order_id}/capture',
            data=b'',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logging.warning(f"Error capturando orden PayPal {order_id}: {e}")
        return None


async def _poll_paypal_payments(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        return
    if not PAYPAL_ORDER_IDS:
        return
    loop = asyncio.get_running_loop()
    token = await loop.run_in_executor(_PAYMENT_EXECUTOR, _obtener_token_paypal_sync)
    if not token:
        return

    for order_id, user_id in list(PAYPAL_ORDER_IDS.items()):
        order = await loop.run_in_executor(
            _PAYMENT_EXECUTOR, _verificar_orden_paypal_sync, token, order_id
        )
        if not order:
            continue
        status = order.get('status')
        intent = order.get('intent', '')
        purchase_units = order.get('purchase_units', [{}])
        capture_id = None
        for pu in purchase_units:
            payments = pu.get('payments', {})
            for capture in payments.get('captures', []):
                if capture.get('status') == 'COMPLETED':
                    capture_id = capture.get('id')
                    break
            for capture in payments.get('authorizations', []):
                if capture.get('status') == 'COMPLETED' and intent == 'CAPTURE':
                    cap_result = await loop.run_in_executor(
                        _PAYMENT_EXECUTOR, _capturar_orden_paypal_sync, token, order_id
                    )
                    if cap_result:
                        for pu2 in cap_result.get('purchase_units', [{}]):
                            for cap2 in pu2.get('payments', {}).get('captures', []):
                                if cap2.get('status') == 'COMPLETED':
                                    capture_id = cap2.get('id')
                                    break

        if not capture_id or capture_id in PAYPAL_PROCESSED:
            if status in ('VOIDED', 'EXPIRED'):
                del PAYPAL_ORDER_IDS[order_id]
                await loop.run_in_executor(
                    _GOOGLE_EXECUTOR, _actualizar_orden_paypal_sync, order_id, 'expired'
                )
            continue

        hoy = datetime.now(TZ).date().isoformat()
        logging.info(f"PayPal orden COMPLETADA: {order_id} → usuario {user_id}, capture {capture_id}")

        try:
            result = await loop.run_in_executor(
                _GOOGLE_EXECUTOR,
                _procesar_pago_paypal_sheet_sync,
                user_id, capture_id, 10.0, hoy,
            )
        except Exception as e:
            logging.error(f"Error guardando pago PayPal {capture_id}: {e}")
            continue

        if result['status'] == 'duplicate':
            PAYPAL_PROCESSED.add(capture_id)
            del PAYPAL_ORDER_IDS[order_id]
            await loop.run_in_executor(
                _GOOGLE_EXECUTOR, _actualizar_orden_paypal_sync, order_id, 'processed'
            )
            continue

        PAYPAL_PROCESSED.add(capture_id)
        _trim_set(PAYPAL_PROCESSED, 2000)
        del PAYPAL_ORDER_IDS[order_id]
        await loop.run_in_executor(
            _GOOGLE_EXECUTOR, _actualizar_orden_paypal_sync, order_id, 'processed'
        )

        await loop.run_in_executor(
            _GOOGLE_EXECUTOR,
            _registrar_evento_sync, user_id, '', 'payment_approved', 'paypal',
        )

        needs_email = result['needs_email']
        expires_on = result['expires_on']
        try:
            if not needs_email:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"✅ ¡Pago PayPal recibido! Tu membresía se ha extendido "
                        f"hasta {expires_on}. ¡Disfruta!"
                    ),
                )
            else:
                PENDING_GMAIL[user_id] = True
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "✅ ¡Pago PayPal confirmado! Ahora envíame tu correo Gmail "
                        "para darte acceso al Drive."
                    ),
                )
        except Exception as e:
            logging.error(f"Pago PayPal {capture_id} guardado, pero no se pudo avisar: {e}")


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
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"'Ventas'!A1:K{MAX_SALES_ROWS + 1}",
            ))
        )
        rows = rows.get('values', [])
        for row in rows[1:]:
            if len(row) < 7:
                continue
            user_id = row[0]
            estado = row[6] if len(row) > 6 else ''
            email = row[2] if len(row) > 2 else ''
            notes = row[9] if len(row) > 9 else ''
            if (
                estado == 'vencido'
                and notes != 'acceso_revocado'
                and email
                and '@' in email
            ):
                ok = await loop.run_in_executor(_GOOGLE_EXECUTOR, _revocar_drive_sync, email)
                if ok:
                    await loop.run_in_executor(
                        _GOOGLE_EXECUTOR,
                        _actualizar_sheet_sync,
                        user_id,
                        'J',
                        'acceso_revocado',
                    )
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
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"'Ventas'!A1:K{MAX_SALES_ROWS + 1}",
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

async def _procesar_demos_vencidas(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now().timestamp()
    vencidas = [uid for uid, exp in list(DEMO_EXPIRY.items()) if now >= exp]
    if not vencidas:
        return
    loop = asyncio.get_event_loop()
    for uid in vencidas:
        del DEMO_EXPIRY[uid]
        try:
            user_data = context.bot_data.get('demo_emails', {}).pop(uid, None)
            if user_data:
                await loop.run_in_executor(_GOOGLE_EXECUTOR, _revocar_drive_demo_sync, user_data)
        except Exception as e:
            logging.warning(f"Error revocando demo a {uid}: {e}")
        try:
            await loop.run_in_executor(
                _GOOGLE_EXECUTOR, _actualizar_demo_status_sync, uid, 'expirado'
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "⏰ TU DEMO HA TERMINADO\n\n"
                    "Espero hayas disfrutado la muestra. Para acceder al "
                    "contenido COMPLETO organizado de la A a la Z:\n\n"
                    "💎 /semanal ($4.990) — 7 días\n"
                    "💎 /mensual ($8.990) — 30 días\n\n"
                    "Pagos nacionales: MercadoPago (débito, crédito, transferencia)\n"
                    "🌍 Pagos internacionales: PayPal — /paypal\n\n"
                    "¡En segundos tienes acceso a TODO!"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🇨🇱 Pago semanal $4.990", callback_data="cmd_semanal")],
                    [InlineKeyboardButton("🇨🇱 Pago mensual $8.990", callback_data="cmd_mensual")],
                    [InlineKeyboardButton("🌍 PayPal $10 USD", url=PAYPAL_LINK)],
                ]),
            )
            logging.info(f"Demo vencida notificada a {uid}")
        except Exception as e:
            logging.warning(f"No se pudo notificar demo vencida a {uid}: {e}")


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
    _run_google_sync(_inicializar_planilla_sync)
    _run_google_sync(_cargar_mensajes_sync)
    _run_google_sync(_cargar_stats_cache_sync)
    _run_google_sync(_cargar_estado_pagos_sync)
    _run_google_sync(_cargar_estado_pagos_paypal_sync)
    _run_google_sync(_cargar_ordenes_paypal_sync)
    _run_google_sync(_cargar_eliminaciones_sync)
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
    application.add_handler(CommandHandler("paypal",   paypal))
    application.add_handler(CommandHandler("lista",    lista))
    application.add_handler(CommandHandler("ventajas", ventajas))
    application.add_handler(CommandHandler("testdrive", test_drive))
    application.add_handler(CommandHandler("demo", demo))
    application.add_handler(
        ChatMemberHandler(_bienvenida_chat_member, chat_member_types=ChatMemberHandler.CHAT_MEMBER)
    )
    application.add_handler(
        MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, ocultar_salida)
    )
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, ocultar_entrada)
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
    job_queue.run_repeating(
        _poll_paypal_payments,
        interval=120,
        first=15,
        name='poll_paypal',
    )
    job_queue.run_repeating(
        limpiar_eliminaciones_pendientes,
        interval=60,
        first=5,
        name='limpiar_bienvenidas',
    )
    job_queue.run_daily(verificar_vencidos, time=time(4, 0, tzinfo=TZ))
    job_queue.run_daily(verificar_proximos_vencer, time=time(10, 0, tzinfo=TZ))
    job_queue.run_repeating(
        _procesar_demos_vencidas,
        interval=15,
        first=10,
        name='procesar_demos',
    )
    for hour in (10, 15, 20):
        job_queue.run_daily(mensaje_canal, time=time(hour, 0, tzinfo=TZ), name=f'canal_{hour}')
    for hour, minute in ((10, 5), (13, 5), (16, 5), (19, 5), (22, 5), (23, 30)):
        job_queue.run_daily(
            publicar_muestra,
            time=time(hour, minute, tzinfo=TZ),
            name=f'muestra_{hour}_{minute}',
        )
    # Rotacion continua: ya no se eliminan todas a medianoche
    job_queue.run_daily(
        actualizar_stats_semanales,
        time=time(6, 0, tzinfo=TZ),
        days=(1,),
        name='stats_lunes_06',
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
