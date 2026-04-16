"""
local_ia.py — Conector HTTP asíncrono para las IAs locales de Synergix.
Gestiona la comunicación con el Juez (0.5B, puerto 8080) y el Pensador (1.5B, puerto 8081).
Implementa validación estricta de JSON para el Juez y manejo robusto de errores.
"""

import json
import logging
from typing import Any, Optional, Dict
import httpx

from aisynergix.config.constants import (
    IA_JUEZ_URL,
    IA_PENSADOR_URL,
    IA_TIMEOUT_SECONDS,
)
from aisynergix.config.system_prompts import (
    JUEZ_SYSTEM_PROMPT,
    PENSADOR_SYSTEM_PROMPT,
    validate_judge_response,
)

logger = logging.getLogger(__name__)

# Modelos Ollama configurados en Docker Compose
JUDGE_MODEL = "qwen2:0.5b"
THINKER_MODEL = "qwen2:1.5b"

# Timeout de conexión y lectura (separados)
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = float(IA_TIMEOUT_SECONDS)


class JudgeResult:
    """Resultado estructurado de la evaluación del Juez."""
    
    def __init__(self, raw: Dict[str, Any]) -> None:
        """
        Inicializa el resultado del Juez con validación estricta.
        
        Args:
            raw: Diccionario con campos calificacion, validez_tecnica y categoria
        """
        self.calificacion = float(raw.get("calificacion", 0))
        self.validez_tecnica = bool(raw.get("validez_tecnica", False))
        self.categoria = str(raw.get("categoria", "otro"))
        self.raw = raw
        
        # Validar rangos
        if self.calificacion < 0 or self.calificacion > 10:
            logger.warning(f"Calificación fuera de rango: {self.calificacion}. Ajustando a [0,10]")
            self.calificacion = max(0, min(10, self.calificacion))
    
    def __repr__(self) -> str:
        return f"JudgeResult(calificacion={self.calificacion}, validez={self.validez_tecnica}, cat='{self.categoria}')"
    
    def is_high_quality(self, threshold: float = 7.0) -> bool:
        """Determina si el aporte es de alta calidad según umbral."""
        return self.calificacion >= threshold and self.validez_tecnica
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte el resultado a diccionario para serialización."""
        return {
            "calificacion": self.calificacion,
            "validez_tecnica": self.validez_tecnica,
            "categoria": self.categoria
        }


async def _ollama_request(
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3
) -> str:
    """
    Realiza una petición HTTP asíncrona a un endpoint Ollama.
    
    Args:
        base_url: URL base del servicio (ej: http://synergix-ia-juez:8080)
        model: Nombre del modelo (ej: "qwen2:0.5b")
        system_prompt: Prompt del sistema (rol y reglas)
        user_prompt: Prompt del usuario (consulta específica)
        temperature: Temperatura para la generación (0.1-1.0)
    
    Returns:
        str: Respuesta del modelo (texto plano)
    
    Raises:
        httpx.HTTPStatusError: Si la petición HTTP falla
        httpx.TimeoutException: Si se excede el timeout
        ValueError: Si la respuesta no es válida
    """
    url = f"{base_url}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }
    
    # Configurar timeout separado para conexión y lectura
    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT)
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            logger.debug(f"Enviando petición a {url} con modelo {model}")
            response = await client.post(url, json=payload)
            response.raise_for_status()
            
            data = response.json()
            content = data.get("message", {}).get("content", "").strip()
            
            if not content:
                raise ValueError("Respuesta vacía del modelo")
            
            logger.debug(f"Respuesta recibida de {model}: {content[:100]}...")
            return content
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Error HTTP en petición a {model}: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.TimeoutException:
            logger.error(f"Timeout en petición a {model} después de {READ_TIMEOUT}s")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"JSON inválido en respuesta de {model}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error inesperado en petición a {model}: {e}", exc_info=True)
            raise


async def ask_judge(text: str, max_retries: int = 2) -> JudgeResult:
    """
    Consulta al Juez (0.5B) para evaluar la calidad de un aporte.
    
    Args:
        text: Texto del aporte a evaluar
        max_retries: Intentos máximos en caso de error
    
    Returns:
        JudgeResult: Resultado estructurado de la evaluación
    """
    retry_count = 0
    
    while retry_count <= max_retries:
        try:
            logger.info(f"Consultando al Juez sobre aporte: '{text[:50]}...'")
            
            # Petición al Juez
            raw_response = await _ollama_request(
                base_url=IA_JUEZ_URL,
                model=JUDGE_MODEL,
                system_prompt=JUEZ_SYSTEM_PROMPT,
                user_prompt=text,
                temperature=0.1  # Baja temperatura para consistencia
            )
            
            # Validar y parsear respuesta
            validated_data = validate_judge_response(raw_response)
            result = JudgeResult(validated_data)
            
            logger.info(f"Juez evaluó: calificación={result.calificacion}, "
                       f"válido={result.validez_tecnica}, categoría={result.categoria}")
            
            return result
            
        except ValueError as e:
            logger.warning(f"Respuesta del Juez inválida (intento {retry_count+1}/{max_retries+1}): {e}")
            retry_count += 1
            
            if retry_count > max_retries:
                logger.error(f"Fallo después de {max_retries+1} intentos con el Juez")
                return JudgeResult({"calificacion": 0, "validez_tecnica": False, "categoria": "error"})
            
            # Esperar antes de reintentar
            import asyncio
            await asyncio.sleep(1 * retry_count)  # Backoff exponencial
            
        except Exception as e:
            logger.error(f"Error consultando al Juez: {e}", exc_info=True)
            
            # En caso de error de conexión, retornar resultado por defecto
            if retry_count >= max_retries:
                return JudgeResult({"calificacion": 0, "validez_tecnica": False, "categoria": "error"})
            
            retry_count += 1
            import asyncio
            await asyncio.sleep(2 * retry_count)
    
    # Fallback final
    return JudgeResult({"calificacion": 0, "validez_tecnica": False, "categoria": "error"})


async def ask_thinker(
    prompt: str,
    context: Optional[str] = None,
    language_hint: Optional[str] = None,
    temperature: float = 0.7
) -> str:
    """
    Consulta al Pensador (1.5B) para generar respuestas expertas.
    
    Args:
        prompt: Pregunta o consulta del usuario
        context: Contexto RAG adicional (opcional)
        language_hint: Sugerencia de idioma para el Pensador (ej: "es", "en", "zh")
        temperature: Temperatura para la generación (0.1-1.0)
    
    Returns:
        str: Respuesta del Pensador formateada
    """
    try:
        logger.info(f"Consultando al Pensador: '{prompt[:50]}...'")
        
        # Construir prompt completo con contexto si está disponible
        if context and context.strip():
            full_prompt = f"Contexto del cerebro colectivo:\n{context}\n\nPregunta del usuario:\n{prompt}"
            logger.debug(f"Pensador recibió contexto RAG de {len(context)} caracteres")
        else:
            full_prompt = prompt
        
        # Añadir hint de idioma si se proporciona
        if language_hint:
            language_hints = {
                "es": "Responde en español.",
                "en": "Respond in English.",
                "zh": "用中文回答。",
                "zh_cn": "用简体中文回答。"
            }
            if language_hint in language_hints:
                full_prompt = f"{language_hints[language_hint]}\n\n{full_prompt}"
        
        # Petición al Pensador
        response = await _ollama_request(
            base_url=IA_PENSADOR_URL,
            model=THINKER_MODEL,
            system_prompt=PENSADOR_SYSTEM_PROMPT,
            user_prompt=full_prompt,
            temperature=temperature
        )
        
        # Post-procesamiento básico
        response = response.strip()
        
        # Log resumido
        logger.info(f"Pensador respondió con {len(response)} caracteres")
        if len(response) > 100:
            logger.debug(f"Primeros 100 caracteres: {response[:100]}...")
        
        return response
        
    except httpx.HTTPStatusError as e:
        logger.error(f"Error HTTP en Pensador: {e.response.status_code}")
        return "⚠️ El Pensador está temporalmente indisponible. Intenta nuevamente en unos momentos."
    
    except httpx.TimeoutException:
        logger.error("Timeout en consulta al Pensador")
        return "⏱️ El Pensador está procesando una consulta compleja. Por favor, sé paciente o reformula tu pregunta."
    
    except Exception as e:
        logger.error(f"Error inesperado en Pensador: {e}", exc_info=True)
        return "🧠 El cerebro colectivo está experimentando dificultades técnicas. Tu consulta ha sido registrada."
