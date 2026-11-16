"""二改第 6 刀：关掉 plan 自动结案。

原来每次 hold / grow 之后，active plan 会被送给小模型判「是否已完成」，判中就
直接改成 resolved，不写 change_log，也不通知写入的一方。plan 是承诺，漏关一条
没什么，错关一条才麻烦，所以默认不再自动结案，只由 trace(status=...) 或后台
看板显式关闭。

同一刀里：status 被设成 active 时，清掉上一次结案留下的 resolved_by /
resolution_reason，免得下次显式结案顺着旧值把一条无关的记忆连带标成 resolved。
"""

import frontmatter as fm
import pytest

from tools import _common as common
from tools import _runtime as rt


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass


class _SpyManager:
    """记录 check_plan_resolution 碰过它的每一次调用。"""

    def __init__(self):
        self.calls = []
        self.updated = []
        self.plan = {
            "id": "plan-1",
            "content": "周日完成模板发布",
            "metadata": {"type": "plan", "status": "active"},
        }

    async def list_all(self, include_archive=False):
        self.calls.append("list_all")
        return [self.plan]

    async def search(self, query, limit=None, vector_scores=None):
        self.calls.append("search")
        return [self.plan]

    async def update(self, bucket_id, **changes):
        self.calls.append("update")
        self.updated.append((bucket_id, changes))
        return True


class _EagerJudge:
    """不管给什么都说「已完成」的小模型替身。"""

    def __init__(self):
        self.asked = 0

    async def judge_plan_resolution(self, plan_text, new_event_text):
        self.asked += 1
        return {"resolved": True, "confidence": 0.99, "reason": "看起来做完了"}


def _wire(monkeypatch, config):
    manager = _SpyManager()
    judge = _EagerJudge()
    monkeypatch.setattr(rt, "bucket_mgr", manager, raising=False)
    monkeypatch.setattr(rt, "embedding_engine", None, raising=False)
    monkeypatch.setattr(rt, "dehydrator", judge, raising=False)
    monkeypatch.setattr(rt, "logger", _Logger(), raising=False)
    monkeypatch.setattr(rt, "config", config, raising=False)
    return manager, judge


# ---------------------------------------------------------
# 开关
# ---------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        None,                                   # 没有 config
        {},                                     # 没有 plan 段
        {"plan": None},                         # plan 段是空的
        {"plan": {}},                           # 没写 auto_resolve
        {"plan": {"auto_resolve": False}},
        {"plan": {"auto_resolve": "false"}},
        {"plan": {"auto_resolve": "maybe"}},    # 写错了按关处理
        {"plan": {"auto_resolve": 1}},          # 只认布尔和明确的字符串
        {"plan": "true"},                       # 段本身写成了字符串
    ],
)
async def test_auto_resolve_is_off_by_default(monkeypatch, config):
    manager, judge = _wire(monkeypatch, config)

    await common.check_plan_resolution(
        "模板已经发布完成，这件事做完了", source_bucket_id="event-1"
    )

    assert manager.updated == [], "默认不该自动结案"
    assert judge.asked == 0, "关着的时候不该去问小模型"
    assert manager.calls == [], "关着的时候连桶都不该列"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, "true", "True", "on", "yes", "1"])
async def test_auto_resolve_can_be_switched_back_on(monkeypatch, value):
    manager, judge = _wire(monkeypatch, {"plan": {"auto_resolve": value}})

    await common.check_plan_resolution(
        "模板已经发布完成，这件事做完了", source_bucket_id="event-1"
    )

    assert judge.asked == 1
    assert manager.updated == [(
        "plan-1",
        {
            "status": "resolved",
            "resolution_reason": "看起来做完了",
            "resolved_by": "event-1",
        },
    )]


# ---------------------------------------------------------
# 重新激活时清掉上一次结案留下的字段
# ---------------------------------------------------------

def _write_meta(bucket_mgr, bucket_id, **fields):
    """直接往磁盘上的 frontmatter 里写字段，模拟旧代码留下的残留值。"""
    fpath = bucket_mgr._find_bucket_file(bucket_id)
    post = fm.load(fpath)
    for key, value in fields.items():
        post[key] = value
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(fm.dumps(post))


@pytest.mark.asyncio
async def test_reopening_a_plan_clears_resolution_fields(bucket_mgr):
    plan_id = await bucket_mgr.create(content="每天出门前挑香水", bucket_type="plan")
    assert await bucket_mgr.update(
        plan_id,
        status="resolved",
        resolved_by="some-event-bucket",
        resolution_reason="小模型觉得做完了",
    )
    closed = (await bucket_mgr.get(plan_id))["metadata"]
    assert closed["status"] == "resolved"
    assert closed["resolved_by"] == "some-event-bucket"
    assert closed["resolution_reason"] == "小模型觉得做完了"

    assert await bucket_mgr.update(plan_id, status="active")

    reopened = (await bucket_mgr.get(plan_id))["metadata"]
    assert reopened["status"] == "active"
    assert "resolved_by" not in reopened
    assert "resolution_reason" not in reopened
    assert reopened["type"] == "plan"


@pytest.mark.asyncio
async def test_setting_active_on_an_already_active_plan_clears_stale_fields(bucket_mgr):
    """旧代码留下的样子：status 是 active，resolved_by 却还指着一条桶。

    清理它不应该要求先把 plan 关掉——关的那一下就会触发联动。
    """
    plan_id = await bucket_mgr.create(content="二改慢慢做", bucket_type="plan")
    _write_meta(
        bucket_mgr, plan_id,
        status="active", resolved_by="unrelated-bucket", resolution_reason="旧的理由",
    )
    before = (await bucket_mgr.get(plan_id))["metadata"]
    assert before["resolved_by"] == "unrelated-bucket"

    assert await bucket_mgr.update(plan_id, status="active")

    after = (await bucket_mgr.get(plan_id))["metadata"]
    assert after["status"] == "active"
    assert "resolved_by" not in after
    assert "resolution_reason" not in after


@pytest.mark.asyncio
async def test_resolving_keeps_resolution_fields(bucket_mgr):
    """对照组：清理只发生在设成 active 的时候，结案和放下都不受影响。"""
    plan_id = await bucket_mgr.create(content="把月亮要回来", bucket_type="plan")
    assert await bucket_mgr.update(
        plan_id, status="resolved", resolved_by="manual", resolution_reason="做完了",
    )
    assert await bucket_mgr.update(plan_id, status="abandoned")

    meta = (await bucket_mgr.get(plan_id))["metadata"]
    assert meta["status"] == "abandoned"
    assert meta["resolved_by"] == "manual"
    assert meta["resolution_reason"] == "做完了"


@pytest.mark.asyncio
async def test_other_updates_do_not_touch_resolution_fields(bucket_mgr):
    """对照组：不带 status 的 update 不清任何东西。"""
    plan_id = await bucket_mgr.create(content="一条已经关掉的计划", bucket_type="plan")
    assert await bucket_mgr.update(
        plan_id, status="resolved", resolved_by="manual", resolution_reason="做完了",
    )
    assert await bucket_mgr.update(plan_id, why_remembered="留个底")

    meta = (await bucket_mgr.get(plan_id))["metadata"]
    assert meta["status"] == "resolved"
    assert meta["resolved_by"] == "manual"
    assert meta["resolution_reason"] == "做完了"


@pytest.mark.asyncio
async def test_explicit_fields_win_over_the_cleanup(bucket_mgr):
    """调用方在同一次 update 里显式传了这两个字段时，以调用方为准。"""
    plan_id = await bucket_mgr.create(content="迁移脚本会这样写", bucket_type="plan")
    assert await bucket_mgr.update(
        plan_id, status="active", resolved_by="kept-on-purpose",
    )

    meta = (await bucket_mgr.get(plan_id))["metadata"]
    assert meta["status"] == "active"
    assert meta["resolved_by"] == "kept-on-purpose"
    assert "resolution_reason" not in meta


@pytest.mark.asyncio
async def test_reopened_plan_no_longer_drags_an_unrelated_bucket_down(
    bucket_mgr, monkeypatch,
):
    """整条链路：被误关过的 plan 重新打开以后，再显式结案不会连带沉掉旧桶。"""
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "logger", _Logger(), raising=False)

    unrelated_id = await bucket_mgr.create(content="2026-11-14 今天挑了地中海花园")
    plan_id = await bucket_mgr.create(content="Render 和安全那一侧", bucket_type="plan")
    # 自动结案当年留下的痕迹：resolved_by 指着触发它的那条 hold
    assert await bucket_mgr.update(
        plan_id, status="resolved", resolved_by=unrelated_id, resolution_reason="误判",
    )

    # 发现以后把它打开
    assert await bucket_mgr.update(plan_id, status="active")

    # 过了很久，这条 plan 真的做完了，显式结案 → 走联动
    assert await bucket_mgr.update(plan_id, status="resolved")
    plan_meta = (await bucket_mgr.get(plan_id))["metadata"]
    linked = await common.cascade_plan_resolved_to_buckets(plan_meta, plan_id)

    assert linked == []
    unrelated = (await bucket_mgr.get(unrelated_id))["metadata"]
    assert unrelated.get("resolved") is not True
