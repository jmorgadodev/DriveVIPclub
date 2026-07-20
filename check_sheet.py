import json, os
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SHEET_ID = '1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs'

with open(r'C:\Users\jorge\.codex\credentials\gsc-service-account.json') as f:
    info = json.load(f)
creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
service = build('sheets', 'v4', credentials=creds)

# Read headers
r1 = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range='Hoja 1!1:1').execute()
print('Row 1:', r1.get('values', []))

# Read formulas in row 1
r1f = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range='Hoja 1!1:1', valueRenderOption='FORMULA').execute()
print('Formulas:', r1f.get('values', []))

# Read a few data rows
rows = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range='Hoja 1!A:K').execute()
print('\nAll data:')
for i, row in enumerate(rows.get('values', [])[:10]):
    print(f'  Row {i}: {row}')

# Check Mensajes tab
men = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range='Mensajes!A:B').execute()
print('\nMensajes:')
for r in men.get('values', []):
    print(f'  {r}')
