import asyncio
import os
import shutil
import urllib.parse
from datetime import datetime, timedelta, timezone
import httpx
from aisynergix.services.greenfield import _sign_v4, SP_URL, BUCKET_NAME

LOG_FILE = "logs/synergix.log"

async def upload_audit(file_path: str, date_str: str) -> bool:
    obj_key = f"aisynergix/logs/{date_str}.log"
    path = f"/{BUCKET_NAME}/{obj_key}"
    domain = urllib.parse.urlparse(SP_URL).netloc
    
    with open(file_path, "rb") as f: payload = f.read()
        
    headers = {
        "Host": domain,
        "Content-Type": "text/plain",
        "x-amz-tagging": f"type=audit&date={date_str}"
    }
    
    auth, date = _sign_v4("PUT", path, headers, payload)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.put(f"{SP_URL}{path}", headers=headers, content=payload, timeout=60.0)
            return resp.status_code == 200
        except: return False

async def log_daemon():
    print("📝 Auditor Inmutable Activo (Ciclo 24h)")
    while True:
        now = datetime.now(timezone.utc)
        target = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        await asyncio.sleep((target - now).total_seconds())
        
        try:
            date_label = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')
            temp_log = f"logs/backup_{date_label}.log"
            
            if os.path.exists(LOG_FILE):
                shutil.copy(LOG_FILE, temp_log)
                open(LOG_FILE, "w").close() 
                
                if await upload_audit(temp_log, date_label):
                    print(f"✅ Auditoría asegurada en DCellar: {date_label}")
                    os.remove(temp_log)
        except Exception as e:
            print(f"⚠️ Error en auditoría: {e}")

if __name__ == "__main__":
    asyncio.run(log_daemon())
