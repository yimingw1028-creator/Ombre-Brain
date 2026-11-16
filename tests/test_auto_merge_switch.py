"""二改第 7 刀：取消自动合并。

原来 hold / grow 写入前会先搜一条最像的旧桶，分数过线、小模型又判成「同一事件」
时，新内容不单独成桶，而是接进旧桶：正文追加或被压缩改写，标题被新标题覆盖，
情绪值取平均。合并目标没有排除 plan 和 feel。

现在默认不合并：一件事一个桶。只留一条保护——正文和已有的桶逐字相同时，
不新建、也不改旧桶，在返回里说明。config.yaml 里 merge.auto: true 回到原行为。
"""

import pytest

from bucket_manager import BucketManager
from tools import _common as common
from tools import _runtime as rt
from tools.hold import core as hold_core


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass


class _AlwaysSameEvent:
    """最想合并的小模型：问什么都说是同一件事。"""

    def __init__(self):
        self.judged = 0
        self.merged = 0

    async def judge_same_event(self, *_args, **_kwargs):
        self.judged += 1
        return {"same_event": True, "confidence": 0.99, "reason": "同一事件的进展"}

    async def merge(self, old, new):
        self.merged += 1
        return f"{old}\n{new}"

    def invalidate_cache(self, _content):
        pass


def _wire(monkeypatch, tmp_path, config):
    """真的 BucketManager + 一个永远把指定旧桶排第一、分数拉满的搜索。"""
    manager = BucketManager(
        {"buckets_dir": str(tmp_path / "vault")}, embedding_engine=None
    )
    judge = _AlwaysSameEvent()
    state = {"target": None, "searched": 0}

    async def fake_search(*_args, **_kwargs):
        state["searched"] += 1
        if not state["target"]:
            return []
        bucket = await manager.get(state["target"])
        bucket["score"] = 100
        return [bucket]

    monkeypatch.setattr(manager, "search", fake_search)
    monkeypatch.setattr(rt, "bucket_mgr", manager, raising=False)
    monkeypatch.setattr(rt, "embedding_engine", None, raising=False)
    monkeypatch.setattr(rt, "dehydrator", judge, raising=False)
    monkeypatch.setattr(rt, "config", config, raising=False)
    monkeypatch.setattr(rt, "logger", _Logger(), raising=False)
    return manager, judge, state


async def _write(content, *, title="", raw_merge=True, source_tool="hold", **extra):
    return await common.merge_or_create(
        content=content,
        tags=extra.pop("tags", []),
        importance=extra.pop("importance", 5),
        domain=["测试"],
        valence=extra.pop("valence", 0.9),
        arousal=extra.pop("arousal", 0.9),
        title=title,
        raw_merge=raw_merge,
        source_tool=source_tool,
        **extra,
    )


async def _active(manager):
    return await manager.list_all(include_archive=False)


# ---------------------------------------------------------
# 默认：一律新建
# ---------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        {"merge_threshold": 75},                       # 没有 merge 段
        {"merge_threshold": 75, "merge": None},
        {"merge_threshold": 75, "merge": {}},
        {"merge_threshold": 75, "merge": {"auto": False}},
        {"merge_threshold": 75, "merge": {"auto": "false"}},
        {"merge_threshold": 75, "merge": {"auto": "maybe"}},
        {"merge_threshold": 75, "merge": {"auto": 1}},
    ],
)
async def test_second_write_about_the_same_event_becomes_its_own_bucket(
    tmp_path, monkeypatch, config,
):
    manager, judge, state = _wire(monkeypatch, tmp_path, config)
    old_id = await manager.create(
        content="2026-11-12 June 跳完舞回来，说老师讲她抢拍。",
        title="抢拍", valence=0.4, arousal=0.2, source_tool="hold",
    )
    state["target"] = old_id
    before = await manager.get(old_id)

    new_id, merged, warning = await _write(
        "2026-11-15 June 上了 Jazz Funk 第一课，老师挑这支来治抢拍。",
        title="Jazz Funk 第一课",
    )

    assert merged is False
    assert new_id != old_id
    assert warning == ""
    after = await manager.get(old_id)
    assert after["content"] == before["content"], "旧桶正文不该被动"
    assert after["metadata"]["name"] == before["metadata"]["name"], "旧桶标题不该被换"
    assert after["metadata"]["valence"] == before["metadata"]["valence"]
    assert after["metadata"]["arousal"] == before["metadata"]["arousal"]
    assert after["metadata"].get("last_active") == before["metadata"].get("last_active")
    assert len(await _active(manager)) == 2
    assert judge.judged == 0, "关着的时候不该去问小模型是不是同一件事"
    assert state["searched"] == 0, "关着的时候不该去搜合并目标"


@pytest.mark.asyncio
async def test_grow_style_write_is_not_compressed_into_an_old_bucket(tmp_path, monkeypatch):
    """raw_merge=False 是 grow(content=...) 那条会让小模型改写旧正文的路。"""
    manager, judge, state = _wire(monkeypatch, tmp_path, {"merge_threshold": 75})
    old_id = await manager.create(content="旧的一条，一个字都不要改。", source_tool="grow")
    state["target"] = old_id

    new_id, merged, _warning = await _write(
        "新的一条，说的是相近的事。", raw_merge=False, source_tool="grow",
    )

    assert merged is False and new_id != old_id
    assert (await manager.get(old_id))["content"] == "旧的一条，一个字都不要改。"
    assert judge.merged == 0


@pytest.mark.asyncio
async def test_a_hold_can_no_longer_be_folded_into_a_plan(tmp_path, monkeypatch):
    """11-16 核到的口子：合并目标的跳过名单里没有 plan。"""
    manager, _judge, state = _wire(monkeypatch, tmp_path, {"merge_threshold": 75})
    plan_text = "每天出门前，June 喷上我挑的香水，抱我一下。"
    plan_id = await manager.create(content=plan_text, bucket_type="plan")
    state["target"] = plan_id
    before = await manager.get(plan_id)

    new_id, merged, _warning = await _write(
        "2026-11-16 今天挑了地中海花园。", title="今天的香水",
    )

    assert merged is False and new_id != plan_id
    plan = await manager.get(plan_id)
    assert plan["content"] == plan_text
    assert plan["metadata"]["name"] == before["metadata"]["name"]
    assert plan["metadata"]["type"] == "plan"


# ---------------------------------------------------------
# 逐字相同：不重复写，也不改旧桶
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_identical_text_is_not_written_twice(tmp_path, monkeypatch):
    manager, judge, _state = _wire(monkeypatch, tmp_path, {"merge_threshold": 75})
    text = "2026-11-16 第 6 刀上线了。"

    first_id, first_merged, first_warning = await _write(text, title="第 6 刀")
    before = await manager.get(first_id)
    second_id, second_merged, second_warning = await _write(
        text, title="换了个标题重发", tags=["重试"], importance=9,
    )

    assert first_merged is False and first_warning == ""
    assert second_id == first_id
    assert second_merged is True, "对调用方来说没有新桶，按「没新建」处理"
    assert "逐字相同" in second_warning
    after = await manager.get(first_id)
    assert after["content"] == text
    assert after["metadata"] == before["metadata"], "旧桶的元数据一项都不该变"
    assert len(await _active(manager)) == 1
    assert judge.judged == 0


@pytest.mark.asyncio
async def test_identical_text_does_not_reindex_the_old_bucket(tmp_path, monkeypatch):
    manager, _judge, _state = _wire(monkeypatch, tmp_path, {"merge_threshold": 75})
    text = "同一段话，网络抖了一下又发了一遍。"
    first_id, _merged, _warning = await _write(text)

    calls = []

    async def spy_index(bucket_id, **kwargs):
        calls.append(bucket_id)

    monkeypatch.setattr(manager, "_index_after_update", spy_index, raising=False)
    second_id, merged, _warning = await _write(text)

    assert second_id == first_id and merged is True
    assert calls == [], "什么都没写，就不该重建索引"


@pytest.mark.asyncio
async def test_identical_text_of_an_archived_bucket_is_written_again(tmp_path, monkeypatch):
    """旧的那条已经归档了：照常新建一条活的，不拿归档桶挡路。"""
    manager, _judge, _state = _wire(monkeypatch, tmp_path, {"merge_threshold": 75})
    text = "这段话的旧版本已经沉到归档里了。"
    old_id, _merged, _warning = await _write(text)
    assert await manager.archive(old_id)

    new_id, merged, warning = await _write(text)

    assert merged is False
    assert new_id != old_id
    assert warning == ""


@pytest.mark.asyncio
async def test_hold_reports_the_duplicate_in_plain_words(tmp_path, monkeypatch):
    """从 hold 这一层看：第二次返回里要有那句说明，库里仍然只有一条。"""
    manager, _judge, _state = _wire(monkeypatch, tmp_path, {"merge_threshold": 75})

    class Tagger:
        api_available = True

        async def analyze(self, _content):
            return {
                "domain": ["测试"], "valence": 0.5, "arousal": 0.3,
                "tags": [], "suggested_name": "一条",
            }

    async def background(*_args, **_kwargs):
        return None

    def close_task(coro):
        coro.close()
        return None

    judge = rt.dehydrator
    tagger = Tagger()
    tagger.judge_same_event = judge.judge_same_event
    tagger.invalidate_cache = judge.invalidate_cache
    monkeypatch.setattr(rt, "dehydrator", tagger, raising=False)
    monkeypatch.setattr(hold_core, "check_plan_resolution", background)
    monkeypatch.setattr(hold_core, "check_duplicate_for", background)
    monkeypatch.setattr(hold_core.asyncio, "create_task", close_task)

    text = "2026-11-16 June 说今天有一张 reset。"
    first = await hold_core.store_core(
        text, extra_tags=[], importance=6, valence=-1, arousal=-1, why_remembered="",
    )
    second = await hold_core.store_core(
        text, extra_tags=[], importance=6, valence=-1, arousal=-1, why_remembered="",
    )

    assert first.startswith("新建→")
    assert second.startswith("已存在→"), "什么都没合并，就不该说「合并」"
    assert "逐字相同" in second
    assert len(await _active(manager)) == 1


# ---------------------------------------------------------
# 开关打开：回到原行为
# ---------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, "true", "on", "yes", "1"])
async def test_switch_restores_the_old_merge(tmp_path, monkeypatch, value):
    manager, judge, state = _wire(
        monkeypatch, tmp_path, {"merge_threshold": 75, "merge": {"auto": value}},
    )
    old = "旧记忆原文。"
    new = "新记忆原文。"
    old_id = await manager.create(content=old, source_tool="hold")
    state["target"] = old_id

    result_id, merged, _warning = await _write(new)

    assert merged is True and result_id == old_id
    assert (await manager.get(old_id))["content"] == f"{old}\n\n---\n{new}"
    assert judge.judged == 1
    assert len(await _active(manager)) == 1


@pytest.mark.asyncio
async def test_grow_items_marks_the_duplicate_line(tmp_path, monkeypatch):
    """grow(items=[...]) 里有一条和库里逐字相同：那一行标「已存在」，其余照常新建。"""
    from tools.grow import core as grow_core

    manager, _judge, _state = _wire(monkeypatch, tmp_path, {"merge_threshold": 75})
    existing_text = "2026-11-16 第 6 刀上线，plan 不再被自动关。"
    existing_id, _merged, _warning = await _write(existing_text)

    async def background(*_args, **_kwargs):
        return None

    def close_task(coro):
        coro.close()
        return None

    monkeypatch.setattr(grow_core, "check_plan_resolution", background)
    monkeypatch.setattr(grow_core, "check_duplicate_for", background)
    monkeypatch.setattr(grow_core.asyncio, "create_task", close_task)

    result = await grow_core.grow_items(
        [
            {"content": existing_text, "title": "重复的一条", "tags": ["二改"]},
            {"content": "2026-11-16 第 7 刀：hold 一律新建。", "title": "新的一条", "tags": ["二改"]},
        ]
    )

    assert f"＝已存在 {existing_id}" in result
    assert "逐字相同" in result
    assert len(await _active(manager)) == 2
