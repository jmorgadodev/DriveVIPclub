import create_demo as cd
drive = cd._get_drive_service()
DRIVE_FOLDER_ID = cd.DRIVE_FOLDER_ID
children = cd.list_folder_children(DRIVE_FOLDER_ID)
subfolders = [c for c in children if c['mimeType'] == 'application/vnd.google-apps.folder']
print(f"Total subfolders: {len(subfolders)}")
for sf in subfolders[:5]:
    print(f"\n--- {sf['name']} ({sf['id']}) ---")
    kids = cd.list_folder_children(sf['id'])
    for k in kids[:15]:
        t = '(folder)' if k['mimeType'] == 'application/vnd.google-apps.folder' else k['mimeType'].split('/')[-1]
        print(f"  {k['name']:30s} {t}")
    if len(kids) > 15:
        print(f"  ... y {len(kids)-15} mas")
