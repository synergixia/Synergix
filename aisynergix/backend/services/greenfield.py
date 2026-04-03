# aisynergix/backend/services/greenfield.py
"""
Cliente Python para BNB Greenfield Mainnet.
TODAS las operaciones usan el SDK oficial @bnb-chain/greenfield-js-sdk
via Node.js bridge (subprocess). No hay HTTP manual — el SDK maneja
autenticación, endpoints del SP y firma de transacciones.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime
from typing import Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

logger = logging.getLogger("synergixai.greenfield")

BUCKET_NAME:   str = os.getenv("GF_BUCKET",   "synergixai")
GF_RPC_URL:    str = os.getenv("GF_RPC_URL",  "https://greenfield-chain.bnbchain.org")
GF_CHAIN_ID:   str = os.getenv("GF_CHAIN_ID", "1017")
UPLOAD_JS_PATH: str = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "upload.js")
)


def _run_node(script: str, timeout: int = 30) -> dict:
    """
    Ejecuta un script Node.js inline y retorna el resultado parseado.
    Espera que el script imprima __RESULT__:{json} en stdout.
    """
    res = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )
    if res.stderr.strip():
        logger.debug("GF node stderr: %s", res.stderr.strip()[:300])
    if res.returncode != 0:
        raise Exception(f"Node exit {res.returncode}: {res.stderr.strip()[:250]}")
    for line in res.stdout.split("\n"):
        if line.startswith("__RESULT__:"):
            return json.loads(line.split("__RESULT__:")[1])
    raise Exception(f"Sin __RESULT__: {res.stdout[:200]}")


class GreenfieldClient:
    """
    Cliente para BNB Greenfield — usa SDK JS oficial para todas las operaciones.
    list_objects y headObject usan el SDK directamente (no HTTP manual).
    """

    def __init__(self) -> None:
        self._js = UPLOAD_JS_PATH.replace("\\", "/")
        logger.info("🔗 GreenfieldClient inicializado → bucket: %s | js: %s",
                    BUCKET_NAME, self._js)

    # ── Sharding helpers ──────────────────────────────────────────────────────

    @staticmethod
    def build_object_name(user_id: int | str, ts: Optional[int] = None) -> str:
        month = datetime.now().strftime("%Y-%m")
        ts = ts or int(time.time() * 1000)
        return f"aisynergix/aportes/{month}/{user_id}_{ts}.txt"

    # ── list_objects via SDK JS ───────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    async def list_objects(
        self, prefix: str = "aisynergix/aportes/", max_keys: int = 200
    ) -> list[dict[str, Any]]:
        """
        Lista objetos del bucket usando client.object.listObjects del SDK JS.
        Retorna lista de {name, size, last_modified}.
        """
        script = f"""
require('dotenv').config({{ path: require('path').join(__dirname, '..', '.env') }});
const {{ Client }} = require('@bnb-chain/greenfield-js-sdk');
const client = Client.create(
  process.env.GF_RPC_URL  || '{GF_RPC_URL}',
  process.env.GF_CHAIN_ID || '{GF_CHAIN_ID}'
);
const bucket = process.env.GF_BUCKET || '{BUCKET_NAME}';

(async () => {{
  try {{
    const res = await client.object.listObjects({{
      bucketName: bucket,
      query: new URLSearchParams({{ prefix: '{prefix}', 'max-keys': '{max_keys}' }}),
    }});
    const objects = (res.body && res.body.GfSpListObjectsByBucketNameResponse)
      ? res.body.GfSpListObjectsByBucketNameResponse.Objects || []
      : [];
    const result = objects.map(o => ({{
      name:          o.ObjectInfo ? o.ObjectInfo.ObjectName  : (o.object_name || ''),
      size:          o.ObjectInfo ? o.ObjectInfo.PayloadSize : (o.payload_size || 0),
      last_modified: o.ObjectInfo ? o.ObjectInfo.CreateAt    : '',
      tags:          o.ObjectInfo && o.ObjectInfo.Tags ? o.ObjectInfo.Tags.Tags || [] : [],
    }}));
    console.log('__RESULT__:' + JSON.stringify(result));
  }} catch(e) {{
    console.error('list_objects error:', e.message);
    console.log('__RESULT__:[]');
  }}
}})();
"""
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, lambda: _run_node(script, timeout=25))
            objects = raw if isinstance(raw, list) else []
            logger.info("📦 list_objects prefix='%s' → %d objetos", prefix, len(objects))
            return objects
        except Exception as exc:
            logger.warning("⚠️ list_objects error: %s", exc)
            return []

    # ── head_object via SDK JS ────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6), reraise=True)
    async def head_object(self, object_name: str) -> dict[str, Any]:
        """
        headObject via SDK JS — retorna tags on-chain del objeto.
        """
        script = f"""
require('dotenv').config({{ path: require('path').join(__dirname, '..', '.env') }});
const {{ Client }} = require('@bnb-chain/greenfield-js-sdk');
const client = Client.create(
  process.env.GF_RPC_URL  || '{GF_RPC_URL}',
  process.env.GF_CHAIN_ID || '{GF_CHAIN_ID}'
);
const bucket = process.env.GF_BUCKET || '{BUCKET_NAME}';
client.object.headObject(bucket, '{object_name.replace("'", "\\'")}')
  .then(res => {{
    const info = res.objectInfo || {{}};
    const tags = (info.tags && info.tags.tags) ? info.tags.tags : [];
    const meta = {{}};
    tags.forEach(t => {{ meta[t.key] = t.value; }});
    console.log('__RESULT__:' + JSON.stringify({{
      exists:        true,
      object_name:   '{object_name}',
      size:          info.payloadSize || 0,
      tags:          tags,
      meta:          meta,
      summary:       meta.summary   || meta['ai-summary'] || '',
      quality_score: parseInt(meta.score ? meta.score.split('|')[0] : (meta['quality-score'] || '5')) || 5,
      knowledge_tag: meta.score ? (meta.score.split('|')[2] || 'general') : (meta['knowledge-tag'] || 'general'),
      user_id:       meta.meta ? (meta.meta.split('uid:')[1] || '').split('|')[0] : (meta['user-id'] || ''),
    }}));
  }})
  .catch(e => {{
    console.log('__RESULT__:' + JSON.stringify({{ exists: false, object_name: '{object_name}' }}));
  }});
"""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, lambda: _run_node(script, timeout=15))
        except Exception as exc:
            logger.warning("⚠️ head_object '%s': %s", object_name, exc)
            return {"exists": False, "object_name": object_name, "summary": "", "quality_score": 0}

    # ── get_object via SDK JS ─────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    async def get_object(self, object_name: str) -> str:
        """
        Descarga el contenido de un objeto usando client.object.getObject del SDK.
        """
        obj_esc = object_name.replace("'", "\\'")
        script = f"""
require('dotenv').config({{ path: require('path').join(__dirname, '..', '.env') }});
const {{ Client }} = require('@bnb-chain/greenfield-js-sdk');
const {{ ethers }} = require('ethers');
const client = Client.create(
  process.env.GF_RPC_URL  || '{GF_RPC_URL}',
  process.env.GF_CHAIN_ID || '{GF_CHAIN_ID}'
);
const bucket  = process.env.GF_BUCKET || '{BUCKET_NAME}';
let pk = process.env.PRIVATE_KEY || '';
if (!pk.startsWith('0x')) pk = '0x' + pk;

(async () => {{
  try {{
    const res = await client.object.getObject(
      {{ bucketName: bucket, objectName: '{obj_esc}' }},
      {{ type: 'ECDSA', privateKey: pk }}
    );
    const buf = Buffer.from(await res.body.arrayBuffer());
    console.log('__RESULT__:' + JSON.stringify({{ content: buf.toString('utf8') }}));
  }} catch(e) {{
    console.log('__RESULT__:' + JSON.stringify({{ content: '' }}));
  }}
}})();
"""
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, lambda: _run_node(script, timeout=30))
            return result.get("content", "")
        except Exception as exc:
            logger.warning("⚠️ get_object '%s': %s", object_name, exc)
            return ""

    # ── get_aportes_metadata (para RAG) ──────────────────────────────────────

    async def get_aportes_metadata(
        self,
        prefix: str = "aisynergix/aportes/",
        min_quality: int = 5,
        max_items: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Lista aportes y lee sus tags via headObject. Filtra por calidad mínima.
        """
        objects = await self.list_objects(prefix=prefix, max_keys=200)
        objects = sorted(objects, key=lambda x: x.get("last_modified", ""), reverse=True)
        objects = objects[:max_items]

        sem = asyncio.Semaphore(5)

        async def fetch_meta(obj: dict) -> Optional[dict]:
            async with sem:
                name = obj.get("name", "")
                if not name:
                    return None
                # Los tags pueden venir directo del listObjects
                if obj.get("tags"):
                    meta = {t["key"]: t["value"] for t in obj["tags"]}
                    score = int(meta.get("score", "5").split("|")[0] if "score" in meta else meta.get("quality-score", "5")) or 5
                else:
                    head = await self.head_object(name)
                    meta = head.get("meta", {})
                    score = head.get("quality_score", 5)

                if score < min_quality:
                    return None
                return {
                    "object_name":   name,
                    "summary":       meta.get("summary", meta.get("ai-summary", "")),
                    "quality_score": score,
                    "knowledge_tag": meta.get("score", "|general").split("|")[2] if "score" in meta else meta.get("knowledge-tag", "general"),
                    "user_id":       meta.get("meta", "uid:").split("uid:")[1].split("|")[0] if "meta" in meta else "",
                }

        tasks = [fetch_meta(obj) for obj in objects]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        valid = [r for r in results if r is not None]
        logger.info("📊 RAG metadata: %d/%d aportes calidad>=%d", len(valid), len(objects), min_quality)
        return valid

    # ── get_user_profile ──────────────────────────────────────────────────────

    async def get_user_profile(self, user_id: int | str) -> dict[str, Any]:
        """Lee el perfil del usuario desde users/{uid} via headObject."""
        meta = await self.head_object(f"aisynergix/users/{user_id}")
        if meta.get("exists"):
            raw = meta.get("meta", {})
            role_lang = raw.get("role", "user|es").split("|")
            return {
                "exists":        True,
                "role":          role_lang[0] if role_lang else "user",
                "lang":          role_lang[1] if len(role_lang) > 1 else "es",
                "points":        int(raw.get("points", 0) or 0),
                "contributions": int(raw.get("contributions", 0) or 0),
            }
        return {"exists": False, "role": "user", "lang": "es", "points": 0, "contributions": 0}

    # ── upload_object_sync (bridge a upload.js) ───────────────────────────────

    def upload_object_sync(
        self, content: str, user_id: int | str, metadata: Optional[dict] = None
    ) -> dict[str, Any]:
        """Sube un objeto via upload.js (Node.js bridge)."""
        from aisynergix.backend.services.greenfield import _run_node
        meta_json = json.dumps(metadata or {})
        object_name = self.build_object_name(user_id)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        js_esc  = self._js.replace("'", "\\'")
        tmp_esc = tmp_path.replace("\\", "/").replace("'", "\\'")
        obj_esc = object_name.replace("'", "\\'")

        script = f"""
const {{ uploadToGreenfield }} = require('{js_esc}');
const fs = require('fs');
const content = fs.readFileSync('{tmp_esc}', 'utf8');
const meta = {meta_json};
uploadToGreenfield(content, '{user_id}', '{obj_esc}', meta)
  .then(r => {{ console.log('__RESULT__:' + JSON.stringify(r)); process.exit(0); }})
  .catch(e => {{ console.error('__ERROR__:' + e.message); process.exit(1); }});
"""
        try:
            result = _run_node(script, timeout=120)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return result
        except Exception as exc:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            raise

    async def close(self) -> None:
        pass  # No hay conexiones persistentes que cerrar


# ── Singleton ─────────────────────────────────────────────────────────────────
greenfield_client = GreenfieldClient()
