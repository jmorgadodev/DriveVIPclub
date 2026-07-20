import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
PUBLIC_GROUP_ID    = int(os.getenv('PUBLIC_GROUP_ID', '0'))
VIP_GROUP_ID       = int(os.getenv('VIP_GROUP_ID', '0'))
CHANNEL_ID         = int(os.getenv('CHANNEL_ID', '0'))
ADMIN_USERNAME     = os.getenv('ADMIN_USERNAME', '@backadminthree')
FIXED_LIST_MESSAGE_ID = int(os.getenv('FIXED_LIST_MESSAGE_ID', '478'))

GOOGLE_SHEET_ID            = os.getenv('GOOGLE_SHEET_ID', '')
GOOGLE_SHEET_RANGE         = os.getenv('GOOGLE_SHEET_RANGE', 'Hoja1!A:J')
GOOGLE_SERVICE_ACCOUNT     = os.getenv('GOOGLE_SERVICE_ACCOUNT', '')
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON', '')
GOOGLE_DRIVE_OAUTH_TOKEN_JSON = os.getenv('GOOGLE_DRIVE_OAUTH_TOKEN_JSON', '')
MENSAJES_SHEET_RANGE       = os.getenv('MENSAJES_SHEET_RANGE', 'Mensajes!A:B')
MP_ACCESS_TOKEN            = os.getenv('MP_ACCESS_TOKEN', '')
DRIVE_FOLDER_ID            = os.getenv('DRIVE_FOLDER_ID', '')
LISTADO_SHEET_ID           = os.getenv('LISTADO_SHEET_ID', '')
PAYPAL_CLIENT_ID           = os.getenv('PAYPAL_CLIENT_ID', '')
PAYPAL_CLIENT_SECRET       = os.getenv('PAYPAL_CLIENT_SECRET', '')
PAYPAL_LINK                = os.getenv('PAYPAL_LINK', 'https://www.paypal.com/ncp/payment/KQ7PLLMZ6CLCC')
