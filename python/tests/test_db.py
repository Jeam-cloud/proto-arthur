"""Database + conversation store."""

from core.conversations import ConversationStore
from core.db import Database


async def test_migrations_are_idempotent(settings):
    db = Database(settings.db_path)
    await db.connect()
    await db.close()
    db2 = Database(settings.db_path)  # reopen: migrations must not re-run
    await db2.connect()
    assert await db2.fetch_one("PRAGMA user_version") == {"user_version": 1}
    await db2.close()


async def test_settings_roundtrip(db):
    await db.set_setting("default_model", "qwen3:14b")
    assert await db.get_setting("default_model") == "qwen3:14b"
    await db.set_setting("default_model", "llama3.3:8b")  # upsert
    assert await db.get_setting("default_model") == "llama3.3:8b"
    assert await db.get_setting("missing", "fallback") == "fallback"


async def test_conversation_crud_and_cascade(db):
    store = ConversationStore(db)
    conv = await store.create()
    await store.add_message(conv["id"], "user", "hello")
    await store.add_message(conv["id"], "assistant", "hi there")
    assert len(await store.messages(conv["id"])) == 2

    await store.delete(conv["id"])
    orphans = await db.fetch_all("SELECT * FROM messages WHERE conversation_id=?", (conv["id"],))
    assert orphans == []  # ON DELETE CASCADE did its job


async def test_history_budget_keeps_newest(db):
    store = ConversationStore(db)
    conv = await store.create()
    for i in range(10):
        await store.add_message(conv["id"], "user", f"message {i} " + "x" * 400)
    history = await store.history_for_model(conv["id"], char_budget=1000)
    assert history  # something survived
    assert history[-1]["content"].startswith("message 9")  # newest kept
    assert len(history) < 10  # oldest trimmed


async def test_history_excludes_tool_messages(db):
    store = ConversationStore(db)
    conv = await store.create()
    await store.add_message(conv["id"], "user", "question")
    await store.add_message(conv["id"], "tool", "raw tool output", tool_name="echo")
    await store.add_message(conv["id"], "assistant", "answer")
    roles = [m["role"] for m in await store.history_for_model(conv["id"])]
    assert "tool" not in roles
