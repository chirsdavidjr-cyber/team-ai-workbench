"""入团队库审核流。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...deps import get_admin_user, get_current_user, get_db
from ...kb_service import ai_preliminary_review
from ...models import KbDoc, KbReview, User
from .common import _doc_out, _get_editable_doc

router = APIRouter()

@router.post("/docs/{doc_id}/request-team")
async def request_team(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """申请入团队库：AI 预审（查重/质量/敏感）→ 进入管理员审核队列。"""
    d = _get_editable_doc(doc_id, user, db)
    if d.team_status == "pending":
        raise HTTPException(status_code=409, detail="该文档已在审核中")
    if d.team_status == "approved":
        raise HTTPException(status_code=409, detail="该文档已在团队库中")
    if d.status != "ready":
        raise HTTPException(status_code=400, detail="请先将文档入个人库（解析）后再申请")

    ai_review = await ai_preliminary_review(db, d)
    review = KbReview(
        doc_id=d.id, requester_id=user.id,
        ai_review_json=json.dumps(ai_review, ensure_ascii=False),
    )
    d.team_status = "pending"
    db.add(review)
    db.commit()
    return {"ok": True, "review_id": review.id, "ai_review": ai_review}


@router.get("/reviews")
def list_reviews(
    status: str = "pending",
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    stmt = select(KbReview).order_by(KbReview.id.desc()).limit(50)
    if status != "all":
        stmt = stmt.where(KbReview.status == status)
    rows = db.scalars(stmt).all()
    out = []
    for r in rows:
        doc = db.get(KbDoc, r.doc_id)
        if doc is None:
            continue
        requester = db.get(User, r.requester_id)
        out.append(
            {
                "id": r.id,
                "doc": _doc_out(doc),
                "requester": requester.name if requester else "?",
                "ai_review": r.ai_review,
                "status": r.status,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out


class ReviewDecisionIn(BaseModel):
    action: str  # approve | reject
    reason: str = ""


@router.post("/reviews/{review_id}/decide")
def decide_review(
    review_id: int,
    body: ReviewDecisionIn,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    r = db.get(KbReview, review_id)
    if r is None or r.status != "pending":
        raise HTTPException(status_code=404, detail="审核单不存在或已处理")
    doc = db.get(KbDoc, r.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档已被删除")
    if body.action == "approve":
        r.status = "approved"
        doc.team_status = "approved"
    elif body.action == "reject":
        if not body.reason.strip():
            raise HTTPException(status_code=400, detail="驳回需填写理由")
        r.status = "rejected"
        r.reason = body.reason.strip()
        doc.team_status = "rejected"
    else:
        raise HTTPException(status_code=400, detail="action 不合法")
    r.reviewer_id = admin.id
    db.commit()
    return {"ok": True}



