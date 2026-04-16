"""
identity.py — Sistema de identidad de Synergix con ofuscación PbD y caché LRU.
Ofusca UIDs de Telegram con XOR de 64 bits y Base36, manteniendo caché en RAM con TTL 10 min.
"""

import logging
import time
import base64
from typing import Optional, Dict, Any, Tuple
from cachetools import TTLCache

from aisynergix.config.constants import (
    SECRET_MASK,
    CACHE_TTL_SECONDS,
    USERS_PREFIX,
    get_rank_for_points
)
from aisynergix.services.greenfield import (
    get_user_metadata,
    put_object,
    update_user_metadata
)

logger = logging.getLogger(__name__)


class IdentityCache:
    """
    Caché LRU con TTL para identidades de usuario.
    Evita latencias RPC redundantes a Greenfield.
    """
    
    def __init__(self, maxsize: int = 1000, ttl: int = CACHE_TTL_SECONDS):
        """
        Inicializa el caché de identidades.
        
        Args:
            maxsize: Máximo número de entradas en caché
            ttl: Tiempo de vida en segundos (por defecto 10 minutos)
        """
        self.cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self.hits = 0
        self.misses = 0
    
    def get(self, key: str) -> Optional[Dict[str, str]]:
        """
        Obtiene una identidad del caché.
        
        Args:
            key: Clave de caché (UID ofuscado)
        
        Returns:
            Optional[Dict]: Tags del usuario o None si no está en caché
        """
        if key in self.cache:
            self.hits += 1
            logger.debug(f"Cache HIT para {key[:8]}...")
            return self.cache[key]
        
        self.misses += 1
        logger.debug(f"Cache MISS para {key[:8]}...")
        return None
    
    def set(self, key: str, value: Dict[str, str]):
        """
        Almacena una identidad en el caché.
        
        Args:
            key: Clave de caché (UID ofuscado)
            value: Tags del usuario
        """
        self.cache[key] = value
        logger.debug(f"Cache SET para {key[:8]}... (size: {len(self.cache)})")
    
    def invalidate(self, key: str):
        """
        Elimina una entrada del caché.
        
        Args:
            key: Clave de caché a invalidar
        """
        if key in self.cache:
            del self.cache[key]
            logger.debug(f"Cache INVALIDATE para {key[:8]}...")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Obtiene estadísticas del caché.
        
        Returns:
            Dict: Estadísticas de uso del caché
        """
        return {
            "size": len(self.cache),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / (self.hits + self.misses) if (self.hits + self.misses) > 0 else 0,
            "maxsize": self.cache.maxsize,
            "ttl": self.cache.ttl
        }


# Caché global de identidades
identity_cache = IdentityCache(maxsize=1000, ttl=CACHE_TTL_SECONDS)


def mask_uid(telegram_uid: str) -> str:
    """
    Ofusca un UID de Telegram usando XOR de 64 bits y Base36.
    
    Proceso:
    1. Convertir UID string a int
    2. Aplicar XOR con máscara secreta de 64 bits
    3. Convertir resultado a Base36 (0-9, a-z)
    4. Asegurar longitud mínima con padding
    
    Args:
        telegram_uid: UID numérico de Telegram como string
    
    Returns:
        str: UID ofuscado en Base36 (ej: "6g7t8k9ti3p0")
    
    Raises:
        ValueError: Si el UID no es numérico válido
    """
    try:
        # Convertir UID a entero
        uid_int = int(telegram_uid)
        
        # Aplicar XOR con máscara de 64 bits
        masked = uid_int ^ SECRET_MASK
        
        # Convertir a Base36 (sin signo negativo)
        if masked < 0:
            masked = abs(masked)  # Tomar valor absoluto
        
        # Convertir a Base36 y asegurar longitud mínima
        base36 = base64.b36encode(masked.to_bytes(8, 'big', signed=False)).decode('ascii')
        
        # Eliminar ceros iniciales y asegurar al menos 8 caracteres
        base36_clean = base36.lstrip('0')
        if len(base36_clean) < 8:
            base36_clean = base36_clean.rjust(8, '0')
        
        # Limitar a 12 caracteres máximo para legibilidad
        final_uid = base36_clean[:12]
        
        logger.debug(f"UID ofuscado: {telegram_uid} -> {final_uid}")
        return final_uid
        
    except ValueError as e:
        logger.error(f"Error ofuscando UID {telegram_uid}: {e}")
        raise ValueError(f"UID de Telegram no válido: {telegram_uid}")
    except Exception as e:
        logger.error(f"Error inesperado ofuscando UID {telegram_uid}: {e}", exc_info=True)
        # Fallback: hash simple
        import hashlib
        hash_obj = hashlib.sha256(telegram_uid.encode()).hexdigest()[:12]
        return hash_obj


def unmask_uid(masked_uid: str) -> Optional[str]:
    """
    Desofusca un UID de Base36 a Telegram UID original.
    
    Args:
        masked_uid: UID ofuscado en Base36
    
    Returns:
        Optional[str]: UID original de Telegram o None si error
    
    Nota: Esta función solo se usa internamente para logging y debugging.
          Nunca se expone al usuario o fuera del sistema.
    """
    try:
        # Asegurar que el string Base36 sea válido
        if not all(c.isalnum() for c in masked_uid):
            logger.warning(f"UID ofuscado inválido (no alfanumérico): {masked_uid}")
            return None
        
        # Convertir de Base36 a entero
        uid_bytes = base64.b36decode(masked_uid.upper().encode('ascii'))
        masked_int = int.from_bytes(uid_bytes, 'big', signed=False)
        
        # Aplicar XOR inverso (mismo que mask)
        original_int = masked_int ^ SECRET_MASK
        
        # Convertir a string
        original_uid = str(original_int)
        
        logger.debug(f"UID desofuscado: {masked_uid} -> {original_uid}")
        return original_uid
        
    except Exception as e:
        logger.error(f"Error desofuscando UID {masked_uid}: {e}")
        return None


class UserContext:
    """
    Contexto de usuario en RAM (Stateless).
    Representa el estado actual del usuario extraído de los tags de Greenfield.
    """
    
    def __init__(self, uid_ofuscado: str, tags: Dict[str, str], telegram_uid: str):
        """
        Inicializa el contexto de usuario.
        
        Args:
            uid_ofuscado: UID ofuscado (Base36)
            tags: Tags del usuario desde Greenfield
            telegram_uid: UID original de Telegram (solo para logging interno)
        """
        self.uid_ofuscado = uid_ofuscado
        self.telegram_uid = telegram_uid
        
        # Extraer y parsear tags
        self.points = int(tags.get("points", "0"))
        self.rank = tags.get("rank", "Iniciado")
        self.welcomed = tags.get("welcomed", "false").lower() == "true"
        self.language = tags.get("language", "auto")
        self.first_name = tags.get("first_name", "Usuario")
        self.fsm_state = tags.get("fsm_state", "IDLE")
        self.daily_quota = int(tags.get("daily_quota", "10"))
        self.last_seen_ts = int(tags.get("last_seen_ts", "0"))
        
        # Calcular rango actual basado en puntos
        calculated_rank = get_rank_for_points(self.points)
        if self.rank != calculated_rank:
            logger.info(f"Rango desincronizado para {uid_ofuscado}: "
                       f"{self.rank} vs {calculated_rank} (basado en {self.points} pts)")
            self.rank = calculated_rank
        
        # Timestamp de creación del contexto
        self.context_created_at = time.time()
    
    def to_tags(self) -> Dict[str, str]:
        """
        Convierte el contexto de vuelta a tags para Greenfield.
        
        Returns:
            Dict[str, str]: Tags actualizados
        """
        return {
            "points": str(self.points),
            "rank": self.rank,
            "welcomed": str(self.welcomed).lower(),
            "language": self.language,
            "first_name": self.first_name,
            "fsm_state": self.fsm_state,
            "daily_quota": str(self.daily_quota),
            "last_seen_ts": str(int(time.time()))
        }
    
    def increment_points(self, amount: int = 1) -> int:
        """
        Incrementa los puntos del usuario y actualiza rango si es necesario.
        
        Args:
            amount: Cantidad de puntos a añadir
        
        Returns:
            int: Nuevo total de puntos
        """
        old_points = self.points
        self.points += amount
        
        # Verificar si cambió el rango
        new_rank = get_rank_for_points(self.points)
        if new_rank != self.rank:
            logger.info(f"Usuario {self.uid_ofuscado} ascendió de {self.rank} a {new_rank} "
                       f"({old_points} -> {self.points} pts)")
            self.rank = new_rank
        
        return self.points
    
    def __repr__(self) -> str:
        return (f"UserContext(uid={self.uid_ofuscado[:8]}..., "
                f"points={self.points}, rank={self.rank}, "
                f"lang={self.language}, welcomed={self.welcomed})")


async def hydrate_user(telegram_uid: str, first_name: str = "Usuario") -> UserContext:
    """
    Hidrata el contexto del usuario desde Greenfield con caché LRU.
    
    Proceso:
    1. Ofuscar UID de Telegram
    2. Buscar en caché LRU (TTL 10 min)
    3. Si no en caché, hacer HEAD request a Greenfield
    4. Si usuario no existe (404), crear archivo 0-bytes con tags por defecto
    5. Retornar UserContext
    
    Args:
        telegram_uid: UID numérico de Telegram como string
        first_name: Nombre del usuario para personalización
    
    Returns:
        UserContext: Contexto completo del usuario
    """
    try:
        # 1. Ofuscar UID
        uid_ofuscado = mask_uid(telegram_uid)
        
        # 2. Intentar obtener del caché
        cached_tags = identity_cache.get(uid_ofuscado)
        
        if cached_tags is not None:
            logger.debug(f"Usuario {uid_ofuscado[:8]}... encontrado en caché")
            # Actualizar first_name si es diferente
            if cached_tags.get("first_name") != first_name:
                cached_tags["first_name"] = first_name
        else:
            # 3. HEAD request a Greenfield
            logger.debug(f"Usuario {uid_ofuscado[:8]}... no en caché, consultando Greenfield...")
            cached_tags = await get_user_metadata(uid_ofuscado)
            
            if cached_tags is None:
                # 4. Usuario nuevo - crear con tags por defecto
                logger.info(f"Nuevo usuario detectado: {telegram_uid} -> {uid_ofuscado}")
                default_tags = {
                    "points": "0",
                    "rank": "Iniciado",
                    "welcomed": "false",
                    "language": "auto",
                    "first_name": first_name,
                    "fsm_state": "IDLE",
                    "daily_quota": "10",
                    "last_seen_ts": str(int(time.time()))
                }
                
                # Crear objeto 0-bytes en Greenfield
                success = await put_object(
                    f"{USERS_PREFIX}/{uid_ofuscado}",
                    b"",  # 0 bytes
                    tags=default_tags
                )
                
                if not success:
                    logger.error(f"Error creando usuario {uid_ofuscado} en Greenfield")
                    # Continuar con tags por defecto en RAM
                
                cached_tags = default_tags
            else:
                # 5. Usuario existente - actualizar first_name si cambió
                if cached_tags.get("first_name") != first_name:
                    cached_tags["first_name"] = first_name
                    # Actualización lazy a Greenfield
                    asyncio.create_task(
                        update_user_metadata(uid_ofuscado, cached_tags)
                    )
            
            # Almacenar en caché
            identity_cache.set(uid_ofuscado, cached_tags)
        
        # 6. Crear y retornar contexto
        context = UserContext(uid_ofuscado, cached_tags, telegram_uid)
        logger.debug(f"Contexto hidratado: {context}")
        
        return context
        
    except Exception as e:
        logger.error(f"Error hidratando usuario {telegram_uid}: {e}", exc_info=True)
        # Fallback: contexto mínimo
        fallback_tags = {
            "points": "0",
            "rank": "Iniciado",
            "welcomed": "false",
            "language": "auto",
            "first_name": first_name,
            "fsm_state": "IDLE",
            "daily_quota": "10",
            "last_seen_ts": str(int(time.time()))
        }
        fallback_uid = f"error_{telegram_uid[:8]}"
        return UserContext(fallback_uid, fallback_tags, telegram_uid)


async def dehydrate_user(ctx: UserContext) -> bool:
    """
    Sincroniza el contexto de RAM a Greenfield (actualización lazy).
    
    Args:
        ctx: UserContext a sincronizar
    
    Returns:
        bool: True si la sincronización fue exitosa
    """
    try:
        # Actualizar timestamp de última vista
        ctx.last_seen_ts = int(time.time())
        
        # Convertir a tags
        tags = ctx.to_tags()
        
        # Actualizar caché
        identity_cache.set(ctx.uid_ofuscado, tags)
        
        # Sincronización lazy a Greenfield (fire-and-forget)
        async def _sync_to_greenfield():
            try:
                success = await update_user_metadata(ctx.uid_ofuscado, tags)
                if success:
                    logger.debug(f"Usuario {ctx.uid_ofuscado[:8]}... sincronizado a Greenfield")
                else:
                    logger.warning(f"Fallo sincronizando {ctx.uid_ofuscado[:8]}... a Greenfield")
            except Exception as e:
                logger.error(f"Error en sync_to_greenfield para {ctx.uid_ofuscado[:8]}: {e}")
        
        # Ejecutar en segundo plano
        asyncio.create_task(_sync_to_greenfield())
        
        return True
        
    except Exception as e:
        logger.error(f"Error deshidratando usuario {ctx.uid_ofuscado[:8]}...: {e}")
        return False


async def invalidate_user_cache(uid_ofuscado: str):
    """
    Invalida la entrada de un usuario en el caché.
    Útil después de actualizaciones externas.
    
    Args:
        uid_ofuscado: UID ofuscado del usuario
    """
    identity_cache.invalidate(uid_ofuscado)
    logger.debug(f"Caché invalidado para {uid_ofuscado[:8]}...")


def get_cache_stats() -> Dict[str, Any]:
    """
    Obtiene estadísticas del caché de identidades.
    
    Returns:
        Dict: Estadísticas del caché
    """
    return identity_cache.get_stats()
