import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
PUBLIC_GROUP_ID    = int(os.getenv('PUBLIC_GROUP_ID', '0'))
VIP_GROUP_ID       = int(os.getenv('VIP_GROUP_ID', '0'))
CHANNEL_ID         = int(os.getenv('CHANNEL_ID', '0'))
ADMIN_USERNAME     = os.getenv('ADMIN_USERNAME', '@koooke')

GOOGLE_SHEET_ID       = os.getenv('GOOGLE_SHEET_ID', '')
GOOGLE_SHEET_RANGE    = os.getenv('GOOGLE_SHEET_RANGE', 'Hoja1!A:I')
GOOGLE_SERVICE_ACCOUNT = os.getenv('GOOGLE_SERVICE_ACCOUNT', '')
