"""
Módulo 1: Web3 (greenfield.py)
---------------------------------------------------------
Servicio Cliente de BNB Greenfield con Parche UTF-8 Puro
"""
importar sistema operativo
hora de importación
importar hashlib
importar registro
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote

importar httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponencial

registrador = registro.getLogger("synergix.greenfield")

GREENFIELD_RPC_URL = os.environ.get("GREENFIELD_RPC_URL", "https://greenfield-chain.bnbchain.org")
NOMBRE_DEL_BUCKET = os.environ.get("NOMBRE_DEL_BUCKET", "synergixai")
SALT_UID = "Synergix_"

def _hash_uid(raw_uid: int | str) -> str:
    return hashlib.sha256(f"{SALT_UID}{raw_uid}".encode()).hexdigest()[:12]

async def hash_uid(raw_uid: int | str) -> str:
    devolver _hash_uid(raw_uid)

def safe_encode_header(val: Any) -> str:
    "UTF-8 es un código ASCII codificado en URL compatible con HTTPX."
    devolver quote(str(val))

def safe_decode_header(val: Any) -> str:
    devolver unquote(str(val))

Clase GreenfieldClient:
    def __init__(self):
        priv_key = os.environ.get("GREENFIELD_PRIVATE_KEY")
        if not priv_key: raise ValueError("CRÉDITO: Falta GREENFIELD_PRIVATE_KEY")
        self.account = Account.from_key(priv_key)
        self.rpc_url = GREENFIELD_RPC_URL.rstrip("/")
        self.bucket = NOMBRE_DEL_BUCKET
        self._client: Optional[httpx.AsyncClient] = None
        self._auth_token: Optional[str] = None
        self._auth_expiry: int = 0

    async def _ensure_client(self) -> httpx.AsyncClient:
        Si self._client es None:
            self._client = httpx.AsyncClient(base_url=self.rpc_url, timeout=httpx.Timeout(30.0))
        devolver self._client

    async def _acquire_auth_token(self) -> str:
        ahora = int(tiempo.tiempo())
        Si self._auth_token y ahora < self._auth_expiry - 60: devolver self._auth_token
        msg = f"Synergix aut {ahora}"
        firmado = self.cuenta.sign_message(encode_defunct(text=msg))
        self._auth_token = f"{self.account.address}:{signed.signature.hex()}"
        self._auth_expiry = ahora + 3600
        devolver self._auth_token

    async def cerrar(self):
        si self._client:
            esperar a que el cliente se cierre (self._client.aclose())
            self._client = Ninguno

    async def _signed_request(self, method: str, path: str, params: Optional[Dict] = None, data: Optional[bytes] = None, headers: Optional[Dict] = None) -> httpx.Response:
        cliente = esperar a que se asegure el cliente (self._ensure_client())
        token = esperar a que se adquiera el token de autenticación (self._acquire_auth_token())
        req_h = {"Autorización": f"Greenfield {token}"}
        Si hay encabezados: req_h.update(encabezados)
        
        full_url = f"/{self.bucket}/{path.lstrip('/')}"
        
        retryer = AsyncRetrying(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException, httpx.HTTPStatusError)), reraise=True)
        asíncrono para intento en reintentador:
            con intento:
                resp = await client.request(method=method, url=full_url, params=params, headers=req_h, content=data)
                resp.raise_for_status()
                respuesta de retorno

    async def get_object(self, path: str, include_meta: bool = True) -> Tuple[bytes, Dict[str, str]]:
        clean_path = path.replace(f"{self.bucket}/", "")
        intentar:
            resp = await self._signed_request("GET", clean_path)
            etiquetas = {}
            para k, v en resp.headers.items():
                Si k.lower().startswith("x-gn-meta-"):
                    tags[k[10:]] = safe_decode_header(v)
                elif k.lower() == "x-amz-meta-tags":
                    para pk en str(v).split(","):
                        si "=" en pk:
                            pk_k, pk_v = pk.split("=", 1)
                            tags[pk_k.strip()] = safe_decode_header(pk_v.strip())
            devolver resp.content, etiquetas
        excepto httpx.HTTPStatusError como e:
            if e.response.status_code == 404: return b"", {}
            elevar e

    async def put_object(self, path: str, data: bytes, tags: Optional[Dict[str, str]] = None, content_type: str = "application/octet-stream") -> bool:
        clean_path = path.replace(f"{self.bucket}/", "")
        h = {"Content-Type": content_type}
        si etiquetas:
            para k, v en tags.items():
                h[f"X-Gn-Meta-{k}"] = safe_encode_header(v)
            h["x-amz-meta-tags"] = ",".join([f"{k}={safe_encode_header(v)}" for k, v in tags.items()])
        await self._signed_request("PUT", clean_path, headers=h, data=data)
        devolver verdadero

    async def get_user_metadata(self, uid: str) -> Dict[str, str]:
        _, tags = await self.get_object(f"aisynergix/users/{uid}", True)
        etiquetas de retorno

    async def update_user_metadata(self, uid: str, tags: Dict[str, str]) -> bool:
        return await self.put_object(f"aisynergix/users/{uid}", b"", tags=tags, content_type="application/json")

    async def list_objects(self, prefix: str) -> List[Tuple[str, Dict[str, str]]]:
        intentar:
            resp = await self._signed_request("GET", "", params={"prefix": prefix.replace(f"{self.bucket}/", "")})
            res = []
            Si resp.status_code == 200:
                ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
                intentar:
                    para el contenido en ET.fromstring(resp.text).findall('s3:Contents', ns) o ET.fromstring(resp.text).findall('Contents'):
                        clave = contenido.find('s3:Clave', ns) o contenido.find('Clave')
                        Si key no es None y key.text: res.append((key.text, {}))
                excepto Excepción: pasar
            retorno res
        excepto Excepción: devolver []

    async def list_users(self) -> List[Tuple[str, Dict[str, str]]]:
        return await self.list_objects("aisynergix/users/")

    async def add_residual_points(self, uid: str) -> None:
        t = esperar a que self.get_user_metadata(uid)
        si no t: devolver
        intentar:
            t["puntos"] = str(int(t.get("puntos", "0")) + 1)
            t["total_uses_count"] = str(int(t.get("total_uses_count", "0")) + 1)
            t["last_seen_ts"] = str(int(time.time()))
            esperar a que self.update_user_metadata(uid, t)
        excepto Excepción: pasar

    async def upload_aporte(self, uid: str, content: str, score: int, cat: str, imp: float, lang: str) -> str:
        p = f"aisynergix/aportes/{datetime.now(timezone.utc).strftime('%Y-%m')}/{uid}_{int(time.time())}.txt"
        t = {"quality_score": str(score), "category": str(cat), "impact_index": str(imp), "author_uid": uid, "lang": lang}
        await self.put_object(p, content.encode("utf-8"), tags=t, content_type="text/plain")
        devolver p

_client_instance = Ninguno
async def _get_client():
    global _client_instance
    Si _client_instance es None: _client_instance = GreenfieldClient()
    devolver _instancia_cliente

async def close_greenfield_client():
    global _client_instance
    si _client_instance:
        esperar _client_instance.aclose()
        _client_instance = Ninguno

async def get_object(path: str, include_meta: bool = True): client = await _get_client(); return await client.get_object(path, include_meta)
async def put_object(path: str, data: bytes, tags: Optional[Dict[str, str]] = None, content_type: str = "application/octet-stream"): client = await _get_client(); return await client.put_object(path, data, tags, content_type)
async def get_user_metadata(uid: str): client = await _get_client(); return await client.get_user_metadata(uid)
async def update_user_metadata(uid: str, tags: Dict[str, str]): client = await _get_client(); return await client.update_user_metadata(uid, tags)
async def list_users(): cliente = await _get_client(); return await cliente.list_users()
async def list_objects(prefix: str): client = await _get_client(); return await client.list_objects(prefix)
async def add_residual_points(uid: str): client = await _get_client(); await client.add_residual_points(uid)
async def upload_aporte(uid: str, content: str, score: int, cat: str, imp: float, lang: str): client = await _get_client(); return await client.upload_aporte(uid, content, score, cat, imp, lang)
