
import hashlib
import json
import logging
import os
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct

load_dotenv()
logger = logging.getLogger("synergix.greenfield")

# ── Config ─────────────────────────────────────────────────────────────────────
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
SALT        = os.getenv("SALT", "synergix_ghost_protocol_v1_super_secret")
BUCKET      = os.getenv("BUCKET_NAME", "synergixai")
SP_URL      = os.getenv("SP_URL", "https://gnfd-mainnet-sp1.bnbchain.org")

if PRIVATE_KEY:
    _pk     = PRIVATE_KEY if PRIVATE_KEY.startswith("0x") else "0x" + PRIVATE_KEY
    account = Account.from_key(_pk)
    ADDRESS = account.address
    logger.info("🔑 Wallet: %s...", ADDRESS[:12])
else:
    account = None
    ADDRESS = ""
    logger.warning("⚠️  PRIVATE_KEY no configurada")


def get_ghost_id(uid: str) -> str:
    """SHA-256(uid + salt) → hash anónimo de 32 chars. UID real NUNCA en GF."""
    return hashlib.sha256(f"{uid}{SALT}".encode()).hexdigest()[:32]


# ══════════════════════════════════════════════════════════════════════════════
# FIRMA AWS4-ECDSA
# ══════════════════════════════════════════════════════════════════════════════
def _sign(method: str, url_path: str, payload: bytes = b"",
          extra: dict = None) -> dict:
    if not account:
        return {}
    t    = datetime.now(timezone.utc)
    ts   = t.strftime("%Y%m%dT%H%M%SZ")
    ds   = t.strftime("%Y%m%d")
    host = urllib.parse.urlparse(SP_URL).netloc

    canon_uri  = urllib.parse.quote(url_path, safe="/~")
    body_hash  = hashlib.sha256(payload).hexdigest()
    canon_hdrs = f"host:{host}\nx-amz-date:{ts}\n"
    sign_hdrs  = "host;x-amz-date"
    canon_req  = f"{method}\n{canon_uri}\n\n{canon_hdrs}\n{sign_hdrs}\n{body_hash}"

    scope   = f"{ds}/greenfield/s3/aws4_request"
    to_sign = (
        f"AWS4-ECDSA-SHA256\n{ts}\n{scope}\n"
        + hashlib.sha256(canon_req.encode()).hexdigest()
    )
    sig = account.sign_message(encode_defunct(text=to_sign)).signature.hex()
    auth = (
        f"AWS4-ECDSA-SHA256 Credential={ADDRESS}/{scope},"
        f" SignedHeaders={sign_hdrs}, Signature={sig}"
    )
    hdrs = {"Host": host, "x-amz-date": ts, "Authorization": auth}
    if extra:
        hdrs.update(extra)
    return hdrs


# ══════════════════════════════════════════════════════════════════════════════
# USUARIOS — Nodo Fantasma (0 bytes)
# ══════════════════════════════════════════════════════════════════════════════
async def create_user(uid: str) -> bool:
    """
    Crea el archivo 0 bytes en users/{ghost_id} con tags iniciales.

    FIX: El problema anterior era que update_user_metadata() usaba ?tagging
    sobre un objeto que no existía. Greenfield (y S3) no permiten setear tags
    de un objeto inexistente. Ahora se crea el objeto primero con PUT 0 bytes
    e incluimos los meta-datos iniciales en los headers x-amz-meta-*.
    """
    if not account:
        return False

    gid  = get_ghost_id(uid)
    path = f"/{BUCKET}/aisynergix/users/{gid}"

    extra = {
        "Content-Type":           "application/octet-stream",
        "Content-Length":         "0",
        "x-amz-meta-points":      "0",
        "x-amz-meta-rank":        "iniciado",
        "x-amz-meta-fsm-state":   "IDLE",
        "x-amz-meta-daily-quota": "5",
        "x-amz-meta-language":    "es",
        "x-amz-meta-impact":      "0",
        "x-amz-meta-created-at":  str(int(time.time())),
    }
    hdrs = _sign("PUT", path, b"", extra)

    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.put(f"{SP_URL}{path}", headers=hdrs, content=b"")
        if r.status_code in (200, 201):
            logger.info("✅ Usuario creado GF: users/%s...", gid[:12])
            return True
        logger.error("❌ create_user HTTP %d: %s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        logger.error("❌ create_user: %s", e)
        return False


async def get_user_metadata(uid: str) -> dict | None:
    """
    HEAD a users/{ghost_id} → lee x-amz-meta-* headers.
    Retorna None si el usuario no existe (404).
    """
    if not account:
        return None

    gid  = get_ghost_id(uid)
    path = f"/{BUCKET}/aisynergix/users/{gid}"
    hdrs = _sign("HEAD", path)

    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.head(f"{SP_URL}{path}", headers=hdrs)

        if r.status_code == 404:
            return None
        if r.status_code != 200:
            logger.warning("⚠️  get_user_metadata HTTP %d", r.status_code)
            return None

        # Leer meta headers (Greenfield devuelve x-amz-meta-{key}: {value})
        meta: dict = {}
        for k, v in r.headers.items():
            lk = k.lower()
            if lk.startswith("x-amz-meta-"):
                meta[lk[len("x-amz-meta-"):]] = v

        if not meta:
            logger.warning("⚠️  HEAD sin meta-headers para %s", gid[:12])
            return None

        return {
            "points":       int(meta.get("points",      0)),
            "rank":         meta.get("rank",             "🌱 Iniciado"),
            "fsm_state":    meta.get("fsm-state",        "IDLE"),
            "daily_quota":  int(meta.get("daily-quota",  5)),
            "language":     meta.get("language",         "es"),
            "impact_index": int(meta.get("impact",       0)),
        }
    except Exception as e:
        logger.error("❌ get_user_metadata: %s", e)
        return None


async def update_user_metadata(uid: str, updates: dict) -> bool:
    """
    Actualiza los tags del usuario con re-PUT 0 bytes + nuevos meta headers.
    Esto es el patrón correcto para actualizar metadata en S3-compatible APIs.
    """
    if not account:
        return False

    gid  = get_ghost_id(uid)
    path = f"/{BUCKET}/aisynergix/users/{gid}"

    # Mapear campos del UserContext a nombres de header
    field_map = {
        "points":       "x-amz-meta-points",
        "rank":         "x-amz-meta-rank",
        "fsm_state":    "x-amz-meta-fsm-state",
        "daily_quota":  "x-amz-meta-daily-quota",
        "language":     "x-amz-meta-language",
        "impact_index": "x-amz-meta-impact",
    }
    extra = {
        "Content-Type":          "application/octet-stream",
        "Content-Length":        "0",
        "x-amz-meta-updated-at": str(int(time.time())),
    }
    for key, val in updates.items():
        hdr = field_map.get(key, f"x-amz-meta-{key}")
        extra[hdr] = str(val)

    hdrs = _sign("PUT", path, b"", extra)

    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.put(f"{SP_URL}{path}", headers=hdrs, content=b"")
        ok = r.status_code in (200, 201, 204)
        if not ok:
            logger.warning("⚠️  update_metadata HTTP %d: %s",
                           r.status_code, r.text[:100])
        return ok
    except Exception as e:
        logger.error("❌ update_user_metadata: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# APORTES
# ══════════════════════════════════════════════════════════════════════════════
async def upload_aporte(uid: str, content: str, tags: dict) -> str | None:
    """Sube aporte a aportes/YYYY-MM/{ghost_id}_{ts}.txt."""
    if not account:
        return None

    gid     = get_ghost_id(uid)
    month   = datetime.now(timezone.utc).strftime("%Y-%m")
    ts      = int(time.time())
    path    = f"/{BUCKET}/aisynergix/aportes/{month}/{gid}_{ts}.txt"
    payload = content.encode("utf-8")

    extra = {
        "Content-Type":             "text/plain; charset=utf-8",
        "Content-Length":           str(len(payload)),
        "x-amz-meta-quality-score": str(tags.get("score", 0)),
        "x-amz-meta-category":      str(tags.get("category", "general")),
        "x-amz-meta-impact-index":  "0",
    }
    hdrs = _sign("PUT", path, payload, extra)

    try:
        async with httpx.AsyncClient(timeout=30.0) as cli:
            r = await cli.put(f"{SP_URL}{path}", headers=hdrs, content=payload)
        if r.status_code in (200, 201):
            logger.info("✅ Aporte subido: %d bytes", len(payload))
            return path
        logger.error("❌ upload_aporte HTTP %d: %s", r.status_code, r.text[:150])
        return None
    except Exception as e:
        logger.error("❌ upload_aporte: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# LIST
# ══════════════════════════════════════════════════════════════════════════════
async def list_objects(prefix: str) -> list[str]:
    """Lista objetos bajo un prefijo para auditoría y evolución."""
    if not account:
        return []
    path = f"/{BUCKET}/"
    hdrs = _sign("GET", path)
    keys: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as cli:
            r = await cli.get(f"{SP_URL}{path}", headers=hdrs,
                              params={"prefix": prefix, "max-keys": "200"})
        if r.status_code == 200:
            root = ET.fromstring(r.text)
            ns   = "{http://s3.amazonaws.com/doc/2006-03-01/}"
            for c in root.findall(f".//{ns}Contents") or root.findall("Contents"):
                k = c.find(f"{ns}Key") or c.find("Key")
                if k is not None:
                    keys.append(k.text)
    except Exception as e:
        logger.error("❌ list_objects: %s", e)
    return keys
