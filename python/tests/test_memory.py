"""Memory: storage, recall relevance, dedupe, poisoning-relevant parsing."""

from memory.extractor import parse_facts
from memory.service import MemoryService
from memory.vector_store import InMemoryVectorStore, cosine


class TestVectorMath:
    def test_cosine_identical(self):
        assert abs(cosine([1, 2, 3], [1, 2, 3]) - 1.0) < 1e-9

    def test_cosine_orthogonal(self):
        assert abs(cosine([1, 0], [0, 1])) < 1e-9

    def test_zero_vector_safe(self):
        assert cosine([0, 0], [1, 1]) == 0.0  # no ZeroDivisionError


class TestMemoryService:
    async def test_add_and_recall_exact(self, db, embedder):
        svc = MemoryService(db, embedder, InMemoryVectorStore())
        await svc.add("User prefers tea over coffee", "preference")
        hits = await svc.recall("User prefers tea over coffee")
        assert len(hits) == 1 and hits[0]["text"] == "User prefers tea over coffee"

    async def test_relevance_floor_filters_noise(self, db, embedder):
        svc = MemoryService(db, embedder, InMemoryVectorStore())
        await svc.add("User's dog is named Biscuit", "profile")
        # hash-based fake vectors: unrelated text ≈ random ≈ low cosine
        hits = await svc.recall("completely unrelated query about quantum physics", floor=0.95)
        assert hits == []

    async def test_dedupe_updates_instead_of_inserting(self, db, embedder):
        svc = MemoryService(db, embedder, InMemoryVectorStore())
        embedder.alias("likes tea", "prefers tea")  # same vector -> sim 1.0
        await svc.add("likes tea", "preference")
        await svc.add("prefers tea", "preference")
        rows = await svc.list_all()
        assert len(rows) == 1
        assert rows[0]["text"] == "prefers tea"  # fresher wording won

    async def test_disabled_memories_not_recalled(self, db, embedder):
        svc = MemoryService(db, embedder, InMemoryVectorStore())
        row = await svc.add("User works night shifts", "profile")
        await svc.set_enabled(row["id"], False)
        assert await svc.recall("User works night shifts") == []

    async def test_delete_removes_everywhere(self, db, embedder):
        store = InMemoryVectorStore()
        svc = MemoryService(db, embedder, store)
        row = await svc.add("temporary fact", "other")
        await svc.delete(row["id"])
        assert await svc.list_all() == [] and store.count() == 0

    async def test_embedder_down_fails_soft(self, db, embedder):
        svc = MemoryService(db, embedder, InMemoryVectorStore())
        embedder.fail = True
        assert await svc.add("won't be saved") is None
        assert await svc.recall("anything") == []
        assert svc.available is False  # /system/status surfaces this

    async def test_rebuild_index_from_sqlite(self, db, embedder):
        """Chroma dir wiped -> memories come back from the canonical store."""
        svc = MemoryService(db, embedder, InMemoryVectorStore())
        await svc.add("resilient fact", "other")
        fresh_store = InMemoryVectorStore()
        svc2 = MemoryService(db, embedder, fresh_store)
        assert await svc2.rebuild_index() == 1
        assert (await svc2.recall("resilient fact"))[0]["text"] == "resilient fact"


class TestFactParsing:
    def test_clean_json(self):
        facts = parse_facts('[{"fact": "User lives in Austin", "category": "profile"}]')
        assert facts == [{"fact": "User lives in Austin", "category": "profile"}]

    def test_json_wrapped_in_prose_and_fences(self):
        raw = 'Sure! Here are the facts:\n```json\n[{"fact": "Prefers dark mode", "category": "preference"}]\n```'
        assert parse_facts(raw)[0]["fact"] == "Prefers dark mode"

    def test_garbage_returns_empty(self):
        assert parse_facts("I could not find any facts, sorry!") == []
        assert parse_facts("[not valid json") == []
        assert parse_facts('{"fact": "not a list"}') == []

    def test_invalid_category_normalized(self):
        facts = parse_facts('[{"fact": "Plays chess on Sundays", "category": "hobbies!!"}]')
        assert facts[0]["category"] == "other"

    def test_caps_and_length_limits(self):
        many = "[" + ",".join(f'{{"fact": "fact number {i} padding", "category": "other"}}' for i in range(20)) + "]"
        assert len(parse_facts(many)) == 5
        assert parse_facts('[{"fact": "hi", "category": "other"}]') == []  # too short
