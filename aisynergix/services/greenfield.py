import os
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from eth_account import Account
from eth_account.messages import encode_defunct

logger = logging.getLogger(__name__)

class GreenfieldError(Exception):
    """Excepción personalizada para errores del cliente BNB Greenfield."""
    pass

class GreenfieldService:
    """
    Cliente asíncrono robusto para BNB Greenfield.
    Actúa como ÚNICA fuente de verdad bajo arquitectura 'Nodo Fantasma' y 'Stateless Absoluto'.
    """
    def __init__(self):
        # Carga de credenciales inquebrantables. El nodo local no almacena nada persistente.
        self.private_key = os.getenv("GREENFIELD_PRIVATE_KEY")
        self.bucket_name = "synergixai"
        
        if not self.private_key:
            logger.warning("GREENFIELD_PRIVATE_KEY no configurado. Operaciones Web3 fallarán si no se setea.")
            self.account = None
        else:
            self.account = Account.from_key(self.private_key)
            
        self.sp_url = os.getenv("GREENFIELD_SP_URL", "https://gnfd-mainnet-sp1.bnbchain.org")

    def _get_signature_headers(self, method: str, path: str) -> Dict[str, str]:
        """Genera los encabezados de autorización GNFD1-ECDSA requeridos para las transacciones."""
        if not self.account:
            raise GreenfieldError("Identidad Web3 obligatoria no configurada en el nodo.")
            
        timestamp = str(int(datetime.utcnow().timestamp() * 1000))
        message = f"{method}\n{path}\n{timestamp}"
        msg_encoded = encode_defunct(text=message)
        signed_message = Account.sign_message(msg_encoded, private_key=self.private_key)
        
        return {
            "X-Gnfd-App-Id": "Synergix-GhostNode",
            "X-Gnfd-Timestamp": timestamp,
            "Authorization": f"GNFD1-ECDSA {self.account.address}:{signed_message.signature.hex()}"
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=2, max=15),
        retry=retry_if_exception_type((httpx.RequestError, GreenfieldError))
    )
    async def _execute_request(self, method: str, object_path: str, content: bytes = None, tags: Dict[str, Any] = None) -> httpx.Response:
        """
        Ejecuta peticiones asíncronas HTTP a Greenfield con tolerancia a fallos (Tenacity 3 reintentos).
        """
        url = f"{self.sp_url}/{self.bucket_name}/{object_path}"
        headers = self._get_signature_headers(method, f"/{self.bucket_name}/{object_path}")

        # Inyección de Tags Web3 (Metadatos on-chain)
        if tags:
            headers["X-Gnfd-Object-Tags"] = json.dumps(tags)
            
        if content is not None:
            headers["Content-Length"] = str(len(content))
            if method == "PUT" and "Content-Type" not in headers:
                headers["Content-Type"] = "application/octet-stream"

        async with httpx.AsyncClient(timeout=45.0) as client:
            try:
                if method == "GET":
                    response = await client.get(url, headers=headers)
                elif method == "PUT":
                    response = await client.put(url, content=content, headers=headers)
                elif method == "POST":
                    # POST se usa para UpdateObjectMetadata (Tags)
                    response = await client.post(url, json={"tags": tags} if tags else {}, headers=headers)
                elif method == "DELETE":
                    response = await client.delete(url, headers=headers)
                else:
                    raise ValueError(f"HTTP Method {method} no soportado en Nodo Fantasma.")

                if response.status_code >= 400:
                    raise GreenfieldError(f"HTTP {response.status_code}: {response.text}")
                    
                return response
            except httpx.HTTPError as e:
                logger.error(f"Error de red Web3 conectando al Storage Provider: {str(e)}")
                raise GreenfieldError(f"Falla de conexión Web3 asíncrona: {str(e)}")

    # -------------------------------------------------------------
    # 🎭 Identidades Fantasma (users/)
    # -------------------------------------------------------------
    
    async def get_user_tags(self, uid_ofuscado: str) -> Optional[Dict[str, Any]]:
        """Lee los tags Web3 de un usuario de manera asíncrona. Única fuente de verdad."""
        path = f"aisynergix/users/{uid_ofuscado}"
        try:
            response = await self._execute_request("GET", path)
            tags_str = response.headers.get("X-Gnfd-Object-Tags", "{}")
            return json.loads(tags_str)
        except GreenfieldError as e:
            if "404" in str(e):
                return None
            raise

    async def create_user_profile(self, uid_ofuscado: str, tags: Dict[str, Any]) -> bool:
        """Inmortaliza un nuevo usuario con archivo de 0 bytes y los Tags obligatorios."""
        path = f"aisynergix/users/{uid_ofuscado}"
        # Se requiere contenido vacío (b"") para inicializar el perfil fantasma
        await self._execute_request("PUT", path, content=b"", tags=tags)
        return True

    async def update_user_tags(self, uid_ofuscado: str, tags: Dict[str, Any]) -> bool:
        """UpdateObjectMetadata: Actualiza escudos Anti-Spam y Rangos."""
        path = f"aisynergix/users/{uid_ofuscado}"
        await self._execute_request("POST", path, tags=tags)
        return True

    # -------------------------------------------------------------
    # 🌟 Inmortalización de Conocimiento (aportes/)
    # -------------------------------------------------------------
    
    async def upload_aporte(self, uid_ofuscado: str, timestamp: int, content: str, tags: Dict[str, Any]) -> str:
        """Guarda el aporte en la subcarpeta YYYY-MM con validación Juez IA inyectada en Tags."""
        date_folder = datetime.utcnow().strftime("%Y-%m")
        object_name = f"{uid_ofuscado}_{timestamp}.txt"
        path = f"aisynergix/aportes/{date_folder}/{object_name}"
        
        await self._execute_request("PUT", path, content=content.encode("utf-8"), tags=tags)
        return path
        
    async def read_aporte(self, path: str) -> str:
        """Recupera el texto crudo de un fragmento de conocimiento específico."""
        response = await self._execute_request("GET", path)
        return response.text

    async def list_recent_aportes(self, date_folder: str) -> List[Dict[str, Any]]:
        """Lista aportes usando la API S3/REST. date_folder formato 'YYYY-MM'."""
        path = f"?prefix=aisynergix/aportes/{date_folder}/"
        try:
            response = await self._execute_request("GET", path)
            data = response.json()
            return data.get("objects", []) # Estructura sujeta a formato ListObjects de Greenfield
        except GreenfieldError as e:
            logger.error(f"Error listando la memoria inmortal mensual = {str(e)}")
            return []

    # -------------------------------------------------------------
    # 🧠 Orquestación y Memoria Maestra (data/)
    # -------------------------------------------------------------
    
    async def get_system_json(self, file_name: str) -> Optional[Dict[str, Any]]:
        """Recupera configuraciones nativas y leaderboard (top10.json, system_config.json)."""
        path = f"aisynergix/data/{file_name}"
        try:
            response = await self._execute_request("GET", path)
            return response.json()
        except GreenfieldError as e:
            if "404" in str(e):
                return None
            raise
            
    async def upload_system_json(self, file_name: str, data: Dict[str, Any]) -> bool:
        """Sobrescribe archivos críticos del sistema en Greenfield."""
        path = f"aisynergix/data/{file_name}"
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        await self._execute_request("PUT", path, content=content)
        return True

    async def get_brain_pointer(self) -> str:
        """Obtiene el puntero exacto del cerebro global FAISS (búsqueda RAG)."""
        path = "aisynergix/data/brain_pointer"
        try:
            response = await self._execute_request("GET", path)
            tags_str = response.headers.get("X-Gnfd-Object-Tags", "{}")
            return json.loads(tags_str).get("latest_v", "")
        except GreenfieldError:
            return ""

    async def update_brain_pointer(self, version: str) -> bool:
        """Actualiza el tag de versión del cerebro global (Archivo 0 bytes)."""
        path = "aisynergix/data/brain_pointer"
        await self._execute_request("PUT", path, content=b"", tags={"latest_v": version})
        return True
        
    # -------------------------------------------------------------
    # 🛡️ Auditoría Transparente (logs/)
    # -------------------------------------------------------------
    
    async def upload_daily_log(self, yyyy_mm_dd: str, log_content: bytes) -> bool:
        """Sube el archivo de logs comprimido del nodo para transparencia."""
        path = f"aisynergix/logs/{yyyy_mm_dd}.log"
        await self._execute_request("PUT", path, content=log_content)
        return True

# Instancia Singleton expuesta para todo el nodo
greenfield = GreenfieldService()
