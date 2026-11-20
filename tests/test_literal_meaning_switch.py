"""二改第 9a 刀：关键词搜索的字面命中也看 meaning。

原来 search() 的字面命中只查标题、标签、主题域和正文。meaning 只有最新一条
有向量，之前追加的几条任何一条路都搜不到；最新那条也只能靠语义找到。

现在：
- 查询串原样出现在任意一条 meaning 里，就算字面命中：放行、加分、续命，
  和正文命中完全一样。
- 每条 meaning 单独比，不和正文拼在一起。
- 开关 matching.literal_meaning，默认开；写 false 回到旧行为。
- BM25、模糊分、向量都不动；why_remembered 仍然搜不到。
"""

import pytest

from bucket_manager import BucketManager


# 这句处境只写在 meaning 里，正文、标题、标签里都没有。
_SITUATION = "不是讲给我看的"
_CONTENT = "2026-06-09 她和朋友吃了白菜炖排骨，回来跟我讲店名记错了。"


@pytest.fixture
def mgr_with_switch(test_config, fake_embedding_engine, tmp_path):
    counter = iter(range(1000))

    def _make(value):
        cfg = dict(test_config)
        # 每个 manager 一份独立的桶目录，互相看不到对方写的桶
        base = tmp_path / f"vault{next(counter)}"
        for sub in ("permanent", "dynamic", "archive", "feel"):
            (base / sub).mkdir(parents=True)
        cfg["buckets_dir"] = str(base)
        matching = dict(cfg.get("matching") or {})
        if value is not _UNSET:
            matching["literal_meaning"] = value
        cfg["matching"] = matching
        return BucketManager(cfg, embedding_engine=fake_embedding_engine)

    return _make


_UNSET = object()


async def _ids(mgr, query):
    return [b["id"] for b in await mgr.search(query)]


@pytest.mark.asyncio
async def test_default_finds_word_only_in_meaning(bucket_mgr):
    bid = await bucket_mgr.create(_CONTENT, name="蓝帽子", meaning=f"她回来把一天讲给我听，{_SITUATION}")
    other = await bucket_mgr.create("2026-06-10 今天下了很大的雨。", name="下雨")

    hits = await bucket_mgr.search(_SITUATION)
    ids = [b["id"] for b in hits]
    assert bid in ids
    assert other not in ids
    hit = next(b for b in hits if b["id"] == bid)
    # 算字面命中：第 4 刀据此续命，结果头部也不会被标成 [语义关联]
    assert hit["literal_hit"] is True
    assert "vector_match" not in hit


@pytest.mark.asyncio
async def test_older_meaning_entries_are_searchable(bucket_mgr):
    bid = await bucket_mgr.create(_CONTENT, name="蓝帽子", meaning=f"第一次写：{_SITUATION}")
    await bucket_mgr.update(bid, meaning_append="第二次补的另一句处境")

    # 旧的那条没有向量，以前任何一条路都找不到它
    assert bid in await _ids(bucket_mgr, _SITUATION)
    assert bid in await _ids(bucket_mgr, "第二次补的另一句处境")


@pytest.mark.asyncio
async def test_switch_off_restores_old_behavior(mgr_with_switch):
    mgr = mgr_with_switch(False)
    bid = await mgr.create(_CONTENT, name="蓝帽子", meaning=f"她回来讲给我听，{_SITUATION}")
    assert bid not in await _ids(mgr, _SITUATION)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", "off", "0", "no"])
async def test_switch_off_strings(mgr_with_switch, value):
    mgr = mgr_with_switch(value)
    bid = await mgr.create(_CONTENT, name="蓝帽子", meaning=f"她回来讲给我听，{_SITUATION}")
    assert bid not in await _ids(mgr, _SITUATION)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, "true", "on", "enable", "写错了", None, 5])
async def test_switch_on_or_unreadable_uses_default(mgr_with_switch, value):
    mgr = mgr_with_switch(value)
    bid = await mgr.create(_CONTENT, name="蓝帽子", meaning=f"她回来讲给我听，{_SITUATION}")
    assert bid in await _ids(mgr, _SITUATION)


@pytest.mark.asyncio
async def test_matching_section_missing_uses_default(test_config, fake_embedding_engine):
    cfg = dict(test_config)
    cfg.pop("matching", None)
    mgr = BucketManager(cfg, embedding_engine=fake_embedding_engine)
    bid = await mgr.create(_CONTENT, name="蓝帽子", meaning=f"她回来讲给我听，{_SITUATION}")
    assert bid in await _ids(mgr, _SITUATION)


@pytest.mark.asyncio
async def test_content_hits_unchanged(mgr_with_switch):
    """只在正文里的词：开关开和关，结果、分数、literal_hit 一模一样。"""
    results = []
    for value in (True, False):
        mgr = mgr_with_switch(value)
        await mgr.create(_CONTENT, name="蓝帽子", meaning="一句和查询无关的处境")
        await mgr.create("2026-06-11 楼上装修很吵。", name="装修")
        hits = await mgr.search("白菜炖排骨")
        results.append([(b["content"], b["score"], b["literal_hit"]) for b in hits])
    assert results[0] == results[1]
    assert results[0] and results[0][0][2] is True


@pytest.mark.asyncio
async def test_no_cross_boundary_match(bucket_mgr):
    """正文结尾和 meaning 开头拼起来才凑出的串，不算命中。"""
    # 标题故意不含查询里的词：真 rapidfuzz 下标题模糊分很高，会把这条测试变成在测模糊分
    bid = await bucket_mgr.create("2026-06-12 今天去了顶楼", name="周末", meaning="餐厅吃牛排")
    assert bid not in await _ids(bucket_mgr, "顶楼 餐厅")


@pytest.mark.asyncio
async def test_why_remembered_still_not_searchable(bucket_mgr):
    bid = await bucket_mgr.create(
        _CONTENT, name="蓝帽子", why_remembered=f"因为{_SITUATION}"
    )
    assert bid not in await _ids(bucket_mgr, _SITUATION)


def test_meaning_texts_shapes():
    from bucket_manager import _meaning_texts

    assert _meaning_texts({}) == []
    assert _meaning_texts({"meaning": None}) == []
    assert _meaning_texts({"meaning": "单独一句"}) == ["单独一句"]
    assert _meaning_texts({"meaning": ["a", "", "  ", 3, "b"]}) == ["a", "b"]
    assert _meaning_texts({"meaning": {"x": 1}}) == []
