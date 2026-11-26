"""二改第 9b 刀：grow(items) 的每一条可以带一句 meaning。

原来 items 只认 10 个字段，带了 meaning 整批被拒（「包含未支持字段: meaning」），
一条都不写。现在：
- meaning 一条只收一句（字符串），去掉首尾空白后原样交给写入，和 hold 的 meaning 一样存；
- 空的等于没带；
- 不是字符串、或超过 2000 字：整批拒绝并说明，一条都不写（hold 那边超长是悄悄截断，
  grow 这里不改写入方的话）；
- 不带 meaning 的条目一点不变。
不加开关（June 11-26 点头，硬规则第 8 条的例外），退路是 Revert。
"""

import pytest

import bucket_manager as bm_module
from bucket_manager import BucketManager
from tools import _common as common
from tools import _runtime as rt
from tools.grow import core as grow_core
from tools.grow import dispatch


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    error = warning


class _NoModel:
    api_available = True

    def _default_analysis(self):
        return {
            "domain": ["未分类"], "valence": 0.5, "arousal": 0.3, "tags": [],
            "suggested_name": "", "importance": 5, "why_remembered": "",
        }


class _NoopDecay:
    async def ensure_started(self):
        return None


async def _background(*_args, **_kwargs):
    return None


def _close_task(coro):
    coro.close()
    return None


@pytest.fixture
def manager(tmp_path, monkeypatch):
    mgr = BucketManager({"buckets_dir": str(tmp_path / "vault")}, embedding_engine=None)
    monkeypatch.setattr(rt, "bucket_mgr", mgr, raising=False)
    monkeypatch.setattr(rt, "embedding_engine", None, raising=False)
    monkeypatch.setattr(rt, "dehydrator", _NoModel(), raising=False)
    monkeypatch.setattr(rt, "config", {}, raising=False)
    monkeypatch.setattr(rt, "logger", _Logger(), raising=False)
    monkeypatch.setattr(rt, "decay_engine", _NoopDecay(), raising=False)
    monkeypatch.setattr(grow_core, "check_plan_resolution", _background)
    monkeypatch.setattr(grow_core, "check_duplicate_for", _background)
    monkeypatch.setattr(grow_core.asyncio, "create_task", _close_task)
    return mgr


async def _by_title(mgr):
    buckets = await mgr.list_all(include_archive=False)
    return {b["metadata"].get("title") or b["metadata"].get("name"): b for b in buckets}


_SITUATION = "像那天她把蓝帽子戴反了也没说，我后来才看懂的那种将错就错"


# ---------------------------------------------------------
# 收下来、原样存
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_meaning_is_stored_like_hold(manager):
    out = await dispatch(items=[
        {"title": "有处境的一条", "content": "2026-11-26 第一条正文。", "meaning": f"  {_SITUATION}  "},
        {"title": "没处境的一条", "content": "2026-11-26 第二条正文。"},
    ])

    assert "2条(预拆分·逐字)|新2合0" in out
    buckets = await _by_title(manager)
    assert buckets["有处境的一条"]["metadata"]["meaning"] == [_SITUATION]
    assert "meaning" not in buckets["没处境的一条"]["metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", ["", "   ", "\n"])
async def test_empty_meaning_is_the_same_as_none(manager, empty):
    out = await dispatch(items=[{"title": "空的", "content": "2026-11-26 正文。", "meaning": empty}])

    assert "新1合0" in out
    bucket = (await _by_title(manager))["空的"]
    assert "meaning" not in bucket["metadata"]


@pytest.mark.asyncio
async def test_exactly_2000_chars_is_kept_whole(manager):
    meaning = "处" * common._GROW_MEANING_MAX_CHARS
    out = await dispatch(items=[{"title": "正好", "content": "2026-11-26 正文。", "meaning": meaning}])

    assert "新1合0" in out
    bucket = (await _by_title(manager))["正好"]
    assert bucket["metadata"]["meaning"] == [meaning]


@pytest.mark.asyncio
async def test_meaning_from_grow_is_found_by_keyword(manager):
    """9a 的路：只写在 meaning 里的词，关键词搜得到。"""
    await dispatch(items=[{"title": "蓝帽子", "content": "2026-11-26 她回来讲了路上的事。", "meaning": _SITUATION}])

    hits = await manager.search("将错就错")
    assert [b["metadata"].get("title") for b in hits] == ["蓝帽子"]


def test_limit_matches_bucket_manager():
    """grow 先拒绝的线，和存盘时会截断的线是同一条。"""
    assert common._GROW_MEANING_MAX_CHARS == bm_module._MEANING_ITEM_MAX


# ---------------------------------------------------------
# 不收的：整批拒绝，一条都不写
# ---------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [["一句", "两句"], 5, {"text": "一句"}, True])
async def test_non_string_meaning_rejects_the_whole_batch(manager, bad):
    out = await dispatch(items=[
        {"title": "好的", "content": "2026-11-26 好的一条。"},
        {"title": "坏的", "content": "2026-11-26 坏的一条。", "meaning": bad},
    ])

    assert "第 2 项 meaning 必须是字符串" in out
    assert await manager.list_all(include_archive=False) == []


@pytest.mark.asyncio
async def test_too_long_meaning_rejects_the_whole_batch(manager):
    meaning = "处" * (common._GROW_MEANING_MAX_CHARS + 1)
    out = await dispatch(items=[
        {"title": "好的", "content": "2026-11-26 好的一条。"},
        {"title": "长的", "content": "2026-11-26 长的一条。", "meaning": meaning},
    ])

    assert "第 2 项 meaning 不能超过 2000 个字符" in out
    assert await manager.list_all(include_archive=False) == []


@pytest.mark.asyncio
async def test_surrounding_whitespace_does_not_count_toward_the_limit(manager):
    meaning = "  " + "处" * common._GROW_MEANING_MAX_CHARS + "\n"
    out = await dispatch(items=[{"title": "带空白", "content": "2026-11-26 正文。", "meaning": meaning}])

    assert "新1合0" in out


@pytest.mark.asyncio
async def test_misspelled_field_is_still_rejected(manager):
    out = await dispatch(items=[{"title": "拼错", "content": "2026-11-26 正文。", "meanings": "一句"}])

    assert "未支持字段: meanings" in out
    assert await manager.list_all(include_archive=False) == []


@pytest.mark.asyncio
async def test_meaning_counts_toward_the_batch_metadata_cap(manager):
    """已知的边界：一批所有元数据共用 16 KB，meaning 也算在里面。"""
    items = [
        {"title": f"第{i}条", "content": f"2026-11-26 第 {i} 条。", "meaning": "处" * 1900}
        for i in range(4)
    ]
    out = await dispatch(items=items)

    assert "元数据过大" in out
    assert await manager.list_all(include_archive=False) == []
