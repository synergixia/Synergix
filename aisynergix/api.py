"""api.py — API pública de solo lectura para organizaciones (Fase 3, §7.4).

Expone la capa pública de Synergix sobre HTTP: nodos y sus mapas de
conocimiento, Passports por Ghost ID, impacto por aporte y leaderboard.
Todo sale de Irys (la única fuente de verdad); no hay escrituras ni
autenticación — son datos ya públicos en Arweave, esta API solo los hace
consultables sin tocar GraphQL.

Ejecución (servicio `api` en docker-compose):
    uvicorn aisynergix.api:app --host 0.0.0.0 --port 8090
"""

import logging

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger("synergix.api")


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "synergix-public-api"})


async def stats(_: Request) -> JSONResponse:
    """Estadísticas globales de la red."""
    from aisynergix.services.irys import get_all_user_uids, compute_top10
    from aisynergix.nodes.node_manager import list_nodes
    try:
        uids = await get_all_user_uids()
        nodes = await list_nodes(limit=200)
        top = await compute_top10()
        total_points = sum(u.get("points", 0) for u in top)
        return JSONResponse({
            "users": len(uids),
            "nodes": len(nodes),
            "top10_points": total_points,
        })
    except Exception as exc:
        logger.warning("stats failed: %s", exc)
        return JSONResponse({"error": "unavailable"}, status_code=503)


async def top10(_: Request) -> JSONResponse:
    from aisynergix.services.irys import compute_top10
    try:
        return JSONResponse({"top10": await compute_top10()})
    except Exception as exc:
        logger.warning("top10 failed: %s", exc)
        return JSONResponse({"error": "unavailable"}, status_code=503)


async def nodes_index(_: Request) -> JSONResponse:
    from aisynergix.nodes.node_manager import list_nodes
    try:
        nodes = await list_nodes(limit=100)
        return JSONResponse({"nodes": [
            {
                "node_id": n.node_id, "name": n.name, "type": n.node_type,
                "language": n.language, "topics": n.topics,
            }
            for n in nodes
        ]})
    except Exception as exc:
        logger.warning("nodes failed: %s", exc)
        return JSONResponse({"error": "unavailable"}, status_code=503)


async def node_detail(request: Request) -> JSONResponse:
    from aisynergix.nodes.node_manager import get_node
    from aisynergix.nodes.knowledge_map import node_knowledge_map
    from aisynergix.services.irys import list_node_gaps
    node_id = request.path_params["node_id"]
    try:
        node = await get_node(node_id)
        if not node:
            return JSONResponse({"error": "not_found"}, status_code=404)
        coverage = await node_knowledge_map(node_id, node.topics) if node.topics else {}
        gaps = await list_node_gaps(node_id, limit=10)
        return JSONResponse({
            "node_id": node.node_id,
            "name": node.name,
            "type": node.node_type,
            "language": node.language,
            "topics": node.topics,
            "members": node.member_count,
            "contributions": node.aporte_count,
            "knowledge_map": coverage,
            "knowledge_gaps": [
                {"question": g.get("question", ""), "language": g.get("language", "")}
                for g in gaps
            ],
        })
    except Exception as exc:
        logger.warning("node_detail %s failed: %s", node_id, exc)
        return JSONResponse({"error": "unavailable"}, status_code=503)


async def node_bounties(request: Request) -> JSONResponse:
    """Bounties de conocimiento de un nodo (Proof-of-Knowledge, §7.4).

    Deja a una organización externa ver qué vacíos tienen pool activo y cuánto
    queda — la superficie de demanda del protocolo.
    """
    from aisynergix.services.bounties import list_bounties
    node_id = request.path_params["node_id"]
    try:
        items = await list_bounties(node_id)
        return JSONResponse({"node_id": node_id, "bounties": [
            {
                "bounty_id": b.get("bounty-id", ""),
                "topic": b.get("topic", ""),
                "status": b.get("bounty-status", "open"),
                "open": bool(b.get("open")),
                "pool": float(b.get("pool", 0) or 0),
                "reward": float(b.get("reward", 0) or 0),
                "remaining": float(b.get("remaining", 0) or 0),
                "paid_count": int(b.get("paid-count", 0) or 0),
                "deadline": int(b.get("deadline", 0) or 0),
            }
            for b in items
        ]})
    except Exception as exc:
        logger.warning("node_bounties %s failed: %s", node_id, exc)
        return JSONResponse({"error": "unavailable"}, status_code=503)


async def passport(request: Request) -> JSONResponse:
    """Passport público por Ghost ID — reputación verificable sin identidad."""
    from aisynergix.services.passport import get_passport
    ghost_id = request.path_params["ghost_id"]
    try:
        record = await get_passport(ghost_id)
        if not record:
            return JSONResponse({"error": "not_found"}, status_code=404)
        from aisynergix.services.irys import IRYS_GATEWAY_URL
        return JSONResponse({
            "passport": record["data"],
            "arweave_tx": record["tx"],
            "arweave_url": f"{IRYS_GATEWAY_URL}/{record['tx']}",
        })
    except Exception as exc:
        logger.warning("passport %s failed: %s", ghost_id, exc)
        return JSONResponse({"error": "unavailable"}, status_code=503)


async def impact(request: Request) -> JSONResponse:
    """Contador de impacto público de un aporte (§7.4)."""
    from aisynergix.services.irys import read_impact_counter
    aporte_tx = request.path_params["aporte_tx"]
    try:
        counter = await read_impact_counter(aporte_tx)
        if not counter:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse({
            "aporte_tx": aporte_tx,
            "views": int(counter.get("views", 0) or 0),
            "useful": int(counter.get("useful", 0) or 0),
            "references": int(counter.get("references", 0) or 0),
            "royalties_synx": float(counter.get("synx-paid", 0) or 0),
        })
    except Exception as exc:
        logger.warning("impact %s failed: %s", aporte_tx[:12], exc)
        return JSONResponse({"error": "unavailable"}, status_code=503)


routes = [
    Route("/api/health", health),
    Route("/api/stats", stats),
    Route("/api/top10", top10),
    Route("/api/nodes", nodes_index),
    Route("/api/nodes/{node_id}", node_detail),
    Route("/api/nodes/{node_id}/bounties", node_bounties),
    Route("/api/passport/{ghost_id}", passport),
    Route("/api/impact/{aporte_tx}", impact),
]

app = Starlette(
    routes=routes,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"],
        ),
    ],
)
