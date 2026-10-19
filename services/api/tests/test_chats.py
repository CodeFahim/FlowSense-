"""Several assistant chats per user: new, open, rename, delete; strict isolation between users and
workspaces (another user's chat id behaves like a missing one)."""

from __future__ import annotations

import uuid

from tests.test_agent import _user


def test_chats_can_be_started_opened_renamed_and_deleted():
    api = _user()
    assert api.get("/api/assistant/chats").json()["chats"] == []
    first = api.post("/api/assistant/chat", {"message": "Where do I add a bill?"}).json()
    c1 = first["chat"]
    assert c1["title"] == "Where do I add a bill?" and c1["messages"] == 2
    # New chat: empty, becomes the default; asking there keeps the first chat intact.
    c2 = api.post("/api/assistant/chats").json()
    assert c2["id"] != c1["id"] and c2["messages"] == 0
    assert api.post("/api/assistant/chats").json()["id"] == c2["id"]  # no pile of blank chats
    api.post("/api/assistant/chat", {"message": "How do I get started?", "chat_id": c2["id"]})
    chats = api.get("/api/assistant/chats").json()["chats"]
    assert [c["title"] for c in chats] == ["How do I get started?", "Where do I add a bill?"]
    old = api.get(f"/api/assistant/conversation?chat_id={c1['id']}").json()
    assert [m["role"] for m in old["messages"]] == ["user", "assistant"] and old["messages"][0]["text"] == "Where do I add a bill?"
    # Continue an older chat explicitly.
    api.post("/api/assistant/chat", {"message": "Where do I add an invoice?", "chat_id": c1["id"]})
    assert len(api.get(f"/api/assistant/conversation?chat_id={c1['id']}").json()["messages"]) == 4
    # Rename (trimmed, control characters removed) and delete.
    r = api.c.patch(f"/api/assistant/chats/{c1['id']}", json={"title": "  Bills\tand  invoices \u0007"}, headers={"X-CSRF-Token": api.csrf})
    assert r.status_code == 200 and r.json()["title"] == "Bills and invoices"
    assert api.c.patch(f"/api/assistant/chats/{c1['id']}", json={"title": "   "}, headers={"X-CSRF-Token": api.csrf}).status_code == 422
    d = api.c.delete(f"/api/assistant/chats/{c1['id']}", headers={"X-CSRF-Token": api.csrf})
    assert d.status_code == 200 and d.json()["messages"] == 4
    assert [c["id"] for c in api.get("/api/assistant/chats").json()["chats"]] == [c2["id"]]
    assert api.get(f"/api/assistant/conversation?chat_id={c1['id']}").status_code == 404


def test_chats_are_isolated_between_users_and_workspaces():
    a, b = _user(), _user()
    chat = a.post("/api/assistant/chat", {"message": "private question from A"}).json()["chat"]
    cid = chat["id"]
    # B can't see, read, write into, rename or delete A's chat.
    assert b.get("/api/assistant/chats").json()["chats"] == []
    assert b.get(f"/api/assistant/conversation?chat_id={cid}").status_code == 404
    assert b.post("/api/assistant/chat", {"message": "sneaky", "chat_id": cid}).status_code == 404
    assert b.c.patch(f"/api/assistant/chats/{cid}", json={"title": "mine"}, headers={"X-CSRF-Token": b.csrf}).status_code == 404
    assert b.c.delete(f"/api/assistant/chats/{cid}", headers={"X-CSRF-Token": b.csrf}).status_code == 404
    assert b.get(f"/api/assistant/conversation?chat_id={uuid.uuid4()}").status_code == 404
    assert b.get("/api/assistant/conversation?chat_id=not-a-uuid").status_code == 404
    assert [m["text"] for m in a.get(f"/api/assistant/conversation?chat_id={cid}").json()["messages"]][0] == "private question from A"
    # A's private sample workspace has its own, separate chats.
    sample = a.post("/api/workspaces/sample").json()
    a.csrf = sample["csrf_token"]
    assert a.get("/api/assistant/chats").json()["chats"] == []
    assert a.get(f"/api/assistant/conversation?chat_id={cid}").status_code == 404
