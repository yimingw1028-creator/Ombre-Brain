"""二改第 4 刀：只有强命中才续命。

breath_search 返回的桶里，只有「查询串原样命中」或「语义相似度够高」的才会被
touch（刷新 last_active、次数 +1）。靠混合分凑过线的弱命中、语义只是沾边的命中，
照样返回给模型看，但不动它的时间——否则一次没搜准的搜索会把一批不相干的旧桶
当成「刚发生的」，塞进当晚的 dream 和第二天的 breath。
"""

import asyncio
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from tools.breath import dispatch
from tools.breath.search import _counts_as_recall


class DisabledEmbedding:
    enabled = False


class NoopDecay:
    is_running = True

    async def ensure_started(self):
        return None

    def calculate_score(self, metadata):
        return float(metadata.get("importance") or 0)


class FlaggedBucketManager:
    """模拟检索层：每条结果都带着 literal_hit / semantic_score。"""

    def __init__(self, buckets):
        self.buckets = list(buckets)
        self.touched = []

    async def get(self, _bucket_id):
        return None

    async def search(self, _query, **_kwargs):
        return list(self.buckets)

    async def list_all(self, include_archive=False):
        return list(self.buckets)

    async def touch_many(self, bucket_ids, ripple=False):
        assert ripple is False
        self.touched.extend(bucket_ids)


def _bucket(bucket_id, content, *, literal_hit, semantic_score, vector_match=False):
    bucket = {
        "id": bucket_id,
        "content": content,
        "literal_hit": literal_hit,
        "semantic_score": semantic_score,
        "metadata": {
            "name": bucket_id,
            "type": "dynamic",
            "importance": 5,
            "domain": ["回归测试"],
            "created": "2026-07-19T12:00:00",
        },
    }
    if vector_match:
        bucket["vector_match"] = True
    return bucket


def _install_runtime(monkeypatch, manager, surfacing=None):
    monkeypatch.setattr(rt, "config", {"surfacing": dict(surfacing or {})})
    monkeypatch.setattr(rt, "bucket_mgr", manager)
    monkeypatch.setattr(rt, "decay_engine", NoopDecay())
    monkeypatch.setattr(rt, "dehydrator", MagicMock())
    monkeypatch.setattr(rt, "embedding_engine", DisabledEmbedding())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "fire_webhook", None)
    monkeypatch.setattr(rt, "mark_op", None)
    monkeypatch.setattr(rt, "record_v3_tool_event", lambda *_a, **_k: None)
    monkeypatch.setattr("tools.breath.search.random.random", lambda: 1.0)


def _four_kinds():
    return [
        _bucket("literal", "正文里原样写着跳舞。", literal_hit=True, semantic_score=None),
        _bucket(
            "strong-semantic", "语义很近的一条。",
            literal_hit=False, semantic_score=0.82, vector_match=True,
        ),
        _bucket(
            "weak-semantic", "语义只是沾边的一条。",
            literal_hit=False, semantic_score=0.67, vector_match=True,
        ),
        _bucket("blended-only", "靠混合分凑过线的一条。", literal_hit=False, semantic_score=0.31),
    ]


@pytest.mark.asyncio
async def test_only_strong_hits_are_touched_but_all_are_returned(monkeypatch):
    manager = FlaggedBucketManager(_four_kinds())
    _install_runtime(monkeypatch, manager)

    output = await dispatch(query="跳舞", max_results=10, max_tokens=6000)
    await asyncio.sleep(0)

    # 四条都照样返回给模型看
    for bucket_id in ("literal", "strong-semantic", "weak-semantic", "blended-only"):
        assert f"[bucket_id:{bucket_id}]" in output
    # 只有字面命中和强语义命中被续命
    assert manager.touched == ["literal", "strong-semantic"]


@pytest.mark.asyncio
async def test_semantic_header_shows_similarity(monkeypatch):
    manager = FlaggedBucketManager(_four_kinds())
    _install_runtime(monkeypatch, manager)

    output = await dispatch(query="跳舞", max_results=10, max_tokens=6000)
    await asyncio.sleep(0)

    assert "[语义关联] [相似度:0.82] [bucket_id:strong-semantic]" in output
    assert "[语义关联] [相似度:0.67] [bucket_id:weak-semantic]" in output


@pytest.mark.asyncio
async def test_touch_mode_all_restores_original_behaviour(monkeypatch):
    manager = FlaggedBucketManager(_four_kinds())
    _install_runtime(monkeypatch, manager, surfacing={"touch_mode": "all"})

    await dispatch(query="跳舞", max_results=10, max_tokens=6000)
    await asyncio.sleep(0)

    assert manager.touched == [
        "literal", "strong-semantic", "weak-semantic", "blended-only",
    ]


@pytest.mark.asyncio
async def test_touch_semantic_min_is_configurable(monkeypatch):
    manager = FlaggedBucketManager(_four_kinds())
    _install_runtime(monkeypatch, manager, surfacing={"touch_semantic_min": 0.65})

    await dispatch(query="跳舞", max_results=10, max_tokens=6000)
    await asyncio.sleep(0)

    assert manager.touched == ["literal", "strong-semantic", "weak-semantic"]


def test_counts_as_recall_rules():
    strong = ("strong", 0.75)
    assert _counts_as_recall({"literal_hit": True, "semantic_score": None}, *strong)
    assert _counts_as_recall({"literal_hit": False, "semantic_score": 0.75}, *strong)
    assert not _counts_as_recall({"literal_hit": False, "semantic_score": 0.74}, *strong)
    assert not _counts_as_recall({"literal_hit": False, "semantic_score": None}, *strong)
    assert not _counts_as_recall({"literal_hit": False, "semantic_score": "坏值"}, *strong)
    # 检索层没给这两个字段（旧的或第三方 manager）→ 保持原行为
    assert _counts_as_recall({"id": "legacy"}, *strong)
    # 开关退回原样
    assert _counts_as_recall({"literal_hit": False, "semantic_score": 0.1}, "all", 0.75)


@pytest.mark.asyncio
async def test_real_search_marks_literal_hits(bucket_mgr):
    """真实检索层要把 literal_hit 带出来，上面的规则才有东西可看。"""
    bucket_id = await bucket_mgr.create(
        content="2026-11-12 June 跳完舞回来，老师说她对 4 Walls 太熟反而抢拍。",
        name="抢拍",
        domain=["回归测试"],
    )

    hits = await bucket_mgr.search("抢拍", limit=5)

    hit = next(b for b in hits if b["id"] == bucket_id)
    assert hit["literal_hit"] is True
    assert "semantic_score" in hit
