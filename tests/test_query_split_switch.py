"""二改第 9 刀：查询拆词。

原来 search() 把整个查询当一个子串去比，几个词拼在一起，只有整串原样出现才算
字面命中。11-17 实测整句搜「阿雾最近在看的书」排第一的是不相干的一条。

现在（只在 breath 检索里）：
- 只按空格拆（半角、全角都算），去重，空的扔掉；只有一个词时一切照旧。
- 全中 = 每个词都在名字、标签、主题域、正文或任意一条 meaning 里出现过，
  不要求挨着。全中就是字面命中：加分、放行、算「想起」，全部照旧。
- 部分命中（中了至少一个、没全中）：不加分、不续命。本来就能进结果的，位置和
  分数不动；只因部分命中才进来的排在最后，先按中了几个词，再按原分。
- search() 在拆词时不截部分命中的桶，breath 才数得准还剩几条。
- 搜了不止一个词时，头部标「字面 k/n」；没有字面命中的不标。
- 条数或 token 放不下的部分命中，每条退成一行「标题 | 字面 k/n | 创建日期 | 桶ID」，
  最多 10 行，算进 token 预算；没列出来的照实说「还有 N 条没显示」。
- 开关 matching.query_split，默认开；关掉回到整句原样比。
- 后台搜索框、自动合并、plan 预筛不传 split_terms，行为不变。
"""

import asyncio
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from bucket_manager import BucketManager
from tools.breath import dispatch


# ---------------------------------------------------------------------------
# 检索层：真实的 BucketManager
# ---------------------------------------------------------------------------

@pytest.fixture
def make_mgr(test_config, fake_embedding_engine, tmp_path):
    """每个 manager 一份独立的桶目录。

    fuzzy_threshold 调到 101：混合分永远过不了线，结果里只剩字面命中、语义命中
    和部分命中，测试不受模糊匹配实现（真 rapidfuzz 还是替身）的影响。
    """
    counter = iter(range(1000))

    def _make(**matching_overrides):
        cfg = dict(test_config)
        base = tmp_path / f"vault{next(counter)}"
        for sub in ("permanent", "dynamic", "archive", "feel"):
            (base / sub).mkdir(parents=True)
        cfg["buckets_dir"] = str(base)
        matching = dict(cfg.get("matching") or {})
        matching["fuzzy_threshold"] = 101
        matching.update(matching_overrides)
        cfg["matching"] = matching
        return BucketManager(cfg, embedding_engine=fake_embedding_engine)

    return _make


async def _three_kinds(mgr):
    both = await mgr.create("2026-11-20 周五晚上去上了舞蹈课，这周学的是爵士。", name="舞蹈课")
    one = await mgr.create("2026-11-21 听了一张爵士唱片。", name="唱片")
    none = await mgr.create("2026-11-22 今天下了很大的雨。", name="下雨")
    return both, one, none


@pytest.mark.asyncio
async def test_default_search_does_not_split(make_mgr):
    """不传 split_terms 的调用方（后台搜索框、合并、plan 预筛）照旧整句比。"""
    mgr = make_mgr()
    both, one, _none = await _three_kinds(mgr)
    hits = await mgr.search("舞蹈 爵士")
    ids = [b["id"] for b in hits]
    assert both not in ids
    assert one not in ids
    assert all("literal_terms" not in b for b in hits)


@pytest.mark.asyncio
async def test_split_full_hit_and_partial_hit(make_mgr):
    mgr = make_mgr()
    both, one, none = await _three_kinds(mgr)
    hits = await mgr.search("舞蹈 爵士", split_terms=True)
    by_id = {b["id"]: b for b in hits}

    assert [b["id"] for b in hits] == [both, one]
    assert none not in by_id
    # 全中就是字面命中：放行、加分、算想起
    assert by_id[both]["literal_hit"] is True
    assert by_id[both]["literal_terms"] == (2, 2)
    assert by_id[both]["partial_only"] is False
    # 部分命中：不算想起，单独标出来
    assert by_id[one]["literal_hit"] is False
    assert by_id[one]["literal_terms"] == (1, 2)
    assert by_id[one]["partial_only"] is True


@pytest.mark.asyncio
async def test_full_hit_gets_bonus_partial_does_not(make_mgr):
    mgr = make_mgr()
    both, one, _none = await _three_kinds(mgr)
    split = {b["id"]: b["score"] for b in await mgr.search("舞蹈 爵士", split_terms=True)}
    # 同一个查询不拆、把门槛放到最低，拿到两条桶不带任何加分的原分来比
    mgr.fuzzy_threshold = -1
    plain = {b["id"]: b["score"] for b in await mgr.search("舞蹈 爵士")}
    assert split[both] == pytest.approx(min(100.0, plain[both] + 25.0), abs=0.01)
    assert split[one] == pytest.approx(plain[one], abs=0.01)


@pytest.mark.asyncio
async def test_partial_tail_sorted_by_hit_count_and_not_truncated(make_mgr):
    mgr = make_mgr()
    two_of_three = await mgr.create("2026-11-20 舞蹈和爵士。", name="二")
    singles = [
        await mgr.create(f"2026-11-2{i} 第 {i} 条只写了唱片。", name=f"单{i}")
        for i in range(5)
    ]
    hits = await mgr.search("舞蹈 爵士 唱片", split_terms=True, limit=2)
    ids = [b["id"] for b in hits]
    # 部分命中不受 limit 截断：一条 2/3 加五条 1/3 全都在
    assert len(ids) == 6
    assert ids[0] == two_of_three
    assert set(ids[1:]) == set(singles)
    assert [b["literal_terms"][0] for b in hits] == [2, 1, 1, 1, 1, 1]


@pytest.mark.asyncio
async def test_existing_hits_keep_position_and_score(make_mgr):
    """拆词之前就能进结果的桶（这里是语义过线），分数和位置一点不动。"""
    mgr = make_mgr()
    semantic = await mgr.create("2026-11-20 一条只和舞蹈有关的记录。", name="语义")
    other = await mgr.create("2026-11-21 一条完全无关的记录。", name="无关")
    vectors = {semantic: 0.9, other: 0.8}

    before = await mgr.search("舞蹈 爵士", vector_scores=vectors)
    after = await mgr.search("舞蹈 爵士", vector_scores=vectors, split_terms=True)
    assert [b["id"] for b in after] == [b["id"] for b in before]
    assert [b["score"] for b in after] == [b["score"] for b in before]
    hit = next(b for b in after if b["id"] == semantic)
    assert hit["literal_terms"] == (1, 2)
    assert hit["partial_only"] is False


@pytest.mark.asyncio
async def test_single_word_unchanged(make_mgr):
    mgr = make_mgr()
    await _three_kinds(mgr)
    plain = await mgr.search("爵士")
    split = await mgr.search("爵士", split_terms=True)
    assert [(b["id"], b["score"]) for b in split] == [(b["id"], b["score"]) for b in plain]
    assert all("literal_terms" not in b for b in split)
    # 重复的词去重后只剩一个，也是一切照旧（整句原样比）
    dup = await mgr.search("爵士 爵士", split_terms=True)
    assert all("literal_terms" not in b for b in dup)


@pytest.mark.asyncio
async def test_fullwidth_space_splits(make_mgr):
    mgr = make_mgr()
    both, one, _none = await _three_kinds(mgr)
    hits = await mgr.search("舞蹈　爵士", split_terms=True)
    assert [b["id"] for b in hits] == [both, one]


@pytest.mark.asyncio
async def test_words_can_come_from_meaning(make_mgr):
    mgr = make_mgr()
    bid = await mgr.create("2026-11-20 周五晚上去上了舞蹈课。", name="课", meaning="像回到小时候学爵士")
    hits = await mgr.search("舞蹈 爵士", split_terms=True)
    assert hits[0]["id"] == bid
    assert hits[0]["literal_terms"] == (2, 2)

    off = make_mgr(literal_meaning=False)
    bid_off = await off.create("2026-11-20 周五晚上去上了舞蹈课。", name="课", meaning="像回到小时候学爵士")
    hits_off = await off.search("舞蹈 爵士", split_terms=True)
    assert hits_off[0]["id"] == bid_off
    assert hits_off[0]["literal_terms"] == (1, 2)


# ---------------------------------------------------------------------------
# breath 层
# ---------------------------------------------------------------------------

class DisabledEmbedding:
    enabled = False

    async def search_similar(self, query, top_k=20):
        return []


class FlatDecay:
    is_running = True

    async def ensure_started(self):
        return None

    def calculate_score(self, metadata):
        return 5.0   # 都不算低权重，「忽然想起来」不会混进来


class RecordingManager:
    """模拟检索层：原样返回给定的结果，记下 breath 传了什么。"""

    def __init__(self, buckets):
        self.buckets = list(buckets)
        self.kwargs = None
        self.touched = []

    async def get(self, _bucket_id):
        return None

    async def search(self, _query, limit=None, domain_filter=None, query_valence=None,
                     query_arousal=None, vector_scores=None, include_archive=False,
                     split_terms=False):
        self.kwargs = {"limit": limit, "split_terms": split_terms}
        return list(self.buckets)

    async def list_all(self, include_archive=False):
        return list(self.buckets)

    async def touch_many(self, bucket_ids, ripple=False):
        self.touched.extend(bucket_ids)


class OldManager(RecordingManager):
    """不认识 split_terms 的 manager：传了会 TypeError。"""

    async def search(self, _query, limit=None, domain_filter=None, query_valence=None,
                     query_arousal=None, vector_scores=None, include_archive=False):
        self.kwargs = {"limit": limit}
        return list(self.buckets)


def _bucket(bucket_id, *, hit, total, literal=False, partial=False, name=None,
            content=None, created="2026-07-19T12:00:00"):
    return {
        "id": bucket_id,
        "content": content if content is not None else f"{bucket_id} 的正文。",
        "literal_hit": literal,
        "semantic_score": None,
        "literal_terms": (hit, total),
        "partial_only": partial,
        "metadata": {
            "name": bucket_id if name is None else name,
            "type": "dynamic",
            "importance": 5,
            "created": created,
        },
    }


def _install(monkeypatch, manager, matching=None):
    cfg = {"surfacing": {}}
    if matching is not None:
        cfg["matching"] = matching
    monkeypatch.setattr(rt, "config", cfg)
    monkeypatch.setattr(rt, "bucket_mgr", manager)
    monkeypatch.setattr(rt, "decay_engine", FlatDecay())
    monkeypatch.setattr(rt, "dehydrator", MagicMock())
    monkeypatch.setattr(rt, "embedding_engine", DisabledEmbedding())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "fire_webhook", None)
    monkeypatch.setattr(rt, "mark_op", None)
    monkeypatch.setattr(rt, "record_v3_tool_event", lambda *_a, **_k: None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "matching, expected",
    [
        (None, True),                              # 没写：默认开
        ({"query_split": True}, True),
        ({"query_split": "随便写的"}, True),        # 认不出的字按默认
        ({"query_split": False}, False),
        ({"query_split": "off"}, False),
        ({"query_split": "false"}, False),
    ],
)
async def test_switch_controls_split_terms(monkeypatch, matching, expected):
    manager = RecordingManager([_bucket("a", hit=1, total=1, literal=True)])
    _install(monkeypatch, manager, matching)
    await dispatch(query="舞蹈 爵士", max_results=5, max_tokens=6000)
    assert manager.kwargs["split_terms"] is expected


@pytest.mark.asyncio
async def test_manager_without_split_param_still_works(monkeypatch):
    manager = OldManager([_bucket("a", hit=1, total=1, literal=True)])
    _install(monkeypatch, manager)
    output = await dispatch(query="舞蹈 爵士", max_results=5, max_tokens=6000)
    assert "[bucket_id:a]" in output
    assert "检索过程出错" not in output


@pytest.mark.asyncio
async def test_header_marks_literal_count(monkeypatch):
    manager = RecordingManager([
        _bucket("full", hit=2, total=2, literal=True),
        _bucket("part", hit=1, total=2, partial=True),
        _bucket("zero", hit=0, total=2),
    ])
    _install(monkeypatch, manager)
    output = await dispatch(query="舞蹈 爵士", max_results=5, max_tokens=6000)
    assert "[字面 2/2] [bucket_id:full]" in output
    assert "[字面 1/2] [bucket_id:part]" in output
    assert "[字面 0/2]" not in output
    assert "[bucket_id:zero]" in output


@pytest.mark.asyncio
async def test_partial_hits_are_not_touched(monkeypatch):
    manager = RecordingManager([
        _bucket("full", hit=2, total=2, literal=True),
        _bucket("part", hit=1, total=2, partial=True),
    ])
    _install(monkeypatch, manager)
    await dispatch(query="舞蹈 爵士", max_results=5, max_tokens=6000)
    await asyncio.sleep(0)
    assert manager.touched == ["full"]


@pytest.mark.asyncio
async def test_overflow_partials_become_lines_with_exact_count(monkeypatch):
    buckets = [_bucket("full", hit=2, total=2, literal=True)]
    buckets += [_bucket(f"p{i:02d}", hit=1, total=2, partial=True) for i in range(14)]
    manager = RecordingManager(buckets)
    _install(monkeypatch, manager)
    output = await dispatch(query="舞蹈 爵士", max_results=2, max_tokens=20000)

    # 两条全文：全中的一条 + 排最前的部分命中
    assert "[字面 2/2] [bucket_id:full]" in output
    assert "[字面 1/2] [bucket_id:p00]" in output
    assert "[bucket_id:p01]" not in output
    # 剩下 13 条：列 10 行，照实说还有 3 条
    assert "=== 只中了部分词，没展开（按桶 ID 搜可看全文） ===" in output
    for i in range(1, 11):
        assert f"p{i:02d} | 字面 1/2 | 2026-07-19 | p{i:02d}" in output
    assert "p11 | 字面" not in output
    assert "还有 3 条没显示" in output


@pytest.mark.asyncio
async def test_line_without_title_uses_first_20_chars(monkeypatch):
    body = "一二三四五六七八九十\n甲乙丙丁戊己庚辛壬癸子丑寅卯"
    manager = RecordingManager([
        _bucket("full", hit=2, total=2, literal=True),
        _bucket("notitle", hit=1, total=2, partial=True, name="", content=body),
    ])
    _install(monkeypatch, manager)
    output = await dispatch(query="舞蹈 爵士", max_results=1, max_tokens=20000)
    assert "一二三四五六七八九十 甲乙丙丁戊己庚辛壬 | 字面 1/2 | 2026-07-19 | notitle" in output
    assert "还有" not in output


@pytest.mark.asyncio
async def test_no_lines_when_nothing_overflows(monkeypatch):
    manager = RecordingManager([
        _bucket("full", hit=2, total=2, literal=True),
        _bucket("part", hit=1, total=2, partial=True),
    ])
    _install(monkeypatch, manager)
    output = await dispatch(query="舞蹈 爵士", max_results=5, max_tokens=20000)
    assert "只中了部分词" not in output
    assert "没显示" not in output


@pytest.mark.asyncio
async def test_token_overflow_partials_become_lines_and_count_in_budget(monkeypatch):
    long_body = "很长的正文。" * 400
    buckets = [_bucket("full", hit=2, total=2, literal=True)]
    buckets += [
        _bucket(f"p{i}", hit=1, total=2, partial=True, content=long_body)
        for i in range(4)
    ]
    manager = RecordingManager(buckets)
    _install(monkeypatch, manager)
    output = await dispatch(query="舞蹈 爵士", max_results=10, max_tokens=300)

    # 全中的那条放得下，四条长的部分命中放不下全文，都退成一行
    assert "[bucket_id:full]" in output
    assert "很长的正文" not in output
    for i in range(4):
        assert f"p{i} | 字面 1/2 | 2026-07-19 | p{i}" in output
    assert "没显示" not in output


@pytest.mark.asyncio
async def test_lines_stop_when_budget_runs_out(monkeypatch):
    from utils import count_tokens_approx
    from tools.breath.search import _PARTIAL_BLOCK_HEADER, _partial_line

    full = _bucket("full", hit=2, total=2, literal=True)
    partials = [
        _bucket(f"p{i}", hit=1, total=2, partial=True, name=f"第{i}条部分命中的标题")
        for i in range(5)
    ]
    manager = RecordingManager([full] + partials)
    _install(monkeypatch, manager)
    rt.config["surfacing"]["overflow_lines"] = False

    # 先用宽松的预算跑一次，拿到全中那条实际渲染出来的样子
    probe = await dispatch(query="舞蹈 爵士", max_results=1, max_tokens=20000)
    body = probe.split("\n", 1)[1]            # 去掉开头那行语义降级提示
    full_entry = body.split("\n---\n")[0]
    assert full_entry.startswith("[字面 2/2] [bucket_id:full]")

    # 预算刚好够：全中那条 + 区块标题 + 两行
    budget = (
        count_tokens_approx(full_entry)
        + count_tokens_approx(_PARTIAL_BLOCK_HEADER)
        + count_tokens_approx(_partial_line(partials[0]))
        + count_tokens_approx(_partial_line(partials[1]))
    )
    output = await dispatch(query="舞蹈 爵士", max_results=1, max_tokens=budget)
    assert "第0条部分命中的标题 | 字面 1/2" in output
    assert "第1条部分命中的标题 | 字面 1/2" in output
    assert "第2条部分命中的标题" not in output
    assert "还有 3 条没显示" in output


@pytest.mark.asyncio
async def test_end_to_end_with_real_manager(monkeypatch, make_mgr):
    """真实 manager：「舞蹈 爵士」能搜出只写了爵士的那条，排在全中后面，不续命。"""
    mgr = make_mgr()
    both, one, none = await _three_kinds(mgr)
    before = (await mgr.get(one))["metadata"].get("last_active")
    _install(monkeypatch, mgr)

    output = await dispatch(query="舞蹈 爵士", max_results=5, max_tokens=20000)
    assert f"[字面 2/2] [bucket_id:{both}]" in output
    assert f"[字面 1/2] [bucket_id:{one}]" in output
    assert output.index(both) < output.index(one)
    assert none not in output
    await asyncio.sleep(0.05)
    assert (await mgr.get(one))["metadata"].get("last_active") == before


@pytest.mark.asyncio
async def test_switch_off_end_to_end(monkeypatch, make_mgr):
    mgr = make_mgr()
    both, one, _none = await _three_kinds(mgr)
    _install(monkeypatch, mgr, {"query_split": False})
    output = await dispatch(query="舞蹈 爵士", max_results=5, max_tokens=20000)
    assert "字面" not in output
    assert both not in output
    assert one not in output
