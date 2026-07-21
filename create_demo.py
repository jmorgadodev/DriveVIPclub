#!/usr/bin/env python3
"""Populate DEMO folder from main DriveVIPclub folder.
Run once locally to set up the demo structure.

Usage:
  python create_demo.py

Requires the same Google credentials as bot.py.
Outputs DEMO_FOLDER_ID at the end — add to config.py and Render env.
"""

import os
import sys
import json
import random
import base64
import io
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

DRIVE_FOLDER_ID = os.environ.get('DRIVE_FOLDER_ID', '')
MAX_PHOTOS = 10
MAX_VIDEOS = 2
MAX_CREATORS_PER_CATEGORY = 999999
SCOPES = ['https://www.googleapis.com/auth/drive']

GOOGLE_DRIVE_OAUTH_TOKEN_JSON = os.environ.get('GOOGLE_DRIVE_OAUTH_TOKEN_JSON', '')
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
GOOGLE_SERVICE_ACCOUNT = os.environ.get('GOOGLE_SERVICE_ACCOUNT', '')

_drive_service = None


def _get_drive_service():
    global _drive_service
    if _drive_service is not None:
        return _drive_service
    if GOOGLE_DRIVE_OAUTH_TOKEN_JSON:
        info = json.loads(base64.b64decode(GOOGLE_DRIVE_OAUTH_TOKEN_JSON))
        creds = Credentials.from_authorized_user_info(info, scopes=SCOPES)
    elif os.path.exists('.drive_token.json'):
        creds = Credentials.from_authorized_user_file('.drive_token.json', scopes=SCOPES)
    elif GOOGLE_SERVICE_ACCOUNT_JSON:
        info = json.loads(base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON))
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    elif GOOGLE_SERVICE_ACCOUNT:
        with open(GOOGLE_SERVICE_ACCOUNT) as f:
            info = json.load(f)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_authorized_user_file('token.json', scopes=SCOPES)
    _drive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    return _drive_service


def _execute(request):
    return request.execute()


def list_folder_children(parent_id):
    """Return list of {id, name, mimeType} for all direct children (files + folders)."""
    results = []
    pt = None
    drive = _get_drive_service()
    while True:
        r = _execute(drive.files().list(
            q=f"'{parent_id}' in parents and trashed=false",
            fields="nextPageToken,files(id,name,mimeType,size)",
            pageSize=200,
            pageToken=pt,
            orderBy="name",
        ))
        results.extend(r.get("files", []))
        pt = r.get("nextPageToken")
        if not pt:
            break
    return results


def collect_direct_files(folder_id):
    """Get non-folder files directly inside folder_id (no recursion).
    Returns (photos, videos) lists of {id, name, mimeType, size}.
    """
    photos = []
    videos = []
    pt = None
    drive = _get_drive_service()
    while True:
        r = _execute(drive.files().list(
            q=f"'{folder_id}' in parents and mimeType!='application/vnd.google-apps.folder' and trashed=false",
            fields="nextPageToken,files(id,name,mimeType,size)",
            pageSize=200,
            pageToken=pt,
        ))
        for f in r.get("files", []):
            if f['mimeType'].startswith('image/'):
                photos.append(f)
            elif f['mimeType'].startswith('video/'):
                videos.append(f)
        pt = r.get("nextPageToken")
        if not pt:
            break
    return photos, videos


def collect_files_recursive(folder_id):
    """Collect all non-folder files recursively from folder_id and subfolders.
    Returns (photos, videos) lists of {id, name, mimeType, size}.
    """
    photos = []
    videos = []
    stack = [folder_id]
    drive = _get_drive_service()
    while stack:
        fid = stack.pop()
        pt = None
        while True:
            r = _execute(drive.files().list(
                q=f"'{fid}' in parents and trashed=false",
                fields="nextPageToken,files(id,name,mimeType,size)",
                pageSize=200,
                pageToken=pt,
            ))
            for f in r.get("files", []):
                if f['mimeType'] == 'application/vnd.google-apps.folder':
                    stack.append(f['id'])
                elif f['mimeType'].startswith('image/'):
                    photos.append(f)
                elif f['mimeType'].startswith('video/'):
                    videos.append(f)
            pt = r.get("nextPageToken")
            if not pt:
                break
    return photos, videos


def create_or_get_folder(drive, name, parent_id):
    """Create subfolder if doesn't exist, return its ID."""
    r = _execute(drive.files().list(
        q=f"name='{name.replace(chr(39), "\\\\'")}' and '{parent_id}' in parents "
          f"and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id)",
    ))
    existing = r.get("files", [])
    if existing:
        return existing[0]["id"]
    folder = _execute(drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id",
    ))
    logging.info(f"  Creada carpeta: {name}")
    return folder["id"]


def copy_file(drive, file_id, target_parent_id):
    """Copy a file to target folder, return new file ID."""
    copied = _execute(drive.files().copy(
        fileId=file_id,
        body={"parents": [target_parent_id]},
        fields="id,name",
    ))
    return copied


def create_demo_txt(drive, parent_id, folder_name):
    """Create _SOLO_DEMO.txt in the demo folder."""
    content = (
        "SOLO DEMO - Contenido limitado\n"
        f"Carpeta: {folder_name}\n"
        "\n"
        "Esta carpeta contiene solo una MUESTRA del contenido real.\n"
        "Para acceder al contenido COMPLETO organisado de la A a la Z\n"
        "con actualizaciones semanales, unete al grupo:\n"
        "\n"
        "https://t.me/+-1gS1EfQMLNmMjdh\n"
        "\n"
        "Planes:\n"
        "- Semanal $4.990 (7 dias)\n"
        "- Mensual $8.990 (30 dias)\n"
        "\n"
        "@DriveVIPclubBot\n"
    )
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")),
                               mimetype="text/plain", resumable=False)
    _execute(drive.files().create(
        body={"name": "_SOLO_DEMO.txt", "parents": [parent_id]},
        media_body=media,
        fields="id",
    ))


def main():
    if not DRIVE_FOLDER_ID:
        print("ERROR: DRIVE_FOLDER_ID not set in .env")
        sys.exit(1)

    drive = _get_drive_service()

    # Get main folder info
    main_info = _execute(drive.files().get(
        fileId=DRIVE_FOLDER_ID, fields="id,name,parents"
    ))
    parent_ids = main_info.get("parents", [])

    # Determine/create DEMO parent folder
    demo_name = "DEMO - DriveVIPclub"
    if parent_ids:
        r = _execute(drive.files().list(
            q=f"name='{demo_name}' and '{parent_ids[0]}' in parents "
              f"and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id)",
        ))
        existing = r.get("files", [])
        if existing:
            demo_parent_id = existing[0]["id"]
            logging.info(f"DEMO folder ya existe: {demo_parent_id}")
        else:
            folder = _execute(drive.files().create(
                body={"name": demo_name, "mimeType": "application/vnd.google-apps.folder",
                      "parents": parent_ids[0]},
                fields="id",
            ))
            demo_parent_id = folder["id"]
            logging.info(f"DEMO folder creada: {demo_parent_id}")
    else:
        r = _execute(drive.files().list(
            q=f"name='{demo_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id)",
        ))
        existing = r.get("files", [])
        if existing:
            demo_parent_id = existing[0]["id"]
        else:
            folder = _execute(drive.files().create(
                body={"name": demo_name, "mimeType": "application/vnd.google-apps.folder"},
                fields="id",
            ))
            demo_parent_id = folder["id"]

    # List category folders (A-Z, #, etc.)
    children = list_folder_children(DRIVE_FOLDER_ID)
    category_folders = [c for c in children if c["mimeType"] == "application/vnd.google-apps.folder"]
    logging.info(f"Categorias encontradas: {len(category_folders)}")

    total_photos = 0
    total_videos = 0
    total_creators = 0
    total_categories = 0

    for cat_folder in category_folders:
        cat_name = cat_folder["name"]
        logging.info(f"\n=== Categoria: {cat_name} ===")

        # Create category folder in demo
        demo_cat = create_or_get_folder(drive, cat_name, demo_parent_id)
        total_categories += 1

        # List creator subfolders inside this category
        creators = [c for c in list_folder_children(cat_folder["id"])
                     if c["mimeType"] == "application/vnd.google-apps.folder"]
        logging.info(f"  Creadores: {len(creators)}")

        selected_creators = creators

        for creator in selected_creators:
            cname = creator["name"]
            # Try direct files first; if none, try recursive
            photos, videos = collect_direct_files(creator["id"])
            if not photos and not videos:
                photos, videos = collect_files_recursive(creator["id"])
            if not photos and not videos:
                continue

            selected_photos = random.sample(photos, min(MAX_PHOTOS, len(photos))) if photos else []
            selected_videos = random.sample(videos, min(MAX_VIDEOS, len(videos))) if videos else []
            if not selected_photos and not selected_videos:
                continue

            # Create creator folder in demo category
            demo_creator = create_or_get_folder(drive, cname, demo_cat)
            total_creators += 1

            for f in selected_photos:
                try:
                    copy_file(drive, f["id"], demo_creator)
                    total_photos += 1
                except Exception as e:
                    logging.error(f"    Error copiando foto {f.get('name','?')}: {e}")

            for f in selected_videos:
                try:
                    copy_file(drive, f["id"], demo_creator)
                    total_videos += 1
                except Exception as e:
                    logging.error(f"    Error copiando video {f.get('name','?')}: {e}")

            try:
                create_demo_txt(drive, demo_creator, cname)
            except Exception as e:
                logging.error(f"    Error creando _SOLO_DEMO.txt: {e}")

        # Add _SOLO_DEMO.txt at category level too
        try:
            create_demo_txt(drive, demo_cat, f"Categoria {cat_name} (DEMO)")
        except Exception as e:
            pass

        logging.info(f"  Creadores en demo: {len(selected_creators)}")
        logging.info(f"  Fotos: {total_photos} | Videos: {total_videos} (acumulado)")

    print(f"\n{'='*50}")
    print(f"RESUMEN FINAL")
    print(f"  Categorias: {total_categories}")
    print(f"  Creadores con demo: {total_creators}")
    print(f"  Fotos copiadas: {total_photos}")
    print(f"  Videos copiados: {total_videos}")
    print(f"\n  DEMO_FOLDER_ID={demo_parent_id}")
    print(f"\nAgrega DEMO_FOLDER_ID a:")
    print(f"  1. config.py")
    print(f"  2. Render dashboard (Environment Variables)")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
