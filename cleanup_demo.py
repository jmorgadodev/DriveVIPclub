"""Delete old DEMO - DriveVIPclub folders."""
import create_demo as cd
drive = cd._get_drive_service()
r = cd._execute(drive.files().list(
    q="name='DEMO - DriveVIPclub' and trashed=false",
    fields='files(id)',
))
for f in r.get('files', []):
    cd._execute(drive.files().update(fileId=f['id'], body={'trashed': True}))
    print(f"Enviado a papelera: {f['id']}")
