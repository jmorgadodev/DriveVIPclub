"""Count total creators across all categories."""
import create_demo as cd
drive = cd._get_drive_service()

children = cd.list_folder_children(cd.DRIVE_FOLDER_ID)
cats = [c for c in children if c['mimeType'] == 'application/vnd.google-apps.folder']
cats.sort(key=lambda x: x['name'])

total = 0
for cat in cats:
    creators = [c for c in cd.list_folder_children(cat['id']) if c['mimeType'] == 'application/vnd.google-apps.folder']
    total += len(creators)
    print(f"{cat['name']:4s}: {len(creators):4d} creadores")
print(f"\nTotal: {total} creadores en {len(cats)} categorias")

if total > 0:
    avg_files = total * 12
    est_secs = total * 6  # ~6s per creator for copy ops
    print(f"Estimado: {avg_files} archivos a copiar, ~{est_secs//60} min")
