"""Chat and the student's own dashboard data."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import agent
from ..config import get_settings
from ..db import get_db, session_factory
from ..models import Conversation, Message, User
from ..security import current_user
from ..tools import ToolContext, run_tool

router = APIRouter(prefix="/api", tags=["student"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # DB timestamps are naive UTC


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = None


class ApplyIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


def _own_conversation(db: Session, user: User, conv_id: int) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if not conv or conv.user_id != user.id:
        raise HTTPException(404, "Conversa não encontrada.")
    return conv


def _check_rate_limit(db: Session, user: User) -> None:
    limit = get_settings().chat_rate_limit_per_hour
    if limit <= 0 or user.role != "student":
        return
    since = _utcnow() - timedelta(hours=1)
    count = db.scalar(
        select(func.count(Message.id)).join(Conversation)
        .where(Conversation.user_id == user.id, Message.role == "user", Message.created_at >= since)
    )
    if count >= limit:
        raise HTTPException(429, f"Atingiste o limite de {limit} mensagens por hora. Tenta mais tarde.")


@router.post("/chat")
async def chat(body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _check_rate_limit(db, user)
    if body.conversation_id:
        conv = _own_conversation(db, user, body.conversation_id)
    else:
        conv = Conversation(user_id=user.id, title=body.message.strip()[:80])
        db.add(conv)
        db.commit()
    history = [{"role": m.role, "content": m.content} for m in conv.messages]
    user_id, conv_id, question = user.id, conv.id, body.message.strip()

    def work() -> dict:
        # Own session: this runs in a worker thread.
        with session_factory()() as tdb:
            return agent.answer(tdb, tdb.get(User, user_id), history, question)

    result = await asyncio.to_thread(work)
    db.add(Message(conversation_id=conv_id, role="user", content=question))
    bot = Message(conversation_id=conv_id, role="assistant", content=result["answer"],
                  meta=json.dumps({"sources": result["sources"], "tools": result["tools"], "mode": result["mode"]},
                                  ensure_ascii=False))
    db.add(bot)
    conv.updated_at = _utcnow()
    db.commit()
    return {"conversation_id": conv_id, "message_id": bot.id, "answer": result["answer"],
            "sources": result["sources"], "mode": result["mode"]}


@router.get("/conversations")
def list_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    convs = db.scalars(select(Conversation).where(Conversation.user_id == user.id)
                       .order_by(Conversation.updated_at.desc()).limit(100))
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()} for c in convs]


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    conv = _own_conversation(db, user, conv_id)
    return {"id": conv.id, "title": conv.title, "messages": [
        {"id": m.id, "role": m.role, "content": m.content,
         "sources": (json.loads(m.meta).get("sources", []) if m.meta else [])}
        for m in conv.messages
    ]}


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_own_conversation(db, user, conv_id))
    db.commit()
    return {"ok": True}


def _ctx(db: Session, user: User) -> ToolContext:
    return ToolContext(db=db, user=user, now=agent.school_now())


@router.get("/me/overview")
def overview(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Side panel: today's classes, money due, upcoming activities."""
    ctx = _ctx(db, user)
    return {
        "today": run_tool(ctx, "get_my_schedule", {"days": 2}),
        "charges": run_tool(ctx, "get_my_charges", {}),
        "activities": run_tool(ctx, "list_activities", {}).get("activities", [])[:6],
        "llm_enabled": get_settings().llm_enabled,
    }


@router.get("/me/schedule")
def schedule(date: str | None = None, days: int = 7, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return run_tool(_ctx(db, user), "get_my_schedule", {"date": date, "days": days})


@router.post("/activities/{activity_id}/apply")
def apply(activity_id: int, body: ApplyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # A click on the button is the confirmation.
    result = run_tool(_ctx(db, user), "apply_to_activity",
                      {"activity_id": activity_id, "confirmed": True, "note": body.note})
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result
