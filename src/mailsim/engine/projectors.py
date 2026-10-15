"""Projection handlers: how each event type mutates the mailbox-state tables.

These are the only code paths that write projection tables. Replay re-runs them over the
recorded event payloads, so every payload must be self-contained (ids, timestamps and blob
references are recorded, never recomputed).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from mailsim.domain.clock import parse_ts
from mailsim.engine.log import projector
from mailsim.providers.ids import gmail_id, graph_id
from mailsim.store import models as m


def _map_ids(session: Session, run: m.Run, entity_type: str, entity_id: str) -> None:
    for provider, fn in (("gmail", gmail_id), ("graph", graph_id)):
        session.add(m.ProviderId(run_id=run.id, provider=provider, entity_type=entity_type,
                                 external_id=fn(entity_id), entity_id=entity_id))  # fmt: skip


def _unmap_ids(session: Session, run: m.Run, entity_type: str, entity_ids: list[str]) -> None:
    session.execute(
        delete(m.ProviderId).where(
            m.ProviderId.run_id == run.id,
            m.ProviderId.entity_type == entity_type,
            m.ProviderId.entity_id.in_(entity_ids),
        )
    )


def _next_uid(session: Session, run: m.Run, folder_id: str) -> int:
    folder = session.get(m.Folder, (folder_id, run.id))
    assert folder is not None, f"unknown folder {folder_id}"
    uid = folder.uid_next
    folder.uid_next = uid + 1
    return uid


@projector("org.created")
def _org(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    session.add(
        m.Organization(run_id=run.id, id=p["id"], name=p["name"], domain=p["domain"],
                       policies=p["policies"])
    )  # fmt: skip


@projector("persona.created")
def _persona(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    session.add(
        m.Persona(run_id=run.id, id=p["id"], role=p["role"], email=p["email"],
                  knowledge_scope=p["knowledge_scope"], response_policy=p["response_policy"])
    )  # fmt: skip


@projector("relationship.created")
def _relationship(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    session.add(
        m.Relationship(run_id=run.id, id=p["id"], source_persona=p["source_persona"],
                       target_persona=p["target_persona"], type=p["type"],
                       trust_level=p["trust_level"])
    )  # fmt: skip


@projector("workproject.created")
def _workproject(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    session.add(
        m.WorkProject(run_id=run.id, id=p["id"], name=p["name"], participants=p["participants"],
                      facts=p["facts"])
    )  # fmt: skip


@projector("mailbox.created")
def _mailbox(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    session.add(
        m.Mailbox(run_id=run.id, id=p["id"], owner_id=p["owner_id"], email=p["email"],
                  provider_profile=p["provider_profile"], settings=p["settings"])
    )  # fmt: skip
    for f in p["folders"]:
        session.add(
            m.Folder(run_id=run.id, id=f["id"], mailbox_id=p["id"], name=f["name"],
                     system_type=f["system_type"], uid_next=1, uid_validity=run.epoch + 1)
        )  # fmt: skip


def _insert_message(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    msg = p["message"]
    if p.get("new_thread"):
        t = p["new_thread"]
        session.add(
            m.Thread(run_id=run.id, id=t["id"], mailbox_id=msg["mailbox_id"],
                     subject_key=t["subject_key"])
        )  # fmt: skip
        _map_ids(session, run, "thread", t["id"])
        session.flush()
    merged = p.get("merged_threads") or []
    if merged:
        session.execute(
            update(m.Message)
            .where(m.Message.run_id == run.id, m.Message.thread_id.in_(merged))
            .values(thread_id=msg["thread_id"])
        )
        session.execute(delete(m.Thread).where(m.Thread.run_id == run.id, m.Thread.id.in_(merged)))
        _unmap_ids(session, run, "thread", merged)
    session.add(
        m.Message(
            run_id=run.id, id=msg["id"], mailbox_id=msg["mailbox_id"],
            message_id_header=msg["message_id_header"], thread_id=msg["thread_id"],
            folder_id=msg["folder_id"], from_=msg["from"], to=msg["to"], cc=msg["cc"],
            bcc=msg["bcc"], subject=msg["subject"], body_text=msg["body_text"],
            body_html=msg["body_html"], in_reply_to=msg["in_reply_to"],
            references=msg["references"], created_at=parse_ts(msg["created_at"]),
            received_at=parse_ts(msg["received_at"]) if msg["received_at"] else None,
            labels=msg["labels"], flags=msg["flags"], headers=msg["headers"],
            is_draft=msg["is_draft"], raw_blob_ref=msg["raw_blob_ref"],
            provider_ids=msg.get("provider_ids") or {},
            imap_uid=_next_uid(session, run, msg["folder_id"]),
        )
    )  # fmt: skip
    _map_ids(session, run, "message", msg["id"])
    draft_id = (msg.get("provider_ids") or {}).get("draft_id")
    if draft_id:  # drafts: stable external draft id -> the message currently holding the draft
        for provider, fn in (("gmail", gmail_id), ("graph", graph_id)):
            session.add(m.ProviderId(run_id=run.id, provider=provider, entity_type="draft",
                                     external_id=fn(draft_id), entity_id=msg["id"]))  # fmt: skip
    session.flush()
    for a in msg["attachments"]:
        session.add(
            m.Attachment(run_id=run.id, id=a["id"], message_id=msg["id"], blob_ref=a["blob_ref"],
                         mime_type=a["mime_type"], filename=a["filename"], checksum=a["checksum"],
                         size=a["size"], inline=a["inline"], content_id=a["content_id"])
        )  # fmt: skip


for _t in (
    "message.delivered",
    "message.sent",
    "draft.created",
    "message.inserted",
    "message.copied",
):
    projector(_t)(_insert_message)


def _msg(session: Session, run: m.Run, message_id: str) -> m.Message:
    row = session.get(m.Message, (message_id, run.id))
    assert row is not None, f"projection references unknown message {message_id}"
    return row


@projector("message.moved")
def _moved(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    row = _msg(session, run, p["message_id"])
    if row.folder_id == p["folder_id"]:
        return  # same-folder move: no new UID, origin kept for untrash
    row.previous_folder_id = row.folder_id
    row.folder_id = p["folder_id"]
    row.imap_uid = _next_uid(session, run, p["folder_id"])  # IMAP: a moved message gets a new UID


@projector("message.labels_changed")
def _labels(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    row = _msg(session, run, p["message_id"])
    row.labels = sorted((set(row.labels) | set(p["add"])) - set(p["remove"]))


@projector("message.flags_changed")
def _flags(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    row = _msg(session, run, p["message_id"])
    row.flags = sorted((set(row.flags) | set(p["add"])) - set(p["remove"]))


@projector("message.deleted")
def _deleted(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    session.execute(
        delete(m.Attachment).where(
            m.Attachment.run_id == run.id, m.Attachment.message_id == p["message_id"]
        )
    )
    session.execute(
        delete(m.Message).where(m.Message.run_id == run.id, m.Message.id == p["message_id"])
    )
    _unmap_ids(session, run, "message", [p["message_id"]])
    _unmap_ids(session, run, "draft", [p["message_id"]])
    if p.get("empty_thread"):
        session.execute(
            delete(m.Thread).where(m.Thread.run_id == run.id, m.Thread.id == p["empty_thread"])
        )
        _unmap_ids(session, run, "thread", [p["empty_thread"]])


@projector("delivery.sunk")
def _sunk(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    session.add(
        m.SinkMessage(run_id=run.id, id=p["id"], recipient=p["recipient"],
                      raw_blob_ref=p["raw_blob_ref"], sent_at=parse_ts(p["sent_at"]))
    )  # fmt: skip


@projector("event.scheduled")
def _scheduled(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    session.add(
        m.ScheduledEvent(run_id=run.id, id=p["id"],
                         due_at=parse_ts(p["due_at"]) if p["due_at"] else None,
                         order_key=p["order_key"], trigger=p["trigger"], spec=p["spec"],
                         status=p["status"])
    )  # fmt: skip


@projector("event.armed")
def _armed(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    row = session.get(m.ScheduledEvent, (p["id"], run.id))
    assert row is not None
    row.due_at = parse_ts(p["due_at"])
    row.status = "armed"


@projector("event.fired")
def _fired(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    row = session.get(m.ScheduledEvent, (p["id"], run.id))
    assert row is not None
    row.status = "fired"
