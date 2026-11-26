"""二改复检小修（2026-11-26）。

两件小事，都是修已经上线的代码，不改变写入 / 搜索 / 浮现的行为，所以不带开关，
退路是在 GitHub 上 Revert 那次提交。

1. grow 的汇总：
   - 和库里逐字相同、什么都没写的那条（第 7 刀的「＝已存在」），原来被算进
     「合」，汇总写「新1合1」，可它什么都没合并。现在单独数：「新1合0已存在1」。
   - 原来只显示第一条提示。「已存在」的说明排在前面时，会把另一条的
     「向量没建上」盖掉。现在不同的提示都列出来。
   grow(items=...) 和 grow(content=...) 拆日记那条路（write.use_tagger 打开时才走）
   用的是同一套写法，一起改了。
2. 开关读取 _config_flag()（plan.auto_resolve / merge.auto / write.use_tagger 都用它）：
   认不出的字符串（比如 enable、maybe）原来一律当「关」，现在按这个开关的默认值。
   这三个开关默认都是关，所以线上的结果不变；改的是以后加一个默认开的开关时不会踩坑。
   数字（1、0）照旧按默认值处理——这是原来测试里写明的规则（只认布尔和明确的字符串），
   没有动。
"""

import pytest

from bucket_manager import BucketManager
from tools import _common as common
from tools import _runtime as rt
from tools.grow import core as grow_core


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    error = warning


class _NoModel:
    """写入不调小模型（第 8 刀默认）时用到的那一点点接口。"""

    api_available = True

    def _default_analysis(self):
        return {
            "domain": ["未分类"], "valence": 0.5, "arousal": 0.3, "tags": [],
            "suggested_name": "", "importance": 5, "why_remembered": "",
        }


async def _background(*_args, **_kwargs):
    return None


def _close_task(coro):
    coro.close()
    return None


def _quiet_background(monkeypatch):
    monkeypatch.setattr(grow_core, "check_plan_resolution", _background)
    monkeypatch.setattr(grow_core, "check_duplicate_for", _background)
    monkeypatch.setattr(grow_core.asyncio, "create_task", _close_task)


def _wire_real(monkeypatch, tmp_path):
    manager = BucketManager(
        {"buckets_dir": str(tmp_path / "vault")}, embedding_engine=None
    )
    monkeypatch.setattr(rt, "bucket_mgr", manager, raising=False)
    monkeypatch.setattr(rt, "embedding_engine", None, raising=False)
    monkeypatch.setattr(rt, "dehydrator", _NoModel(), raising=False)
    monkeypatch.setattr(rt, "config", {}, raising=False)
    monkeypatch.setattr(rt, "logger", _Logger(), raising=False)
    _quiet_background(monkeypatch)
    return manager


def _fake_merge_or_create(*, duplicate_prefix: str):
    """正文以 duplicate_prefix 开头的当成逐字重复，其余新建但向量没建上。"""

    async def fake(content, **_kwargs):
        if content.startswith(duplicate_prefix):
            return "old111", True, common._EXACT_DUPLICATE_NOTICE
        return "new222", False, common._EMBED_WARN

    return fake


# ---------------------------------------------------------
# 1. grow 的汇总
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_items_duplicate_is_counted_apart_from_merged(tmp_path, monkeypatch):
    """真实的写入路径：一条逐字重复、一条新的。汇总不说「合1」。"""
    manager = _wire_real(monkeypatch, tmp_path)
    old_text = "2026-11-26 复检：grow 汇总把没写入的那条算成了合并。"
    old_id = await manager.create(content=old_text, title="复检")

    out = await grow_core.grow_items([
        {"content": old_text, "title": "重复的一条"},
        {"content": "2026-11-26 复检：这一条是新的。", "title": "新的一条"},
    ])

    assert "|新1合0已存在1 " in out
    assert f"＝已存在 {old_id}" in out
    assert len(await manager.list_all(include_archive=False)) == 2


@pytest.mark.asyncio
async def test_items_without_duplicates_keep_the_old_summary(tmp_path, monkeypatch):
    """没有重复的时候，汇总和原来一个字不差，不多出「已存在0」。"""
    _wire_real(monkeypatch, tmp_path)

    out = await grow_core.grow_items([
        {"content": "2026-11-26 第一条。", "title": "一"},
        {"content": "2026-11-26 第二条。", "title": "二"},
    ])

    assert "2条(预拆分·逐字)|新2合0 batch:" in out
    assert "已存在" not in out


@pytest.mark.asyncio
async def test_items_duplicate_notice_no_longer_hides_embedding_warning(monkeypatch):
    """重复的那条排在前面时，另一条「向量没建上」的提示原来会被盖掉。"""
    monkeypatch.setattr(rt, "dehydrator", _NoModel(), raising=False)
    monkeypatch.setattr(rt, "config", {}, raising=False)
    monkeypatch.setattr(rt, "logger", _Logger(), raising=False)
    monkeypatch.setattr(
        grow_core, "merge_or_create", _fake_merge_or_create(duplicate_prefix="重复")
    )
    _quiet_background(monkeypatch)

    out = await grow_core.grow_items([
        {"content": "重复的那条正文", "title": "a"},
        {"content": "新的一条正文内容", "title": "b"},
    ])

    assert "|新1合0已存在1 " in out
    assert f"⚠️ {common._EXACT_DUPLICATE_NOTICE}" in out
    assert f"⚠️ {common._EMBED_WARN}" in out


@pytest.mark.asyncio
@pytest.mark.usefixtures("write_tagger_on")
async def test_digest_path_counts_and_warns_the_same_way(monkeypatch):
    """grow(content=长文) 拆日记那条路（write.use_tagger 打开时）同样处理。"""

    class Digest(_NoModel):
        async def digest(self, _content):
            return [
                {"name": "a", "content": "重复的那条正文", "domain": ["x"]},
                {"name": "b", "content": "新的一条正文内容", "domain": ["x"]},
            ]

    monkeypatch.setattr(rt, "dehydrator", Digest(), raising=False)
    monkeypatch.setattr(rt, "config", {}, raising=False)
    monkeypatch.setattr(rt, "logger", _Logger(), raising=False)
    monkeypatch.setattr(
        grow_core, "merge_or_create", _fake_merge_or_create(duplicate_prefix="重复")
    )
    _quiet_background(monkeypatch)

    out = await grow_core.grow_core("这是一段超过三十个字符的日记原文，用来走拆日记的那条路，不走短内容。")

    assert "2条|新1合0已存在1 " in out
    assert f"⚠️ {common._EXACT_DUPLICATE_NOTICE}" in out
    assert f"⚠️ {common._EMBED_WARN}" in out


# ---------------------------------------------------------
# 2. 开关读取
# ---------------------------------------------------------

@pytest.mark.parametrize("value", ["enable", "maybe", "写错了", "", "  "])
@pytest.mark.parametrize("default", [True, False])
def test_unreadable_string_uses_the_default(monkeypatch, value, default):
    monkeypatch.setattr(rt, "config", {"sec": {"key": value}}, raising=False)
    assert common._config_flag("sec", "key", default) is default


@pytest.mark.parametrize("value", [1, 0, 5, 1.0, None, ["true"]])
@pytest.mark.parametrize("default", [True, False])
def test_non_string_values_still_use_the_default(monkeypatch, value, default):
    """数字照旧按默认值处理：原来的测试写明了只认布尔和明确的字符串。"""
    monkeypatch.setattr(rt, "config", {"sec": {"key": value}}, raising=False)
    assert common._config_flag("sec", "key", default) is default


@pytest.mark.parametrize("value", [True, "true", "True", " on ", "yes", "1"])
@pytest.mark.parametrize("default", [True, False])
def test_clear_on_values(monkeypatch, value, default):
    monkeypatch.setattr(rt, "config", {"sec": {"key": value}}, raising=False)
    assert common._config_flag("sec", "key", default) is True


@pytest.mark.parametrize("value", [False, "false", "FALSE", "off", "no", "0"])
@pytest.mark.parametrize("default", [True, False])
def test_clear_off_values(monkeypatch, value, default):
    monkeypatch.setattr(rt, "config", {"sec": {"key": value}}, raising=False)
    assert common._config_flag("sec", "key", default) is False


@pytest.mark.parametrize("config", [None, {}, {"sec": None}, {"sec": "true"}, {"sec": {}}])
@pytest.mark.parametrize("default", [True, False])
def test_missing_section_or_key_uses_the_default(monkeypatch, config, default):
    monkeypatch.setattr(rt, "config", config, raising=False)
    assert common._config_flag("sec", "key", default) is default
