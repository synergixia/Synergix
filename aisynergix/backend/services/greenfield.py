"""
aisynergix/backend/services/greenfield.py
══════════════════════════════════════════════════════════════════════════════
Cliente Python para consultar Tags de Greenfield en <0.01s.

Arquitectura "Cero DB":
  El bot rara vez descarga archivos completos.
  En su lugar, lee los Tags (metadatos on-chain) de los objetos en milisegundos.

Operaciones:
  · head_object(name)      — lee tags sin descargar contenido  ← PRINCIPAL
  · list_objects(prefix)   — lista objetos de una carpeta
  · get_object(name)       — descarga contenido completo (costoso)
  · get_aportes_meta()     — metadatos de todos los aportes para RAG
  · get_user_profile(uid)  — perfil de usuario via HEAD
══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Any, Optional

from tenacity import (
    before_sleep_log, retry, stop_after_attempt, wait_exponential,
)

logger = logging.getLogger("synergix.greenfield")

# ── Config ────────────────────────────────────────────────────────────────────
GF_BUCKET   = os.getenv("GF_BUCKET",   "synergixai")
GF_RPC_URL  = os.getenv("GF_RPC_URL",  "https://greenfield-chain.bnbchain.org")
GF_CHAIN_ID = os.getenv("GF_CHAIN_ID", "1017")
GF_ROOT     = "aisynergix"

# Ruta al upload.js — se calcula desde este archivo
_HERE       = os.path.dirname(os.path.abspath(__file__))
UPLOAD_JS   = os.path.normpath(os.path.join(_HERE, "..", "upload.js"))
ROOT_DIR    = os.path.normpath(os.path.join(_HERE, "..", "..", "..", ".."))


def _node_env() -> dict:
    return {
        **os.environ,
        "GF_BUCKET":      GF_BUCKET,
        "DOTENV_ROOT":    os.path.join(ROOT_DIR, ".env"),
        "DOTENV_BACKEND": os.path.join(os.path.dirname(UPLOAD_JS), ".env"),
        "NODE_PATH":      os.path.join(ROOT_DIR, "node_modules"),
    }

def _run_node(script: str, timeout: int = 20) -> Optional[dict]:
    """Ejecuta un script Node.js inline y retorna el resultado JSON."""
    try:
        res = subprocess.run(
            ["node", "-e", script],
            capture_output=True, text=True,
            timeout=timeout,
            env=_node_env(),
            cwd=ROOT_DIR,
        )
        if res.stderr.strip():
            logger.debug("GF node stderr: %s", res.stderr.strip()[:150])
        if res.returncode != 0:
            raise RuntimeError(f"node exit {res.returncode}: {res.stderr.strip()[:150]}")
        for line in res.stdout.splitlines():
            if line.startswith("__RESULT__:"):
                return json.loads(line[len("__RESULT__:"):])
    except Exception as e:
        logger.warning("⚠️ _run_node: %s", e)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT CLASS
# ══════════════════════════════════════════════════════════════════════════════
class GreenfieldClient:
    """
    Cliente Python para operaciones en BNB Greenfield.
    Todas las operaciones usan el SDK JS oficial via subprocess.
    """

    def __init__(self) -> None:
        logger.info("🔗 GreenfieldClient → bucket: %s | root: %s",
                    GF_BUCKET, GF_ROOT)

    # ── HEAD — leer tags sin descargar contenido ──────────────────────────────
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8),
           before_sleep=before_sleep_log(logger, logging.WARNING), reraise=False)
    async def head_object(self, object_name: str) -> dict[str, Any]:
        """
        Operación más barata de Greenfield — solo lee tags on-chain.
        Tiempo típico: <100ms.

        Returns:
            {"exists": bool, "meta": {key: value}, ...}
        """
        obj_esc   = object_name.replace("'", "\\'")
        env_path  = os.path.join(ROOT_DIR, ".env").replace("\\", "/")
        script = (
            f"const {{Client}}=require('@bnb-chain/greenfield-js-sdk');"
            f"require('dotenv').config({{path:'{env_path}'}});"
            f"const client=Client.create(process.env.GF_RPC_URL||'{GF_RPC_URL}','{GF_CHAIN_ID}');"
            f"const bucket=process.env.GF_BUCKET||'{GF_BUCKET}';"
            f"client.object.headObject(bucket,'{obj_esc}')"
            f".then(res=>{{const info=res.objectInfo||{{}};"
            f"const tags=(info.tags&&info.tags.tags)?info.tags.tags:[];"
            f"const meta={{}};tags.forEach(t=>{{meta[t.key]=t.value}});"
            f"console.log('__RESULT__:'+JSON.stringify({{exists:true,meta,size:info.payloadSize||0}}));"
            f"process.exit(0)}})"
            f".catch(()=>{{console.log('__RESULT__:'+JSON.stringify({{exists:false}}));process.exit(0)}});"
        )
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: _run_node(script, timeout=15))
        return result or {"exists": False}

    # ── LIST — listar objetos de una carpeta ──────────────────────────────────
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8),
           before_sleep=before_sleep_log(logger, logging.WARNING), reraise=False)
    async def list_objects(self, prefix: str = "aisynergix/aportes/",
                           max_keys: int = 200) -> list[dict[str, Any]]:
        """Lista objetos del bucket con un prefijo dado."""
        env_path = os.path.join(ROOT_DIR, ".env").replace("\\", "/")
        script = (
            f"const {{Client}}=require('@bnb-chain/greenfield-js-sdk');"
            f"require('dotenv').config({{path:'{env_path}'}});"
            f"const client=Client.create(process.env.GF_RPC_URL||'{GF_RPC_URL}','{GF_CHAIN_ID}');"
            f"const bucket=process.env.GF_BUCKET||'{GF_BUCKET}';"
            f"(async()=>{{"
            f"try{{const res=await client.object.listObjects({{"
            f"bucketName:bucket,"
            f"query:new URLSearchParams({{prefix:'{prefix}','max-keys':'{max_keys}'}}),"
            f"}});"
            f"const objects=(res.body&&res.body.GfSpListObjectsByBucketNameResponse)"
            f"?res.body.GfSpListObjectsByBucketNameResponse.Objects||[]"
            f":[];"
            f"const result=objects.map(o=>({{name:o.ObjectInfo?o.ObjectInfo.ObjectName:'',"
            f"size:o.ObjectInfo?o.ObjectInfo.PayloadSize:0,"
            f"ts:o.ObjectInfo?o.ObjectInfo.CreateAt:'',"
            f"tags:o.ObjectInfo&&o.ObjectInfo.Tags?o.ObjectInfo.Tags.Tags||[]:[]}}"
            f"));"
            f"console.log('__RESULT__:'+JSON.stringify(result));process.exit(0);}}"
            f"catch(e){{console.log('__RESULT__:[]');process.exit(0);}}"
            f"}})();"
        )
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: _run_node(script, timeout=25))
        objects = result if isinstance(result, list) else []
        logger.info("📦 list_objects '%s' → %d objetos", prefix, len(objects))
        return objects

    # ── GET — descargar contenido completo ────────────────────────────────────
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10),
           before_sleep=before_sleep_log(logger, logging.WARNING), reraise=False)
    async def get_object(self, object_name: str) -> str:
        """Descarga el contenido completo de un objeto."""
        obj_esc  = object_name.replace("'", "\\'")
        pk_raw   = os.environ.get("PRIVATE_KEY", "")
        pk       = ("0x" + pk_raw) if pk_raw and not pk_raw.startswith("0x") else pk_raw
        env_path = os.path.join(ROOT_DIR, ".env").replace("\\", "/")
        script = (
            f"const {{Client}}=require('@bnb-chain/greenfield-js-sdk');"
            f"const {{ethers}}=require('ethers');"
            f"require('dotenv').config({{path:'{env_path}'}});"
            f"const client=Client.create(process.env.GF_RPC_URL||'{GF_RPC_URL}','{GF_CHAIN_ID}');"
            f"const bucket=process.env.GF_BUCKET||'{GF_BUCKET}';"
            f"let pk=process.env.PRIVATE_KEY||'';"
            f"if(!pk.startsWith('0x'))pk='0x'+pk;"
            f"(async()=>{{"
            f"try{{const res=await client.object.getObject("
            f"{{bucketName:bucket,objectName:'{obj_esc}'}},"
            f"{{type:'ECDSA',privateKey:pk}});"
            f"const buf=Buffer.from(await res.body.arrayBuffer());"
            f"console.log('__RESULT__:'+JSON.stringify({{content:buf.toString('utf8')}}));"
            f"process.exit(0);}}"
            f"catch(e){{console.log('__RESULT__:'+JSON.stringify({{content:''}}));process.exit(0);}}"
            f"}})();"
        )
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: _run_node(script, timeout=30))
        return (result or {}).get("content", "")

    # ── APORTES META — para RAG ───────────────────────────────────────────────
    async def get_aportes_metadata(
        self,
        prefix:      str = "aisynergix/aportes/",
        min_quality: int = 5,
        max_items:   int = 50,
    ) -> list[dict[str, Any]]:
        """
        Lista aportes y extrae sus tags para el RAG engine.
        Filtra por score mínimo de calidad.
        """
        objects = await self.list_objects(prefix=prefix, max_keys=200)
        objects = sorted(objects, key=lambda x: x.get("ts",""), reverse=True)[:max_items]

        sem = asyncio.Semaphore(5)

        async def _fetch_meta(obj: dict) -> Optional[dict]:
            async with sem:
                name = obj.get("name","")
                if not name: return None
                # Tags pueden venir del listObjects
                tags_raw = obj.get("tags", [])
                if tags_raw:
                    meta = {t["key"]: t["value"] for t in tags_raw}
                else:
                    head = await self.head_object(name)
                    meta = head.get("meta", {})

                # Extraer score
                score_raw = meta.get("quality-score", meta.get("score","5"))
                score     = int(str(score_raw).split("|")[0]) if str(score_raw).split("|")[0].isdigit() else 5
                if score < min_quality:
                    return None

                parts = str(score_raw).split("|")
                return {
                    "object_name":   name,
                    "ai-summary":    meta.get("ai-summary", meta.get("summary",""))[:250],
                    "quality-score": score,
                    "quality-label": parts[1] if len(parts) > 1 else "standard",
                    "knowledge-tag": parts[2] if len(parts) > 2 else meta.get("knowledge-tag","general"),
                    "user-id":       meta.get("user-id",""),
                    "impact":        int(meta.get("impact","0")) if str(meta.get("impact","0")).isdigit() else 0,
                    "lang":          meta.get("lang","es"),
                }

        tasks   = [_fetch_meta(o) for o in objects]
        results = await asyncio.gather(*tasks)
        valid   = [r for r in results if r is not None]
        logger.info("📊 aportes meta: %d/%d calidad>=%d",
                    len(valid), len(objects), min_quality)
        return valid

    # ── USER PROFILE — perfil via HEAD ────────────────────────────────────────
    async def get_user_profile(self, uid: int) -> dict[str, Any]:
        """
        Lee perfil de usuario desde users/{uid_hash} via HEAD.
        Operación barata, sin descargar contenido.
        """
        import hashlib
        uid_h    = hashlib.sha256(f"synergix_salt_{uid}".encode()).hexdigest()[:16]
        obj_name = f"{GF_ROOT}/users/{uid_h}"
        head     = await self.head_object(obj_name)
        if not head.get("exists"):
            return {"exists": False}
        meta      = head.get("meta", {})
        role_lang = meta.get("role","rank_1|es").split("|")
        return {
            "exists":        True,
            "role":          role_lang[0].replace("role:","") if role_lang else "rank_1",
            "lang":          role_lang[1].replace("lang:","") if len(role_lang) > 1 else "es",
            "points":        int(meta.get("points","0").split("|")[0]) if meta.get("points","0").split("|")[0].isdigit() else 0,
            "contributions": int(meta.get("points","0|0").split("|")[1].replace("contrib:","")) if "|" in meta.get("points","") else 0,
        }

    async def close(self) -> None:
        pass  # Sin conexiones persistentes


# ── Singleton ─────────────────────────────────────────────────────────────────
greenfield_client = GreenfieldClient()
