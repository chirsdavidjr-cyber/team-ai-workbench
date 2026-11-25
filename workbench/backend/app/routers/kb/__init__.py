"""知识库路由包。

对外保持：
  from app.routers.kb import router, extract_file, apply_kb_op, kb_context_text, request_team
"""

from fastapi import APIRouter

from .extract import extract_file
from .ops import apply_kb_op, kb_context_text
from .reviews import request_team
from . import docs, folders, search, reviews, graph

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])
router.include_router(docs.router)
router.include_router(folders.router)
router.include_router(search.router)
router.include_router(reviews.router)
router.include_router(graph.router)

__all__ = [
    "router",
    "extract_file",
    "apply_kb_op",
    "kb_context_text",
    "request_team",
]
