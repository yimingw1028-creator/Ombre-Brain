"""二改第 5 刀：续命不等于变新。

衰减分里有两件事原来共用一只钟（last_active）：
  1. 指数衰减 e^(-λ×days) —— 多久没动，决定会不会沉下去；
  2. 新鲜度加成 + 短期/长期分支 —— 是不是刚发生的事。
breath_search 命中会刷新 last_active，于是一条一个月前的旧桶被搜到一次，
就同时拿到"不会沉"和"刚发生"两样，在 breath 里和昨天写的桶排在一起。

这一刀把第 2 件事改成看 created。第 1 件事不动。

这个文件只依赖 decay_engine 本身，不需要桶管理器和任何第三方库。
"""

from datetime import datetime, timedelta

import pytest

from decay_engine import DecayEngine, _days_since_created


def _engine(clock=None):
    decay_cfg = {}
    if clock is not None:
        decay_cfg["freshness_clock"] = clock
    return DecayEngine({"decay": decay_cfg}, bucket_mgr=None)


def _meta(*, age_days, idle_days, count=1, importance=7, arousal=0.5, **extra):
    now = datetime.now()
    meta = {
        "type": "dynamic",
        "importance": importance,
        "activation_count": count,
        "arousal": arousal,
        "valence": 0.5,
        "created": (now - timedelta(days=age_days)).isoformat(),
        "last_active": (now - timedelta(days=idle_days)).isoformat(),
    }
    meta.update(extra)
    return meta


def test_default_clock_is_created():
    assert _engine().freshness_clock == "created"
    assert _engine("active").freshness_clock == "active"
    # 认不出的值不报错，按默认处理
    assert _engine("whatever").freshness_clock == "created"
    assert _engine("").freshness_clock == "created"


def test_never_touched_bucket_scores_exactly_as_before():
    """没被搜到过的桶 created == last_active，两只钟读数一样，分数一分不差。"""
    new, old = _engine(), _engine("active")
    for days in (0, 0.5, 1, 2, 2.9, 3.1, 5, 10, 30, 60, 90):
        meta = _meta(age_days=days, idle_days=days)
        assert new.calculate_score(meta) == pytest.approx(old.calculate_score(meta), rel=1e-3)


def test_old_bucket_touched_today_gets_no_freshness_bonus():
    """一个月前的桶今天被搜中：旧行为拿到 ×2 新鲜度并走短期分支，新行为不拿。"""
    meta = _meta(age_days=30, idle_days=0)
    old_score = _engine("active").calculate_score(meta)
    new_score = _engine().calculate_score(meta)
    assert new_score < old_score
    # 旧：7 × (0.7×2.0 + 0.3×1.4) = 12.74；新：7 × (0.7×1.4 + 0.3×1.0) = 8.96
    assert old_score == pytest.approx(12.74, abs=0.02)
    assert new_score == pytest.approx(8.96, abs=0.02)


def test_touched_once_old_bucket_ranks_below_yesterdays_bucket():
    """同样重要度：被搜中一次的旧桶，排在昨天新写的桶后面（旧行为是排在前面）。"""
    touched_old = _meta(age_days=30, idle_days=0)
    written_yesterday = _meta(age_days=1, idle_days=1)
    new, old = _engine(), _engine("active")
    assert new.calculate_score(touched_old) < new.calculate_score(written_yesterday)
    assert old.calculate_score(touched_old) > old.calculate_score(written_yesterday)


def test_touch_still_keeps_bucket_from_archiving():
    """续命还是续命：指数衰减仍按 last_active 算。

    重要度 5、一百天前的桶，从没被想起时早已低于归档线；
    今天被搜中之后，分数远高于归档线。
    """
    eng = _engine()
    never = _meta(age_days=100, idle_days=100, importance=5, arousal=0.3)
    touched = _meta(age_days=100, idle_days=0, importance=5, arousal=0.3)
    assert eng.calculate_score(never) < eng.threshold
    assert eng.calculate_score(touched) > eng.threshold * 10


def test_archive_timeline_after_touch_is_unchanged():
    """旧钟的新鲜度加成在搜中后十天内衰减干净；之后两只钟算出来的分数相同。

    桶要闲置一两个月才会碰到归档线，所以归档日期不变。
    """
    new, old = _engine(), _engine("active")
    for idle in (10, 30, 60, 84):
        meta = _meta(age_days=40 + idle, idle_days=idle, count=3)
        assert new.calculate_score(meta) == pytest.approx(old.calculate_score(meta), rel=1e-3)


def test_new_bucket_touched_same_day_stays_fresh():
    """昨天写的桶今天被搜中：它本来就是新事，新鲜度按写下的时间算，不因为被搜而变得更新。"""
    eng = _engine()
    touched = _meta(age_days=1, idle_days=0, count=1)
    untouched = _meta(age_days=1, idle_days=1, count=1)
    # 只差指数衰减那一天（e^0.05 ≈ 1.051），新鲜度加成相同
    ratio = eng.calculate_score(touched) / eng.calculate_score(untouched)
    assert ratio == pytest.approx(1.0513, abs=0.002)


def test_missing_or_broken_created_falls_back_to_last_active():
    """created 读不到的桶保持旧行为，不报错。"""
    new, old = _engine(), _engine("active")
    for bad in ("", None, "not-a-date"):
        meta = _meta(age_days=30, idle_days=0)
        meta["created"] = bad
        assert new.calculate_score(meta) == pytest.approx(old.calculate_score(meta), rel=1e-3)
    meta = _meta(age_days=30, idle_days=0)
    del meta["created"]
    assert new.calculate_score(meta) == pytest.approx(old.calculate_score(meta), rel=1e-3)


def test_created_newer_than_last_active_is_treated_as_old_behaviour():
    """脏数据：created 比 last_active 还新。取较大的天数，不凭空变新。"""
    new, old = _engine(), _engine("active")
    meta = _meta(age_days=0, idle_days=20)
    assert new.calculate_score(meta) == pytest.approx(old.calculate_score(meta), rel=1e-3)


def test_locked_scores_are_untouched():
    eng = _engine()
    assert eng.calculate_score({"pinned": True}) == 999.0
    assert eng.calculate_score({"protected": True}) == 999.0
    assert eng.calculate_score({"type": "permanent"}) == 999.0
    for kind in ("feel", "plan", "letter"):
        assert eng.calculate_score({"type": kind}) == 50.0


def test_days_since_created_helper():
    now = datetime.now()
    assert _days_since_created({"created": (now - timedelta(days=2)).isoformat()}) == pytest.approx(2.0, abs=0.01)
    # 未来时间（时钟漂移）不产生负数
    assert _days_since_created({"created": (now + timedelta(days=1)).isoformat()}) == 0.0
    assert _days_since_created({}) is None
    assert _days_since_created({"created": "garbage"}) is None
    assert _days_since_created("not a dict") is None
    # last_active 不算数
    assert _days_since_created({"last_active": now.isoformat()}) is None
