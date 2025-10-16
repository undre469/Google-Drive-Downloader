from __future__ import print_function
import os
import io
import pickle
import asyncio
import concurrent.futures
from functools import partial
from multiprocessing import cpu_count
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
MAX_PATH_LEN = 240 


# ------------------ Authentication ------------------ #
def authenticate(credentials_path, token_path=None):
    """
    Authenticate and return Drive API service. Creates/uses token.pickle next to credentials by default.
    """
    if token_path is None:
        token_path = os.path.join(os.path.dirname(credentials_path), 'token.pickle')

    creds = None
    if os.path.exists(token_path):
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)

    service = build('drive', 'v3', credentials=creds)
    return service


# ------------------ Drive File Utilities ------------------ #
def get_all_files(service):
    """
    Fetch all files and folders visible to the user, including 'Shared with me' and shared drives.
    Returns a list of file dicts.
    """
    files = []
    page_token = None
    while True:
        response = service.files().list(
            q="trashed = false",
            corpora="user",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="nextPageToken, files(id, name, mimeType, parents, driveId, owners, size, shared)",
            pageToken=page_token,
            pageSize=1000
        ).execute()
        files.extend(response.get('files', []))
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return files


def build_folder_map(files):
    """
    Build a mapping of folder_id -> { name, parent } for every folder found.
    This map will be used to reconstruct folder paths.
    """
    folder_map = {}
    for f in files:
        if f.get('mimeType') == 'application/vnd.google-apps.folder':
            folder_map[f['id']] = {
                'name': f.get('name', 'Unnamed Folder'),
                'parent': f.get('parents', [None])[0] if f.get('parents') else None
            }
    return folder_map


def sanitize_name(name):
    """
    Replace filesystem-illegal chars and trim length. Cross-platform safe.
    """
    if not name:
        return "untitled"
    invalid_chars = '<>:"/\\|?*\0'
    clean = ''.join(ch if ch not in invalid_chars else '_' for ch in name)
    clean = clean.strip()
    
    if len(clean) > MAX_PATH_LEN:
        # try to preserve extension
        base, ext = os.path.splitext(clean)
        ext = ext[:20]  # keep extension short
        clean = base[:MAX_PATH_LEN - len(ext)] + ext
    return clean or "untitled"


def resolve_path_for_item(item, folder_map, root_folder):
    """
    Build the local directory path for an item by following parents up to root.
    If parents are missing (e.g., file in "Shared with me" without parent metadata), put it in root_folder/Shared/
    """
    parts = []
    parent = item.get('parents', [None])[0] if item.get('parents') else None

    
    while parent and parent in folder_map:
        folder = folder_map[parent]
        parts.insert(0, sanitize_name(folder['name']))
        parent = folder.get('parent')

    
    if not parts:
        
        if item.get('shared'):
            parts = ['Shared']
        else:
            parts = ['My Drive']

    local_dir = os.path.join(root_folder, *parts)
    os.makedirs(local_dir, exist_ok=True)
    return local_dir


def get_local_path(item, folder_map, root_folder):
    """
    Return the final local path (without extension for google-apps types) to save the file.
    Uses resolve_path_for_item() to ensure folders exist.
    """
    local_dir = resolve_path_for_item(item, folder_map, root_folder)
    file_name = sanitize_name(item.get('name', 'untitled'))
    return os.path.join(local_dir, file_name)


# ------------------ Download Logic ------------------ #
def download_file(credentials_path, item, folder_map, root_folder):
    """
    Download or export a single file.
    Each worker calls authenticate() which will reuse token.pickle if present (no repeated OAuth prompt).
    """
    service = authenticate(credentials_path)
    mime_type = item.get('mimeType', '')
    file_id = item.get('id')
    base_local = get_local_path(item, folder_map, root_folder)

    try:
        if mime_type.startswith('application/vnd.google-apps'):
            
            if mime_type == 'application/vnd.google-apps.document':
                request = service.files().export_media(fileId=file_id,
                                                       mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                                       supportsAllDrives=True)
                ext = '.docx'
            elif mime_type == 'application/vnd.google-apps.spreadsheet':
                request = service.files().export_media(fileId=file_id,
                                                       mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                                       supportsAllDrives=True)
                ext = '.xlsx'
            elif mime_type == 'application/vnd.google-apps.presentation':
                request = service.files().export_media(fileId=file_id,
                                                       mimeType='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                                                       supportsAllDrives=True)
                ext = '.pptx'
            else:
                # fallback to PDF for other google-apps types
                request = service.files().export_media(fileId=file_id, mimeType='application/pdf', supportsAllDrives=True)
                ext = '.pdf'
        else:
            # Regular binary/media file
            request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
            
            _, ext = os.path.splitext(item.get('name', ''))
        final_path = base_local + ext
        # avoid overwriting existing files
        if os.path.exists(final_path):
            return f"Skipped (exists): {final_path}"

        # Stream download to file
        fh = io.FileIO(final_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return f"Downloaded: {final_path}"
    except Exception as e:
        return f"Error downloading {item.get('name', 'unknown')}: {e}"


# ------------------ Async + Multiprocessing Orchestration ------------------ #
async def mirror_drive_async(credentials_path, output_folder, max_threads=8, progress_callback=None):
    """
    High-level async generator that downloads files using a process pool with concurrent workers.
    Yields per-file messages so GUI can display them live.
    """
    # 1) initial authentication + metadata fetch (creates token.pickle)
    service = authenticate(credentials_path)
    files = get_all_files(service)
    folder_map = build_folder_map(files)

    # Consider all non-folder items as targets
    targets = [f for f in files if f.get('mimeType') != 'application/vnd.google-apps.folder']

    total = len(targets)
    if total == 0:
        yield "No files found to download."
        return

    completed = 0
 
    processes = min(cpu_count(), max(1, int(os.getenv('DRIVE_MIRROR_PROCS', cpu_count()))))

    
    loop = asyncio.get_running_loop()
    executor = concurrent.futures.ProcessPoolExecutor(max_workers=processes)

    
    tasks = [loop.run_in_executor(executor, partial(download_file, credentials_path, item, folder_map, output_folder))
             for item in targets]

    
    for coro in asyncio.as_completed(tasks):
        result = await coro
        completed += 1
        # progress_callback gets (completed, total, last_message)
        if progress_callback:
            try:
                progress_callback(completed, total, result)
            except Exception:
                pass
        yield result

    
    executor.shutdown(wait=True)


def mirror_drive(credentials_path, output_folder, max_threads=8, progress_callback=None):
    """
    Synchronous wrapper for GUI compatibility. Returns generator-like behavior by collecting yielded items.
    """
    async def run_and_collect():
        async for item in mirror_drive_async(credentials_path, output_folder, max_threads, progress_callback):
            yield item

    # run the async generator and collect items via an event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async_gen = run_and_collect()
    results = []
    try:
        while True:
            item = loop.run_until_complete(async_gen.__anext__())
            results.append(item)
    except StopAsyncIteration:
        pass
    finally:
        loop.close()
    # Return an iterator over results so your GUI loop "for update in mirror_drive(...)" still works
    for r in results:
        yield r
