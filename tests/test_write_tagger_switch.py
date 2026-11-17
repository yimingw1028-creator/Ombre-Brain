"""二改第 8 刀：写入不再调小模型。

原来 hold / grow 写入时会把正文发给小模型（dehydrator.analyze）要主题域、
valence、arousal、标签和建议标题；grow(content=长文) 还让它（digest）把长文拆成
2~6 条并改写。

现在默认不调：写入的一方自己带的字段照常生效，没带的用本地中性值；
grow 不带 items 的长文不写入，只返回一句提示。config.yaml 里
write.use_tagger: true 回到原行为。
"""

import asyncio

import pytest

from bucket_manager import BucketManager
from tools import _common as common
from tools import _runtime as rt
from tools.grow import core as grow_core_mod
from tools.grow import dispatch as grow_dispatch
from tools.grow import shortpath as grow_shortpath_mod
from tools.hold import core as hold_core
from tools.hold import pinned as hold_pinned

_WARNING = "打标 API 暂不可用"
_LONG = "2026-11-17 上午和 June 一起读二改记录，定了第 8 刀的四件事，然后开始跑基线。"


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


class _NoopDecay:
    async def ensure_started(self):
        pass


class _SpyTagger:
    """记下自己被叫了几次的小模型。给的值故意和本地中性值不一样。"""

    api_available = True

    def __init__(self, fail=False):
        self.fail = fail
        self.analyze_calls = 0
        self.digest_calls = 0

    async def analyze(self, _content, **_kwargs):
        self.analyze_calls += 1
        if self.fail:
            raise RuntimeError("tagger down")
        return {
            "domain": ["模型分的域"],
            "valence": 0.9,
            "arousal": 0.8,
            "tags": ["模型标签"],
            "suggested_name": "模型起的标题",
            "importance": 8,
            "why_remembered": "",
        }

    async def digest(self, content):
        self.digest_calls += 1
        half = len(content) // 2
        return [
            {"name": "前半", "content": content[:half], "domain": ["模型分的域"]},
            {"name": "后半", "content": content[half:], "domain": ["模型分的域"]},
        ]

    def _default_analysis(self):
        return {
            "domain": ["未分类"],
            "valence": 0.5,
            "arousal": 0.3,
            "tags": [],
            "suggested_name": "",
            "importance": 5,
            "why_remembered": "",
        }

    def invalidate_cache(self, _content):
        pass


def _wire(monkeypatch, tmp_path, config=None, fail=False):
    manager = BucketManager(
        {"buckets_dir": str(tmp_path / "vault")}, embedding_engine=None
    )
    tagger = _SpyTagger(fail=fail)
    monkeypatch.setattr(rt, "bucket_mgr", manager, raising=False)
    monkeypatch.setattr(rt, "embedding_engine", None, raising=False)
    monkeypatch.setattr(rt, "dehydrator", tagger, raising=False)
    monkeypatch.setattr(rt, "decay_engine", _NoopDecay(), raising=False)
    monkeypatch.setattr(rt, "config", config if config is not None else {}, raising=False)
    monkeypatch.setattr(rt, "logger", _Logger(), raising=False)
    monkeypatch.setattr(rt, "fire_webhook", None, raising=False)
    return manager, tagger


async def _settle():
    """让 hold / grow 末尾丢出去的后台任务跑完。"""
    await asyncio.sleep(0.05)


async def _active(manager):
    return await manager.list_all(include_archive=False)


async def _hold(content, *, tags=None, importance=5, valence=-1, arousal=-1, title=""):
    out = await hold_core.store_core(
        content, tags or [], importance, valence, arousal, "", title=title,
    )
    await _settle()
    return out


_ON = {"write": {"use_tagger": True}}


# ---------------------------------------------------------
# 开关本身：读不到、写错了都按默认（关）
# ---------------------------------------------------------

@pytest.mark.parametrize(
    "config",
    [
        {},
        {"write": None},
        {"write": {}},
        {"write": "true"},
        {"write": {"use_tagger": False}},
        {"write": {"use_tagger": "false"}},
        {"write": {"use_tagger": "maybe"}},
        {"write": {"use_tagger": 1}},
    ],
)
def test_switch_is_off_unless_clearly_turned_on(monkeypatch, config):
    monkeypatch.setattr(rt, "config", config, raising=False)
    assert common.write_tagger_enabled() is False


@pytest.mark.parametrize("value", [True, "true", "on", "yes", "1"])
def test_switch_turns_on_with_true_like_values(monkeypatch, value):
    monkeypatch.setattr(rt, "config", {"write": {"use_tagger": value}}, raising=False)
    assert common.write_tagger_enabled() is True


# ---------------------------------------------------------
# 默认：hold 不调小模型
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_hold_does_not_call_the_tagger(tmp_path, monkeypatch):
    manager, tagger = _wire(monkeypatch, tmp_path)

    out = await _hold(_LONG)

    assert tagger.analyze_calls == 0
    assert out.startswith("新建→")
    assert _WARNING not in out          # 开关关着不是故障
    (bucket,) = await _active(manager)
    assert bucket["content"] == _LONG   # 正文一字不动
    meta = bucket["metadata"]
    assert meta["domain"] == ["未分类"]
    assert meta["valence"] == 0.5
    assert meta["arousal"] == 0.3
    assert meta["tags"] == []
    assert not meta.get("title")        # 没带标题就留空，不替我起


@pytest.mark.asyncio
async def test_hold_keeps_every_field_i_bring(tmp_path, monkeypatch):
    manager, tagger = _wire(monkeypatch, tmp_path)

    out = await _hold(
        _LONG, tags=["OB二改", "日记"], importance=7,
        valence=0.8, arousal=0.6, title="第 8 刀开工",
    )

    assert tagger.analyze_calls == 0
    assert _WARNING not in out
    (bucket,) = await _active(manager)
    meta = bucket["metadata"]
    assert meta["tags"] == ["OB二改", "日记"]
    assert meta["importance"] == 7
    assert meta["valence"] == 0.8
    assert meta["arousal"] == 0.6
    assert meta["title"] == "第 8 刀开工"
    assert meta["domain"] == ["未分类"]


@pytest.mark.asyncio
async def test_pinned_hold_does_not_call_the_tagger(tmp_path, monkeypatch):
    manager, tagger = _wire(monkeypatch, tmp_path)

    out = await hold_pinned.store_pinned(
        "一条核心准则的正文。", ["准则"], -1, -1, "", title="准则标题",
    )

    assert tagger.analyze_calls == 0
    assert out.startswith("📌钉选→")
    (bucket,) = await _active(manager)
    meta = bucket["metadata"]
    assert bucket["content"] == "一条核心准则的正文。"
    assert meta["pinned"] is True
    assert meta["importance"] == 10
    assert meta["tags"] == ["准则"]
    assert meta["title"] == "准则标题"
    assert meta["domain"] == ["未分类"]


# ---------------------------------------------------------
# 默认：grow 的三条路
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_grow_items_never_calls_the_tagger_whatever_is_missing(tmp_path, monkeypatch):
    """一条什么都没带，一条只缺 domain（我们平时的写法），一条全带了。"""
    manager, tagger = _wire(monkeypatch, tmp_path)
    bare = "2026-11-17 第一条：只有正文，什么字段都没带。"
    usual = "2026-11-17 第二条：带了标题、标签、重要度和理由，没带主题域。"
    full = "2026-11-17 第三条：所有字段都带了。"

    out = await grow_core_mod.grow_items([
        bare,
        {"content": usual, "title": "平时的写法", "tags": ["日记"],
         "importance": 7, "why_remembered": "这是我们平时走的路"},
        {"content": full, "title": "全带", "tags": ["全"], "importance": 6,
         "domain": ["自己分的域"], "valence": 0.7, "arousal": 0.4},
    ])
    await _settle()

    assert tagger.analyze_calls == 0
    assert tagger.digest_calls == 0
    assert "3条(预拆分·逐字)|新3合0" in out
    assert _WARNING not in out
    by_content = {b["content"]: b["metadata"] for b in await _active(manager)}
    assert set(by_content) == {bare, usual, full}

    assert by_content[bare]["domain"] == ["未分类"]
    assert by_content[bare]["importance"] == 5
    assert by_content[bare]["valence"] == 0.5
    assert by_content[bare]["arousal"] == 0.3
    assert by_content[bare]["tags"] == []
    assert not by_content[bare].get("title")

    assert by_content[usual]["title"] == "平时的写法"
    assert by_content[usual]["tags"] == ["日记"]
    assert by_content[usual]["importance"] == 7
    assert by_content[usual]["why_remembered"] == "这是我们平时走的路"
    assert by_content[usual]["domain"] == ["未分类"]

    assert by_content[full]["domain"] == ["自己分的域"]
    assert by_content[full]["valence"] == 0.7
    assert by_content[full]["arousal"] == 0.4


@pytest.mark.asyncio
async def test_grow_long_content_without_items_writes_nothing(tmp_path, monkeypatch):
    manager, tagger = _wire(monkeypatch, tmp_path)

    out = await grow_dispatch(_LONG)
    await _settle()

    assert tagger.digest_calls == 0
    assert tagger.analyze_calls == 0
    assert out == common._GROW_NEEDS_ITEMS_NOTICE
    assert "没有写入" in out and "items" in out
    assert await _active(manager) == []


@pytest.mark.asyncio
async def test_grow_short_content_is_still_saved_as_one_bucket(tmp_path, monkeypatch):
    manager, tagger = _wire(monkeypatch, tmp_path)

    out = await grow_dispatch("  今天挑了地中海花园。  ")
    await _settle()

    assert tagger.analyze_calls == 0
    assert "新建 →" in out
    assert "未分类" in out
    (bucket,) = await _active(manager)
    assert bucket["content"] == "今天挑了地中海花园。"
    assert bucket["metadata"]["domain"] == ["未分类"]
    assert bucket["metadata"]["importance"] == 5


@pytest.mark.asyncio
async def test_grow_with_items_and_shared_content_still_goes_the_items_way(tmp_path, monkeypatch):
    """同时传 content 和 items：content 是原文证据，不是要拆的长文，不该被拒绝。"""
    from ombrebrain.storage.source_store import SourceStore

    manager, tagger = _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(rt, "source_store", SourceStore(manager.base_dir), raising=False)

    out = await grow_dispatch(_LONG, items=[{"content": _LONG, "title": "整段一条"}])
    await _settle()

    assert tagger.digest_calls == 0 and tagger.analyze_calls == 0
    assert "1条(预拆分·逐字)|新1合0" in out
    assert len(await _active(manager)) == 1


# ---------------------------------------------------------
# 开关打开：回到原行为
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_switch_on_hold_asks_the_tagger_again(tmp_path, monkeypatch):
    manager, tagger = _wire(monkeypatch, tmp_path, config=dict(_ON))

    await _hold(_LONG)

    assert tagger.analyze_calls == 1
    (bucket,) = await _active(manager)
    meta = bucket["metadata"]
    assert meta["domain"] == ["模型分的域"]
    assert meta["valence"] == 0.9
    assert meta["arousal"] == 0.8
    assert meta["tags"] == ["模型标签"]
    assert meta["title"] == "模型起的标题"


@pytest.mark.asyncio
async def test_switch_on_tagger_failure_still_degrades_with_the_warning(tmp_path, monkeypatch):
    manager, tagger = _wire(monkeypatch, tmp_path, config=dict(_ON), fail=True)

    out = await _hold(_LONG)

    assert tagger.analyze_calls == 1
    assert _WARNING in out              # 这才是真的故障，那句话该在
    (bucket,) = await _active(manager)
    assert bucket["content"] == _LONG
    assert bucket["metadata"]["domain"] == ["未分类"]


@pytest.mark.asyncio
async def test_switch_on_pinned_and_shortpath_ask_the_tagger_again(tmp_path, monkeypatch):
    manager, tagger = _wire(monkeypatch, tmp_path, config=dict(_ON))

    await hold_pinned.store_pinned("一条核心准则的正文。", [], -1, -1, "")
    assert tagger.analyze_calls == 1

    await grow_shortpath_mod.grow_shortpath("今天挑了地中海花园。")
    await _settle()
    assert tagger.analyze_calls == 2
    domains = sorted(b["metadata"]["domain"][0] for b in await _active(manager))
    assert domains == ["模型分的域", "模型分的域"]


@pytest.mark.asyncio
async def test_switch_on_grow_items_fills_missing_fields_from_the_tagger(tmp_path, monkeypatch):
    manager, tagger = _wire(monkeypatch, tmp_path, config=dict(_ON))

    await grow_core_mod.grow_items(["2026-11-17 只有正文的一条，字段让模型补。"])
    await _settle()

    assert tagger.analyze_calls == 1
    (bucket,) = await _active(manager)
    assert bucket["metadata"]["domain"] == ["模型分的域"]
    assert bucket["metadata"]["importance"] == 8


@pytest.mark.asyncio
async def test_switch_on_grow_long_content_is_digested_again(tmp_path, monkeypatch):
    manager, tagger = _wire(monkeypatch, tmp_path, config=dict(_ON))

    out = await grow_dispatch(_LONG)
    await _settle()

    assert tagger.digest_calls == 1
    assert out != common._GROW_NEEDS_ITEMS_NOTICE
    assert len(await _active(manager)) == 2


# ---------------------------------------------------------
# 这一刀声称不动的东西
# ---------------------------------------------------------

class _CountingEmbedding:
    """向量检索是另一个接口、另一个 key：开关关着，写入照样要去生成向量。"""

    enabled = True

    def __init__(self):
        self.stored = []

    async def generate_and_store(self, bucket_id, content):
        self.stored.append((bucket_id, content))
        return True

    def delete_embedding(self, _bucket_id):
        pass

    async def search_similar(self, *_args, **_kwargs):
        return []

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


@pytest.mark.asyncio
async def test_embedding_is_still_generated_when_the_tagger_is_off(tmp_path, monkeypatch):
    engine = _CountingEmbedding()
    manager = BucketManager(
        {"buckets_dir": str(tmp_path / "vault")}, embedding_engine=engine
    )
    tagger = _SpyTagger()
    monkeypatch.setattr(rt, "bucket_mgr", manager, raising=False)
    monkeypatch.setattr(rt, "embedding_engine", engine, raising=False)
    monkeypatch.setattr(rt, "dehydrator", tagger, raising=False)
    monkeypatch.setattr(rt, "config", {}, raising=False)
    monkeypatch.setattr(rt, "logger", _Logger(), raising=False)

    async def _no_duplicate_scan(*_args, **_kwargs):
        return None

    monkeypatch.setattr(hold_core, "check_duplicate_for", _no_duplicate_scan)

    out = await _hold(_LONG, title="向量还在")

    assert tagger.analyze_calls == 0
    assert out.startswith("新建→")
    (bucket,) = await _active(manager)
    assert [content for _id, content in engine.stored] == [_LONG]
    assert engine.stored[0][0] == bucket["id"]


def test_the_tagger_code_is_still_there():
    """这一刀不删旧代码：dehydrator 的方法都在，开关打开就能用。"""
    import dehydrator

    for name in ("analyze", "digest", "_default_analysis"):
        assert callable(getattr(dehydrator.Dehydrator, name))
