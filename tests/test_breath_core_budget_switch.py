"""二改第 8b 刀：breath 浮现预算 10000 → 20000，核心准则单独算预算。

原来核心准则（pinned）和浮现记忆共用一个 max_tokens（默认 10000）。准则先装，
装完剩下的才给浮现记忆。2026-11-18 线上实测：准则 16 条约三千五，浮现 20 个
候选里有 8 个放不下。

现在：
- 准则用 surfacing.breath_core_max_tokens（默认 6000），不再占浮现的预算；
  放不下全文的准则退成一行开头（照计划段的做法），不截断正文。
- 设成 0 回到旧行为：共用 max_tokens，放不下的计入「主要浮现记忆」省略数。
- breath_max_tokens 没配置时的默认值从 10000 改成 20000。
"""

from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from tools.breath import dispatch
from tools.breath._verbatim import render_stored_bucket


class _NoopDecay:
    is_running = True

    async def ensure_started(self):
        return None

    def calculate_score(self, meta):
        return float(meta.get("importance") or 5)


class _DisabledEmbedding:
    enabled = False


class _Manager:
    def __init__(self, buckets):
        self.buckets = list(buckets)

    async def list_all(self, include_archive=False):
        return list(self.buckets)

    async def search(self, query, **kwargs):
        return list(self.buckets)

    async def touch_many(self, bucket_ids, ripple=False):
        return None

    async def get_stats(self):
        return {"permanent_count": 0, "dynamic_count": len(self.buckets)}


def _install(buckets, surfacing=None):
    rt.config = {"surfacing": dict(surfacing or {})}
    rt.bucket_mgr = _Manager(buckets)
    rt.decay_engine = _NoopDecay()
    rt.dehydrator = MagicMock()
    rt.embedding_engine = _DisabledEmbedding()
    rt.logger = MagicMock()
    rt.fire_webhook = None
    rt.mark_op = None
    rt.record_v3_tool_event = lambda *_args, **_kwargs: None


def _pinned(bucket_id, content):
    return {
        "id": bucket_id,
        "content": content,
        "metadata": {"type": "dynamic", "pinned": True, "importance": 10, "domain": []},
    }


def _memory(bucket_id, content, importance=9):
    return {
        "id": bucket_id,
        "content": content,
        "metadata": {
            "type": "dynamic",
            "importance": importance,
            "activation_count": 1,
            "domain": [],
        },
    }


def _cost(bucket, header):
    _, tokens = render_stored_bucket(bucket, header, "👣 Footprint：暂时无法读取")
    return tokens


@pytest.fixture(autouse=True)
def _no_randomness(monkeypatch):
    monkeypatch.setattr("tools.breath.surface.random.shuffle", lambda items: None)
    monkeypatch.setattr("tools.breath.surface.random.random", lambda: 1.0)


@pytest.mark.asyncio
async def test_default_core_does_not_eat_memory_budget():
    core = _pinned("core", "核心准则正文" * 200)
    mem = _memory("mem", "今天的一件事" * 50)
    _install([core, mem])
    mem_cost = _cost(mem, "[权重:9.00] [bucket_id:mem]")

    output = await dispatch(max_tokens=mem_cost)

    assert "=== 核心准则 ===" in output
    assert core["content"] in output
    assert "[bucket_id:mem]" in output
    assert mem["content"] in output
    assert "token 预算不足" not in output


@pytest.mark.asyncio
async def test_switch_zero_restores_shared_budget():
    core = _pinned("core", "核心准则正文" * 20)
    mem = _memory("mem", "今天的一件事" * 50)
    _install([core, mem], {"breath_core_max_tokens": 0, "overflow_lines": False})
    core_cost = _cost(core, "📌 [核心准则] [bucket_id:core]")
    mem_cost = _cost(mem, "[权重:9.00] [bucket_id:mem]")

    # 准则先装进共用预算，剩下的不够放浮现记忆
    output = await dispatch(max_tokens=core_cost + mem_cost - 1)

    assert core["content"] in output
    assert "[bucket_id:mem]" not in output
    assert "token 预算不足" in output


@pytest.mark.asyncio
async def test_core_over_own_budget_falls_back_to_one_line_without_truncation():
    short = _pinned("short", "短准则。")
    long_body = "第一行是开头\n" + "后面很长的准则正文" * 300
    long = _pinned("long", long_body)
    mem = _memory("mem", "今天的一件事")
    short_cost = _cost(short, "📌 [核心准则] [bucket_id:short]")
    _install([short, long, mem], {"breath_core_max_tokens": short_cost})

    output = await dispatch(max_tokens=20000)

    assert short["content"] in output
    assert "（核心准则预算放不下全文，只列开头：）" in output
    assert "📌 [bucket_id:long] 第一行是开头" in output
    assert "后面很长的准则正文" not in output
    assert "[bucket_id:mem]" in output
    # 退成一行的准则不算进浮现记忆的省略数
    assert "token 预算不足" not in output


@pytest.mark.asyncio
async def test_bad_core_budget_value_falls_back_to_default():
    core = _pinned("core", "核心准则正文" * 200)
    mem = _memory("mem", "今天的一件事" * 50)
    _install([core, mem], {"breath_core_max_tokens": "不是数字"})
    mem_cost = _cost(mem, "[权重:9.00] [bucket_id:mem]")

    output = await dispatch(max_tokens=mem_cost)

    assert core["content"] in output
    assert "[bucket_id:mem]" in output
    assert "token 预算不足" not in output


@pytest.mark.asyncio
async def test_memory_default_budget_is_20000():
    # 每条约 1500 token，十条约一万五：旧默认 10000 装不下，新默认 20000 装得下
    buckets = [_memory(f"m{i}", "很长的一天" * 200, importance=9) for i in range(10)]
    _install(buckets)

    output = await dispatch()

    for i in range(10):
        assert f"[bucket_id:m{i}]" in output
    assert "token 预算不足" not in output


@pytest.mark.asyncio
async def test_configured_memory_budget_still_wins():
    buckets = [_memory(f"m{i}", "很长的一天" * 200, importance=9) for i in range(10)]
    _install(buckets, {"breath_max_tokens": 10000, "overflow_lines": False})

    output = await dispatch()

    assert "token 预算不足" in output
    assert "/10000 token" in output
