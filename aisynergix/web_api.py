"""
web_api.py — HTTP bridge that exposes the Synergix engine to the website.

Runs in the SAME process as the Telegram bot (opt-in via the
``SYNERGIX_WEB_API_ENABLED`` env var), so it shares the already-loaded
RAG index + Thinker/Judge connectors — no extra model load, no second
brain download.

Design constraints:
  • Zero new dependencies — built on Starlette + uvicorn + sse-starlette,
    all of which are already in requirements.txt.
  • The Irys PRIVATE_KEY never leaves the bot.  Uploads still go through
    the irys-uploader microservice exactly like the Telegram path.
  • Stateless: web visitors have no Telegram identity, so chat does NOT
    touch the profile/points system.  Language comes from the request,
    not from a stored profile (avoids the "everything answers in Spanish"
    trap of reusing AIManager.stream_conversation).

Endpoints (all under the API-key + rate-limit + CORS middleware):
  GET  /health        → liveness + thinker/judge health
  POST /v1/chat       → SSE stream of {"kind":"answer"|"memory_count"|"error","text"}
  POST /v1/judge      → judge a candidate aporte (preview, no write)
  POST /v1/contribute → verify wallet signature → judge → seal on Irys

Auth: every request must carry  X-API-Key: <SYNERGIX_WEB_API_KEY>.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger("synergix.web_api")

# ── Configuration (env) ────────────────────────────────────────────────

def _getenv(key: str, default: str = "") -> str:
    return os.getenv(f"SYNERGIX_{key}", os.getenv(key, default))


WEB_API_KEY: str = _getenv("WEB_API_KEY", "")
WEB_API_HOST: str = _getenv("WEB_API_HOST", "0.0.0.0")
WEB_API_PORT: int = int(_getenv("WEB_API_PORT", "8090"))
WEB_CORS_ORIGINS: List[str] = [
    o.strip()
    for o in _getenv(
        "WEB_CORS_ORIGINS",
        "https://www.synergix.lol,https://synergix.lol",
    ).split(",")
    if o.strip()
]
# Per-IP sliding-window rate limit.
WEB_RATE_MAX: int = int(_getenv("WEB_RATE_MAX", "20"))         # requests
WEB_RATE_WINDOW: int = int(_getenv("WEB_RATE_WINDOW", "60"))   # seconds
# Whether web chat fires residual rewards to authors of retrieved aportes.
# Off by default to avoid Irys write amplification from anonymous traffic.
WEB_RESIDUAL_REWARDS: bool = _getenv("WEB_RESIDUAL_REWARDS", "false").lower() == "true"

_MAX_MESSAGE_CHARS = 4000
_MAX_CONTRIB_CHARS = 5000
_MIN_CONTRIB_CHARS = 20

# Native language names for the "respond in X" instruction (mirror of
# local_ia.LANG_NAMES, kept local so we don't import private constants).
_LANG_NAMES: Dict[str, str] = {
    "es": "español", "en": "English", "zh": "中文", "hi": "हिन्दी",
    "ar": "العربية", "fr": "Français", "bn": "বাংলা", "pt": "Português",
    "id": "Bahasa Indonesia", "ur": "اردو", "de": "Deutsch", "ja": "日本語",
    "ko": "한국어",
}


# ── In-memory per-IP rate limiter ──────────────────────────────────────

_rate_buckets: Dict[str, Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # Behind Cloudflare tunnel / proxy, prefer forwarded headers.
    fwd = request.headers.get("cf-connecting-ip") or request.headers.get(
        "x-forwarded-for"
    )
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_ok(ip: str) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    cutoff = now - WEB_RATE_WINDOW
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= WEB_RATE_MAX:
        return False
    bucket.append(now)
    return True


# ── Auth + rate-limit middleware ───────────────────────────────────────

class GuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Always allow CORS preflight (handled by CORSMiddleware downstream).
        if request.method == "OPTIONS":
            return await call_next(request)

        # /health is public-ish but still rate-limited.
        path = request.url.path
        ip = _client_ip(request)

        if not _rate_ok(ip):
            return JSONResponse({"error": "rate_limited"}, status_code=429)

        if path != "/health":
            provided = request.headers.get("x-api-key", "")
            if not WEB_API_KEY or provided != WEB_API_KEY:
                return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001
            logger.exception("web_api unhandled error on %s: %s", path, exc)
            return JSONResponse({"error": "internal"}, status_code=500)


# ── Helpers ────────────────────────────────────────────────────────────

def _sse(obj: Dict[str, object]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _web_uid_hash(address: str) -> str:
    """Stable, privacy-preserving author id for a web (wallet) contributor.

    Distinct namespace from Telegram's ``Synergix_<uid>`` hashing so web
    and Telegram identities never collide.
    """
    return "w" + hashlib.sha256(
        f"Synergix_web_{address.lower()}".encode()
    ).hexdigest()[:11]


# ── Route handlers ─────────────────────────────────────────────────────

async def health(request: Request) -> JSONResponse:
    from aisynergix.ai.manager import get_ai_manager

    try:
        h = await get_ai_manager().health_check()
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check failed: %s", exc)
        h = {"thinker": False, "judge": False, "all_healthy": False}
    return JSONResponse({"status": "ok", "engine": h, "ts": int(time.time())})


async def chat(request: Request) -> Response:
    from aisynergix.ai.local_ia import get_thinker
    from aisynergix.ai.manager import (
        _extract_sticker,
        _strip_filler,
        _strip_name_prefix,
    )
    from aisynergix.services.rag_engine import get_rag_engine

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    message = str(body.get("message", "")).strip()[:_MAX_MESSAGE_CHARS]
    if not message:
        return JSONResponse({"error": "empty_message"}, status_code=400)

    lang = str(body.get("lang", "en")).strip()[:5] or "en"
    raw_history = body.get("history", [])
    history: List[Dict[str, str]] = []
    if isinstance(raw_history, list):
        for m in raw_history[-10:]:
            if (
                isinstance(m, dict)
                and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)
            ):
                history.append({"role": m["role"], "content": m["content"][:2000]})

    async def event_stream():
        try:
            rag = await get_rag_engine()
            context, results = await rag.query(message, lang)
        except Exception as exc:  # noqa: BLE001
            logger.warning("web chat RAG failed: %s", exc)
            context, results = "", []

        if results:
            yield _sse({"kind": "memory_count", "text": str(len(results))})

        thinker = get_thinker()
        answer_buf = ""
        # Buffer first answer tokens to strip a leading "Name:" prefix before
        # the user sees it (mirror of AIManager.stream_conversation).
        pfx_buf = ""
        pfx_done = False
        PFX_WINDOW = 15

        try:
            async for kind, text in thinker.stream_think(
                user_message=message,
                context=context,
                history=history,
                target_language=lang,
            ):
                if kind != "answer":
                    continue
                answer_buf += text
                if not pfx_done:
                    pfx_buf += text
                    if len(pfx_buf) >= PFX_WINDOW:
                        pfx_done = True
                        yield _sse({"kind": "answer", "text": _strip_name_prefix(pfx_buf)})
                else:
                    yield _sse({"kind": "answer", "text": text})

            if not pfx_done and pfx_buf:
                yield _sse({"kind": "answer", "text": _strip_name_prefix(pfx_buf)})
        except Exception as exc:  # noqa: BLE001
            logger.warning("web chat stream failed: %s", exc)
            yield _sse({"kind": "error", "text": "generation_failed"})

        # Optional: reward authors of retrieved aportes (off by default).
        if WEB_RESIDUAL_REWARDS and context and results:
            try:
                from aisynergix.ai.manager import get_ai_manager
                asyncio.create_task(
                    get_ai_manager()._process_residual_rewards(results)
                )
            except Exception:
                pass

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering
        },
    )


async def judge(request: Request) -> JSONResponse:
    from aisynergix.ai.local_ia import get_judge

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    text = str(body.get("text", "")).strip()[:_MAX_CONTRIB_CHARS]
    if len(text) < _MIN_CONTRIB_CHARS:
        return JSONResponse(
            {"error": "too_short", "minLength": _MIN_CONTRIB_CHARS},
            status_code=400,
        )

    try:
        evaluation = await get_judge().evaluate(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("web judge failed: %s", exc)
        return JSONResponse({"error": "judge_failed"}, status_code=502)

    return JSONResponse(evaluation)


def _verify_signature(address: str, challenge: str, signature: str) -> bool:
    """EIP-191 personal_sign recovery via eth_account (already a dep)."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        recovered = Account.recover_message(
            encode_defunct(text=challenge), signature=signature
        )
        return recovered.lower() == address.lower()
    except Exception as exc:  # noqa: BLE001
        logger.warning("signature verify error: %s", exc)
        return False


async def contribute(request: Request) -> JSONResponse:
    from aisynergix.ai.local_ia import get_judge
    from aisynergix.services.irys import write_aporte, _gw
    from aisynergix.services.rag_engine import get_rag_engine

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    text = str(body.get("text", "")).strip()[:_MAX_CONTRIB_CHARS]
    address = str(body.get("address", "")).strip()
    challenge = str(body.get("challenge", ""))
    signature = str(body.get("signature", ""))
    lang = str(body.get("lang", "en")).strip()[:5] or "en"

    if len(text) < _MIN_CONTRIB_CHARS:
        return JSONResponse(
            {"error": "too_short", "minLength": _MIN_CONTRIB_CHARS}, status_code=400
        )
    if not (address.startswith("0x") and len(address) == 42):
        return JSONResponse({"error": "bad_address"}, status_code=400)
    if not challenge or not signature:
        return JSONResponse({"error": "missing_signature"}, status_code=400)

    # 1. Prove wallet ownership.
    if not _verify_signature(address, challenge, signature):
        return JSONResponse({"error": "signature_mismatch"}, status_code=401)

    # 2. Quality gate.
    try:
        evaluation = await get_judge().evaluate(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("contribute judge failed: %s", exc)
        return JSONResponse({"error": "judge_failed"}, status_code=502)

    if not evaluation.get("approved"):
        return JSONResponse({"status": "rejected", "evaluation": evaluation})

    # 3. Seal on Irys (signed by the bot's irys-uploader, tagged with the
    #    contributor's wallet as author signature).
    uid_hash = _web_uid_hash(address)
    ts = int(datetime.now(timezone.utc).timestamp())
    content_summary = evaluation.get("content_summary", "") or ""
    tags = {
        "quality_score": str(evaluation.get("quality_score", 0)),
        "author_uid": uid_hash,
        "lang": lang,
        "category": evaluation.get("category", "filosofia"),
        "impact": str(evaluation.get("impact_index", 0.5)),
        "content_summary": content_summary,
        "signature": address.lower(),
        "source": "web",
    }

    try:
        tx_id = await write_aporte(
            uid_ofuscado=uid_hash, texto=text, tags=tags, ts=ts
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("web contribute Irys write failed: %s", exc, exc_info=True)
        return JSONResponse({"error": "irys_write_failed"}, status_code=502)

    # 4. Make it immediately searchable in the shared RAG index.
    try:
        rag = await get_rag_engine()
        await rag.add_contribution(
            text=content_summary or text,
            author_uid=uid_hash,
            language=lang,
            quality_score=float(evaluation.get("quality_score", 0)),
            object_name=tx_id,
            category=evaluation.get("category", "filosofia"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("web contribute RAG index failed (non-fatal): %s", exc)

    return JSONResponse(
        {
            "status": "success",
            "txId": tx_id,
            "gatewayUrl": _gw(tx_id),
            "evaluation": evaluation,
        }
    )


# ── App factory ────────────────────────────────────────────────────────

def create_app() -> Starlette:
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/v1/chat", chat, methods=["POST", "OPTIONS"]),
        Route("/v1/judge", judge, methods=["POST", "OPTIONS"]),
        Route("/v1/contribute", contribute, methods=["POST", "OPTIONS"]),
    ]
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=WEB_CORS_ORIGINS or ["*"],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "X-API-Key"],
            max_age=600,
        ),
        Middleware(GuardMiddleware),
    ]
    return Starlette(routes=routes, middleware=middleware)


async def serve() -> None:
    """Run the web API with uvicorn inside the bot's event loop.

    Launched as an asyncio task from bot.main() when
    ``SYNERGIX_WEB_API_ENABLED`` is truthy.  Shares the process (and thus
    the warm RAG index + Thinker/Judge connectors) with the Telegram bot.
    """
    import uvicorn

    if not WEB_API_KEY:
        logger.error(
            "🌐 Web API enabled but SYNERGIX_WEB_API_KEY is empty — refusing "
            "to start an unauthenticated public endpoint."
        )
        return

    app = create_app()
    config = uvicorn.Config(
        app,
        host=WEB_API_HOST,
        port=WEB_API_PORT,
        log_level="info",
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    logger.info(
        "🌐 Synergix Web API en %s:%d (CORS=%s, rate=%d/%ds)",
        WEB_API_HOST, WEB_API_PORT, WEB_CORS_ORIGINS, WEB_RATE_MAX, WEB_RATE_WINDOW,
    )
    await server.serve()


def is_enabled() -> bool:
    return _getenv("WEB_API_ENABLED", "false").lower() == "true"


__all__ = ["create_app", "serve", "is_enabled"]
