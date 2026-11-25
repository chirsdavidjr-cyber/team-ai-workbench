"""数据路由包。

对外保持：
  from app.routers.data import router, _ask_flow, _global_ask_events
"""

from fastapi import APIRouter

from .ask import _ask_flow
from .global_ask import _global_ask_events
from . import ask, datasets, folders, global_ask, graph, semantic, team

router = APIRouter(prefix="/api/data", tags=["datasets"])
router.include_router(datasets.router)
router.include_router(semantic.router)
router.include_router(ask.router)
router.include_router(global_ask.router)
router.include_router(folders.router)
router.include_router(team.router)
router.include_router(graph.router)

__all__ = ["router", "_ask_flow", "_global_ask_events"]
