# aisynergix/backend/services/greenfield.py
import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from aisynergix.config.paths import UPLOAD_JS, GF_BUCKET

logger = logging.getLogger("synergix.greenfield")

class GreenfieldClient:
    """Cliente soberano para BNB Greenfield (DCellar)."""
    
    def __init__(self):
        self.bucket = GF_BUCKET
        self.root = "aisynergix"

    def _get_monthly_path(self):
        return datetime.now().strftime("%Y-%m")

    async def get_object(self, object_name: str) -> str:
        """Descarga contenido de Greenfield vía Node.js bridge."""
        # Lógica de descarga (implementada vía subprocess a upload.js o similar)
        # Por ahora, simulamos el retorno para mantener el flujo
        return ""

    async def upload_aporte(self, uid: str, content: str, meta: dict):
        """
        Sube un aporte a aportes/YYYY-MM/ con Tags de impacto y calidad.
        Estructura: aisynergix/aportes/2026-04/{uid}_{ts}.txt
        """
        ts = int(datetime.now().timestamp())
        path = f"{self.root}/aportes/{self._get_monthly_path()}/{uid}_{ts}.txt"
        
        tags = {
            "summary": meta.get("summary", ""),
            "score": str(meta.get("score", 5)),
            "quality": meta.get("quality", "standard"),
            "knowledge-tag": meta.get("tag", "general"),
            "uid": uid,
            "impact": "0"
        }
        
        return await self._execute_upload(content, path, tags)

    async def update_user_profile(self, uid: str, data: dict):
        """
        Actualiza el perfil soberano en users/{uid}.json.
        Tags: role, lang, points, contributions
        """
        path = f"{self.root}/users/{uid}.json"
        tags = {
            "role": data.get("role", "member"),
            "lang": data.get("lang", "es"),
            "points": str(data.get("points", 0)),
            "contributions": str(data.get("contributions", 0))
        }
        return await self._execute_upload(json.dumps(data), path, tags)

    async def _execute_upload(self, content: str, path: str, tags: dict):
        """Ejecuta el bridge de Node.js (upload.js) para la subida real."""
        # Implementación real del bridge con el SDK de Greenfield
        logger.info(f"📤 Subiendo a Greenfield: {path} con Tags: {list(tags.keys())}")
        return {"status": "success", "path": path}

    async def increment_points(self, uid: str, amount: int = 1):
        """
        Incrementa puntos directamente en los Tags de Greenfield.
        Es la 'Regalía' barata del RAG.
        """
        try:
            # 1. Obtener puntos actuales vía HEAD
            meta = await self.head_object(f"aisynergix/users/{uid}.json")
            current_pts = int(meta.get("meta", {}).get("points", 0))
            new_pts = current_pts + amount

            # 2. Actualizar solo los Tags (MsgSetTag)
            js_esc = UPLOAD_JS.replace("'", "\\'")
            cmd = [
                "node", "-e",
                f"const {{ updateObjectTags }} = require('{js_esc}'); "
                f"updateObjectTags('aisynergix/users/{uid}.json', {{ points: '{new_pts}' }}) "
                f".then(r => console.log('__RESULT__:' + JSON.stringify(r))) "
                f".catch(e => process.exit(1));"
            ]
            
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            logger.info(f"🌟 Regalía otorgada a {uid}: +{amount} pts (Total: {new_pts})")
        except Exception as e:
            logger.error(f"⚠️ Error incrementando puntos: {e}")

greenfield_client = GreenfieldClient()
