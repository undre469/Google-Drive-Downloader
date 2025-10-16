# hybrid_main.py
from __future__ import annotations
import os
import io
import json
import math
import asyncio
import aiohttp
import multiprocessing
from multiprocessing import Pool, Manager
from typing import List, Dict, Optional, Callable, Generator
from concurrent.futures import ThreadPoolExecutor

# Google auth / Drive API imports (for initial auth & metadata fetching)
import pickle
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuth2Credentials

# ---------------- CONFIG ----------------
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
CHUNK_SIZE = 1024 * 64  # 64 KB per read from aiohttp stream
# ----------------------------------------

def authenticate_and_export_credentials(credentials_json_path: str, token_pickle_path: Optional[str] = None):
    """
    Authenticate once using InstalledAppFlow and return a serializable credentials payload
    that worker processes can use to refresh tokens without user interaction.
    Also returns an authenticated Drive service (for listing files).
    """
    if token_pickle_path is None:
        token_pickle_path = os.path.join(os.path.dirname(credentials_json_path), 'token.pickle')

    creds = None
    if os.path.exists(token_pickle_path):
        with open(token_pickle_path, 'rb') as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_json_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_pickle_path, 'wb') as f:
            pickle.dump(creds, f)

    # Build Drive service for metadata listing
    service = build('drive', 'v3', credentials=creds)

    # Export necessary pieces so child processes can re-create a refreshable Credentials object:
    payload = {
        'token': creds.token,
        'refresh_token': getattr(creds, 'refresh_token', None),
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes
    }
    return service, payload

def list_all_drive_files(service) -> List[Dict]:
    """List files and return minimal metadata list for download tasks."""
    files = []
    page_token = None
    while True:
        response = service.files().list(
            q="'me' in owners",
            fields="nextPageToken, files(id, name, mimeType, parents, size)",
            pageToken=page_token,
            pageSize=1000
        ).execute()
        files.extend(response.get('files', []))
        page_token = response.get('nextPageToken', None)
        if not page_token:
            break
    return files

def _chunkify(lst: List, n: int) -> List[List]:
    """Split list into n approximately-equal chunks."""
    if n <= 0:
        return [lst]
    k, m = divmod(len(lst), n)
    chunks = []
    start = 0
    for i in range(n):
        size = k + (1 if i < m else 0)
        chunks.append(lst[start:start+size])
        start += size
    return chunks

# ---------------- Worker process functions ----------------

def _recreate_creds_from_payload(payload: dict) -> OAuth2Credentials:
    """
    Recreate google.oauth2.credentials.Credentials object from the payload.
    The credentials object can refresh itself using Request() when expired.
    """
    creds = OAuth2Credentials(
        token=payload.get('token'),
        refresh_token=payload.get('refresh_token'),
        token_uri=payload.get('token_uri'),
        client_id=payload.get('client_id'),
        client_secret=payload.get('client_secret'),
        scopes=payload.get('scopes')
    )
    return creds

async def _download_file_aio(session: aiohttp.ClientSession, url: str, headers: dict, dest_path: str):
    """
    Stream-download a file from `url` using aiohttp session and write to `dest_path`.
    """
    tmp_path = dest_path + '.part'
    os.makedirs(os.path.dirname(dest_path) or '.', exist_ok=True)
    async with session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        # write in streaming fashion
        with open(tmp_path, 'wb') as fd:
            async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                if not chunk:
                    break
                fd.write(chunk)
    # atomic replace
    os.replace(tmp_path, dest_path)
    return dest_path

def _map_export_mime(mime_type: str) -> Optional[str]:
    """
    Map google-apps mime types to export mime types. Extend as needed.
    """
    if mime_type == 'application/vnd.google-apps.document':
        return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    if mime_type == 'application/vnd.google-apps.spreadsheet':
        return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    if mime_type == 'application/vnd.google-apps.presentation':
        return 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
    # fallback to PDF for unknown google-apps types
    if mime_type.startswith('application/vnd.google-apps'):
        return 'application/pdf'
    return None

def _worker_process_main(credentials_payload: dict,
                         file_tasks: List[Dict],
                         output_folder: str,
                         queue: "multiprocessing.managers.SyncManager().Queue",
                         tasks_per_process: int = 24,
                         process_id: int = 0):
    """
    Executed inside each spawned process. Reconstruct credentials, run an asyncio loop,
    download assigned files concurrently with aiohttp, and push progress messages into `queue`.
    """
    # Recreate refreshable credentials
    creds = _recreate_creds_from_payload(credentials_payload)

    # async worker
    async def _process_chunk():
        # Create an aiohttp session; ensure token is valid before starting
        # Refresh token if expired
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())

        headers = {'Authorization': f'Bearer {creds.token}'}

        connector = aiohttp.TCPConnector(limit=tasks_per_process)  # limit simultaneous connections
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60*10)  # generous read timeout
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            sem = asyncio.Semaphore(tasks_per_process)

            async def _download_task(task_meta):
                file_id = task_meta['id']
                name = task_meta['name']
                mime = task_meta.get('mimeType', '')
                # Build URL
                if mime.startswith('application/vnd.google-apps'):
                    export_mime = _map_export_mime(mime)
                    if not export_mime:
                        msg = f"Skipped (no export type): {name}"
                        queue.put(msg)
                        return msg
                    url = f'https://www.googleapis.com/drive/v3/files/{file_id}/export?mimeType={export_mime}'
                    # ensure extension for saved file
                    ext = {
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
                        'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
                        'application/pdf': '.pdf'
                    }.get(export_mime, '')
                else:
                    # direct media download
                    url = f'https://www.googleapis.com/drive/v3/files/{file_id}?alt=media'
                    # try preserve extension from name (if exists)
                    ext = ''
                # prepare final path
                # sanitize name for filesystem
                safe_name = "".join(c for c in name if c not in "\/:*?\"<>|")
                dest = os.path.join(output_folder, safe_name + ext)

                # refresh token if about to expire: check validity
                nonlocal creds
                if not creds.valid and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                    except Exception as e:
                        queue.put(f"WARNING: Refresh failed for process {process_id}: {e}")
                        # proceed with existing token if possible

                # update header with possibly refreshed token
                local_headers = {'Authorization': f'Bearer {creds.token}'}

                try:
                    # use semaphore to limit concurrency
                    async with sem:
                        await _download_file_aio(session, url, local_headers, dest)
                    msg = f"Downloaded: {dest}"
                    queue.put(msg)
                    return msg
                except Exception as e:
                    msg = f"Error downloading {name}: {e}"
                    queue.put(msg)
                    return msg

            # schedule download tasks
            tasks = [asyncio.create_task(_download_task(meta)) for meta in file_tasks]
            # wait for completion
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results

    # Run the async worker loop
    try:
        asyncio.run(_process_chunk())
    except Exception as e:
        queue.put(f"WARNING: Process {process_id} failed with error: {e}")

# ---------------- Public API ----------------

def mirror_drive_hybrid(credentials_json_path: str,
                        output_folder: str,
                        max_processes: Optional[int] = None,
                        tasks_per_process: int = 24,
                        progress_callback: Optional[Callable[[int, int, str], None]] = None
                        ) -> Generator[str, None, None]:
    """
    High-level generator that:
      - authenticates once
      - lists Drive files
      - starts multiple processes, each running async downloads
      - yields messages as download results become available

    Usage:
        for msg in mirror_drive_hybrid('credentials.json', './out'):
            print(msg)
    """
    # 1) Authenticate & get Drive service + serializable credential payload
    service, creds_payload = authenticate_and_export_credentials(credentials_json_path)

    # 2) List files
    all_files = list_all_drive_files(service)
    # Filter out folders and optionally filter other things
    targets = [ { 'id': f['id'], 'name': f['name'], 'mimeType': f.get('mimeType',''), 'size': f.get('size') } 
               for f in all_files if f.get('mimeType') != 'application/vnd.google-apps.folder' ]

    total = len(targets)
    if total == 0:
        yield "No files found."
        return

    # 3) Decide number of processes
    cpu_count = multiprocessing.cpu_count()
    max_processes = max_processes or cpu_count
    max_processes = min(max_processes, cpu_count)

    # 4) Split tasks into chunks for each process
    chunks = _chunkify(targets, max_processes)

    manager = Manager()
    queue = manager.Queue()

    # 5) Launch worker processes via multiprocessing.Pool; each process will put messages into queue
    pool = Pool(processes=max_processes)
    for i, chunk in enumerate(chunks):
        if not chunk:
            continue
        pool.apply_async(_worker_process_main, args=(creds_payload, chunk, output_folder, queue, tasks_per_process, i))

    # 6) Collect results as they appear in the queue
    completed = 0
    while completed < total:
        try:
            msg = queue.get(timeout=1.0)  # wait for a message
            # If message is a download/completion message format we increment completed.
            # We treat lines starting with 'Downloaded:' or 'Skipped' or 'Error' as one file completion.
            if msg.startswith("Downloaded:") or msg.startswith("Skipped") or msg.startswith("Error") or msg.startswith("WARNING:"):
                completed += 1
            # send progress callback
            if progress_callback:
                # pass the 'current file' as the last part of the message for GUI use
                current_file = msg.split(": ", 1)[-1] if ": " in msg else msg
                progress_callback(completed, total, current_file)
            yield msg
        except Exception:
            # timeout — continue to check if processes are still running
            # When nothing is in the queue we loop — but still check if all worker processes finished
            if not any(p.is_alive() for p in pool._pool):
                # If no alive workers and queue is empty, break
                break

    # 7) cleanup
    try:
        pool.close()
        pool.join()
    except Exception:
        pass

    # Drain any remaining messages
    while not queue.empty():
        try:
            yield queue.get_nowait()
        except Exception:
            break

    yield "All worker processes finished."
