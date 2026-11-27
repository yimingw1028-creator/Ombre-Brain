"""二改第 8c 刀：breath 放不下全文的桶退成一行，头部带创建日期。

原来：
- 不传 query 的浮现：放不下剩余预算的桶整条跳过，只说「有 N 条没返回」；
  只要跳过过一条，「久未浮现」「偶然想起」两段整段不跑。
- 传 query 的检索：碰到第一条放不下的就全部停下，后面的都不显示。
- 每条头部没有日期。

现在：
- 放不下全文的桶退成一行「标题 | 创建日期 | 全文 N 字 | 桶ID」，不给半截正文；
  没标题取正文开头 20 字。后面放得下的照样给全文。
- 这些行算进预算；一行也放不下的，照实说还有几条。
- 连带关闭去掉。
- 每条头部末尾带创建日期 YYYY-MM-DD。
- 开关 surfacing.overflow_lines，默认开；false 三件事一起回到旧行为。
"""

from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from tools.breath import dispatch
from tools.breath._overflow import overflow_lines_enabled


class _Decay:
    is_running = True

    async def ensure_started(self):
        return None

    def calculate_score(self, meta):
        return float(meta.get("importance") or 5)


class _DisabledEmbedding:
    enabled = False

    async def search_similar(self, query, top_k=20):
        return []


class _Manager:
    def __init__(self, buckets):
        self.buckets = list(buckets)
        self.touched = []

    async def get(self, bucket_id):
        return next((b for b in self.buckets if b["id"] == bucket_id), None)

    async def list_all(self, include_archive=False):
        return list(self.buckets)

    async def search(self, query, **kwargs):
        return list(self.buckets)

    async def touch_many(self, bucket_ids, ripple=False):
        self.touched.extend(bucket_ids)

    async def get_stats(self):
        return {"permanent_count": 0, "dynamic_count": len(self.buckets)}


def _install(monkeypatch, buckets, surfacing=None):
    manager = _Manager(buckets)
    monkeypatch.setattr(rt, "config", {"surfacing": dict(surfacing or {})})
    monkeypatch.setattr(rt, "bucket_mgr", manager)
    monkeypatch.setattr(rt, "decay_engine", _Decay())
    monkeypatch.setattr(rt, "dehydrator", MagicMock())
    monkeypatch.setattr(rt, "embedding_engine", _DisabledEmbedding())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "fire_webhook", None)
    monkeypatch.setattr(rt, "mark_op", None)
    monkeypatch.setattr(rt, "record_v3_tool_event", lambda *_a, **_k: None)
    return manager


@pytest.fixture(autouse=True)
def _no_randomness(monkeypatch):
    monkeypatch.setattr("tools.breath.surface.random.shuffle", lambda items: None)
    monkeypatch.setattr("tools.breath.surface.random.random", lambda: 1.0)


def _memory(bucket_id, content, *, name=None, importance=9, created="2026-11-20T10:00:00",
            last_active=None, activation_count=1, **extra):
    meta = {
        "name": bucket_id if name is None else name,
        "type": "dynamic",
        "importance": importance,
        "activation_count": activation_count,
        "domain": [],
        "created": created,
    }
    if last_active:
        meta["last_active"] = last_active
    bucket = {"id": bucket_id, "content": content, "metadata": meta}
    bucket.update(extra)
    return bucket


_LONG = "很长的一天。" * 400          # 远远超过下面用的预算
_LONG_CHARS = len(_LONG)


# ---------------------------------------------------------------------------
# 开关
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "surfacing, expected",
    [
        ({}, True),
        ({"overflow_lines": True}, True),
        ({"overflow_lines": "随便写的"}, True),
        ({"overflow_lines": False}, False),
        ({"overflow_lines": "off"}, False),
        ({"overflow_lines": "false"}, False),
    ],
)
def test_switch_parsing(monkeypatch, surfacing, expected):
    monkeypatch.setattr(rt, "config", {"surfacing": surfacing})
    assert overflow_lines_enabled() is expected


def test_switch_missing_config_defaults_on(monkeypatch):
    monkeypatch.setattr(rt, "config", None)
    assert overflow_lines_enabled() is True


# ---------------------------------------------------------------------------
# 不传 query 的浮现
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_surface_header_has_created_date(monkeypatch):
    _install(monkeypatch, [_memory("mem", "今天的一件事。")])
    output = await dispatch(max_tokens=20000)
    assert "[权重:9.00] [bucket_id:mem] 2026-11-20\n" in output


@pytest.mark.asyncio
async def test_surface_overflow_becomes_line_and_next_still_full(monkeypatch):
    big = _memory("big", _LONG, name="很长的一条", importance=10)
    small = _memory("small", "一件短短的事。", importance=9)
    _install(monkeypatch, [big, small])
    output = await dispatch(max_tokens=300)

    assert _LONG not in output
    assert f"很长的一条 | 2026-11-20 | 全文 {_LONG_CHARS} 字 | big" in output
    assert "（预算放不下全文，只列一行，按桶 ID 搜可看全文：）" in output
    # 排在后面、放得下的照样给全文
    assert "一件短短的事。" in output
    # 退成一行不算省略
    assert "token 预算不足" not in output


@pytest.mark.asyncio
async def test_surface_line_without_title_uses_first_20_chars(monkeypatch):
    body = "一二三四五六七八九十\n甲乙丙丁戊己庚辛壬癸子丑寅卯" + "很长" * 800
    _install(monkeypatch, [_memory("old", body, name="")])
    output = await dispatch(max_tokens=200)
    assert "一二三四五六七八九十 甲乙丙丁戊己庚辛壬 | 2026-11-20 | 全文" in output


@pytest.mark.asyncio
async def test_surface_line_counts_against_budget(monkeypatch):
    buckets = [
        _memory(f"m{i}", _LONG, name=f"第{i}条很长的记忆", importance=9)
        for i in range(3)
    ]
    _install(monkeypatch, buckets)
    # 预算小到连一行都放不下：照实说有几条没返回
    output = await dispatch(max_tokens=5)
    assert "第0条很长的记忆" not in output
    assert "有 3 条主要浮现记忆因放不下剩余预算而未返回" in output


@pytest.mark.asyncio
async def test_surface_passive_still_runs_when_something_overflowed(monkeypatch):
    big = _memory("big", _LONG, name="很长的一条", importance=10)
    sleepy = _memory(
        "sleepy", "很久没想起的一件事。", importance=9,
        last_active="2026-01-01T00:00:00",
    )
    _install(monkeypatch, [big, sleepy])
    output = await dispatch(max_tokens=300, max_results=1)
    assert "很长的一条 | 2026-11-20" in output
    assert "=== 久未浮现 ===" in output
    assert "💤 [久未浮现] [bucket_id:sleepy] 2026-11-20" in output


@pytest.mark.asyncio
async def test_surface_switch_off_restores_old_behavior(monkeypatch):
    big = _memory("big", _LONG, name="很长的一条", importance=10)
    sleepy = _memory(
        "sleepy", "很久没想起的一件事。", importance=9,
        last_active="2026-01-01T00:00:00",
    )
    _install(monkeypatch, [big, sleepy], {"overflow_lines": False})
    output = await dispatch(max_tokens=300, max_results=1)
    assert "很长的一条 |" not in output
    assert "有 1 条主要浮现记忆因放不下剩余预算而未返回" in output
    # 旧行为：有省略时「久未浮现」整段不跑
    assert "久未浮现" not in output
    # 旧行为：头部没有日期
    small_out = await dispatch(max_tokens=20000, max_results=2)
    assert "[权重:10.00] [bucket_id:big]\n" in small_out


# ---------------------------------------------------------------------------
# 传 query 的检索
# ---------------------------------------------------------------------------

def _hit(bucket_id, content, *, name=None, **extra):
    return _memory(bucket_id, content, name=name, literal_hit=True, semantic_score=None, **extra)


@pytest.mark.asyncio
async def test_search_header_has_created_date(monkeypatch):
    _install(monkeypatch, [_hit("a", "跳舞的一天。")])
    output = await dispatch(query="跳舞", max_results=5, max_tokens=20000)
    assert "[bucket_id:a] 2026-11-20\n" in output


@pytest.mark.asyncio
async def test_search_overflow_becomes_line_and_next_still_full(monkeypatch):
    manager = _install(monkeypatch, [
        _hit("big", _LONG, name="很长的一条"),
        _hit("small", "跳舞的一天。"),
    ])
    output = await dispatch(query="跳舞", max_results=5, max_tokens=300)
    assert _LONG not in output
    assert "=== 放不下全文，只列一行（按桶 ID 搜可看全文） ===" in output
    assert f"很长的一条 | 2026-11-20 | 全文 {_LONG_CHARS} 字 | big" in output
    assert "跳舞的一天。" in output
    # 退成一行的不算想起
    import asyncio
    await asyncio.sleep(0)
    assert manager.touched == ["small"]


@pytest.mark.asyncio
async def test_search_lines_that_do_not_fit_are_counted(monkeypatch):
    _install(monkeypatch, [
        _hit("a", _LONG, name="第一条很长的记忆标题"),
        _hit("b", _LONG, name="第二条很长的记忆标题"),
    ])
    output = await dispatch(query="跳舞", max_results=5, max_tokens=5)
    assert "第一条很长的记忆标题" not in output
    assert "[还有 2 条命中放不下全文，也放不下一行：token 预算不够]" in output


@pytest.mark.asyncio
async def test_search_overflow_lines_come_before_partial_lines(monkeypatch):
    full = _hit("full", _LONG, name="全中但很长", literal_terms=(2, 2), partial_only=False)
    part = _memory(
        "part", "只写了爵士。", name="部分命中", literal_hit=False, semantic_score=None,
        literal_terms=(1, 2), partial_only=True,
    )
    extra = _memory(
        "extra", "也只写了爵士。", name="又一条部分命中", literal_hit=False, semantic_score=None,
        literal_terms=(1, 2), partial_only=True,
    )
    _install(monkeypatch, [full, part, extra])
    output = await dispatch(query="舞蹈 爵士", max_results=2, max_tokens=400)
    assert "全中但很长 | 2026-11-20 | 全文" in output
    assert "[字面 1/2] [bucket_id:part] 2026-11-20" in output
    assert "又一条部分命中 | 字面 1/2 | 2026-11-20 | extra" in output
    assert output.index("全中但很长 |") < output.index("又一条部分命中 |")


@pytest.mark.asyncio
async def test_search_switch_off_restores_old_behavior(monkeypatch):
    _install(monkeypatch, [
        _hit("big", _LONG, name="很长的一条"),
        _hit("small", "跳舞的一天。"),
    ], {"overflow_lines": False})
    output = await dispatch(query="跳舞", max_results=5, max_tokens=300)
    # 旧行为：碰到第一条放不下的就停，后面的都不显示，也没有那一行
    assert "很长的一条 |" not in output
    assert "跳舞的一天。" not in output
    assert "token 预算不足" in output
    no_date = await dispatch(query="跳舞", max_results=5, max_tokens=20000)
    assert "[bucket_id:small]\n" in no_date


@pytest.mark.asyncio
async def test_search_switch_off_keeps_cut9_partial_lines(monkeypatch):
    """关掉 8c 时，第 9 刀「token 放不下的部分命中退成一行」照旧。"""
    full = _hit("full", "两个词都中。", literal_terms=(2, 2), partial_only=False)
    part = _memory(
        "part", _LONG, name="部分命中很长", literal_hit=False, semantic_score=None,
        literal_terms=(1, 2), partial_only=True,
    )
    _install(monkeypatch, [full, part], {"overflow_lines": False})
    output = await dispatch(query="舞蹈 爵士", max_results=5, max_tokens=300)
    assert "[字面 2/2] [bucket_id:full]" in output
    assert "部分命中很长 | 字面 1/2 | 2026-11-20 | part" in output


@pytest.mark.asyncio
async def test_exact_id_header_has_created_date(monkeypatch):
    bucket = _memory("4f3a2b1c0d9e", "按编号读的一条。")
    _install(monkeypatch, [bucket])
    output = await dispatch(query="4f3a2b1c0d9e", max_tokens=20000)
    assert "[exact_bucket_id:true] [bucket_id:4f3a2b1c0d9e] 2026-11-20\n" in output
    assert "按编号读的一条。" in output
