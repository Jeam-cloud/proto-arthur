"""Database + conversation store."""

from core.conversations import ConversationStore
from core.db import MIGRATIONS, Database


async def test_migrations_are_idempotent(settings):
    db = Database(settings.db_path)
    await db.connect()
    await db.close()
    db2 = Database(settings.db_path)  # reopen: migrations must not re-run
    await db2.connect()
    # Compared against len(MIGRATIONS) rather than a hardcoded number: this
    # test is about migrations not RE-RUNNING, not about how many exist, and
    # pinning the literal meant every new migration failed a test that had
    # nothing to do with the change.
    assert await db2.fetch_one("PRAGMA user_version") == {"user_version": len(MIGRATIONS)}
    await db2.close()


async def test_an_existing_database_upgrades_without_losing_data(settings):
    """The migration path real users take, not the fresh-install one.

    Migration 2 adds a column to `conversations`. Anyone upgrading has rows in
    that table already, so the test that matters is that they survive and the
    new column arrives NULL rather than the table being rebuilt.
    """
    db = Database(settings.db_path)
    # Stop one step short of the newest migration to simulate an older install.
    db_migrations_backup = MIGRATIONS[:]
    try:
        MIGRATIONS[:] = db_migrations_backup[:-1]
        await db.connect()
        store = ConversationStore(db)
        convo = await store.create()
        await store.rename(convo["id"], "Existing chat")
        await db.close()

        MIGRATIONS[:] = db_migrations_backup  # now "ship" the new version
        db2 = Database(settings.db_path)
        await db2.connect()
        row = await db2.fetch_one("SELECT * FROM conversations WHERE id=?", (convo["id"],))
        assert row["title"] == "Existing chat"
        assert row["workspace_root"] is None
        await db2.close()
    finally:
        MIGRATIONS[:] = db_migrations_backup


async def test_a_conversation_remembers_its_folder(db):
    store = ConversationStore(db)
    convo = await store.create()
    assert (await store.get(convo["id"]))["workspace_root"] is None

    await store.set_workspace(convo["id"], "/home/me/project")
    assert (await store.get(convo["id"]))["workspace_root"] == "/home/me/project"

    # Clearing returns it to inheriting, not to a literal empty string -- the
    # resolution order in routes._conversation_workspace tests for falsiness.
    await store.set_workspace(convo["id"], None)
    assert (await store.get(convo["id"]))["workspace_root"] is None
    await store.set_workspace(convo["id"], "")
    assert (await store.get(convo["id"]))["workspace_root"] is None


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
