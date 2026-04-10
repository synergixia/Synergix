import asyncio
import os
import logging
import shutil
import json
import urllib.parse
from datetime import datetime, timedelta, timezone
import httpx
from aisynergix.services.greenfield import _sign_v4, SP_URL, BUCKET_NAME

LOG_DIR = "logs/"
LOCAL_LOG_FILE = "logs/synergix.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [AUDIT] %(message)s")
logger = logging.getLogger("SynergixAudit")

async def upload_log_to_web3(file_path, object_key):
    if not os.path.exists(file_path): return False

    with open(file_path, "rb") as f: payload = f.read()

    path = f"/{BUCKET_NAME}/{object_key}"
    headers = {
        "Host": urllib.parse.urlparse(SP_URL).netloc,
        "Content-Type": "text/plain",
        "x-amz-meta-tags": urllib.parse.quote(json.dumps({"type": "audit_log"}))
    }
    
    auth, amz_date = _sign_v4("PUT", path, headers, payload)
    headers.update({"Authorization": auth, "x-amz-date": amz_date})

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.put(f"{SP_URL}{path}", headers=headers, content=payload, timeout=60.0)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Error subiendo log: {e}")
    return False

async def audit_cycle():
    logger.info("Daemon de Auditoría Iniciado (24h)")
    while True:
        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(days=1)
        next_run = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_run - now).total_seconds()
        
        await asyncio.sleep(wait_seconds)

        try:
            yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')
            object_key = f"aisynergix/logs/{yesterday_str}.log"
            temp_file = f"logs/{yesterday_str}.tmp"

            if os.path.exists(LOCAL_LOG_FILE):
                shutil.copy(LOCAL_LOG_FILE, temp_file)
                open(LOCAL_LOG_FILE, "w").close() # Vacia el log actual
                
                success = await upload_log_to_web3(temp_file, object_key)
                if success:
                    os.remove(temp_file)
                    logger.info(f"Auditoría {yesterday_str} subida exitosamente a DCellar.")
        except Exception as e:
            logger.error(f"Fallo en auditoría: {e}")

if __name__ == "__main__":
    asyncio.run(audit_cycle())
