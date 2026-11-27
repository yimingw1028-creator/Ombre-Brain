"""二改第 8c 刀：breath 放不下全文的桶退成一行，头部带创建日期。

原来：无 query 的浮现里，放不下剩余预算的桶整条跳过，只说「有 N 条没返回」，
并且只要跳过过一条，「久未浮现」「偶然想起」两段整段不跑；有 query 的检索
碰到第一条放不下的就全部停下。

现在：放不下全文的桶退成一行「标题 | 创建日期 | 全文 N 字 | 桶ID」，不给半截正文；
这些行算进预算，一行也放不下时照实说还有几条。
连带关闭去掉。每条头部末尾带创建日期（YYYY-MM-DD）。

8c 小修（11-27 验收后）：原来先尽量给全文，剩下的零头再列行，预算一紧那几行常常
一条都挤不进去。现在每给一条全文之前，先看给了它以后还够不够给最多 10 条各列一行；
不够就退成一行。一段最多 10 行。检索：从这一条起全部退成一行（一放不下就停，后面
的相关度更低）。浮现：只这一条退成一行，后面放得下的照样给全文（浮现的顺序是加权
随机洗过的，排在后面的不一定不重要）。

开关：config.yaml 里 surfacing.overflow_lines: false → 三件事一起回到旧行为。
"""

from utils import count_tokens_approx, parse_iso_datetime, strip_wikilinks

from .. import _runtime as rt
from ._verbatim import stored_bucket_content

LINE_TITLE_CHARS = 20   # June 11-27 定：没标题时取正文开头 20 字，和第 9 刀那一行对齐
LINES_MAX = 10          # 8c 小修：一段最多列 10 行，沿用 June 11-27 给第 9 刀定的上限


def overflow_lines_enabled() -> bool:
    """读不到、写错了都按默认（开）；只有明确写 false/0/no/off 才回到旧行为。"""
    cfg = getattr(rt, "config", None)
    surfacing = cfg.get("surfacing") if isinstance(cfg, dict) else None
    if not isinstance(surfacing, dict):
        return True
    value = surfacing.get("overflow_lines", True)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return True


def created_date(bucket: dict) -> str:
    """桶的创建日期 YYYY-MM-DD；没有就是空串。"""
    raw = str((bucket.get("metadata") or {}).get("created") or "").strip()
    if not raw:
        return ""
    try:
        return parse_iso_datetime(raw).date().isoformat()
    except (TypeError, ValueError, AttributeError):
        return raw[:10]


def dated(header: str, bucket: dict, enabled: bool) -> str:
    """头部末尾加创建日期；开关关着或没有日期时原样返回。"""
    date = created_date(bucket) if enabled else ""
    return f"{header} {date}" if date else header


def line_title(bucket: dict) -> str:
    meta = bucket.get("metadata") or {}
    title = " ".join(str(meta.get("name") or "").split())
    if title:
        return title
    body = " ".join(str(bucket.get("content") or "").split())
    return body[:LINE_TITLE_CHARS] or "（无标题）"


def overflow_line(bucket: dict) -> str:
    """标题 | 创建日期 | 全文 N 字 | 桶ID。N 是会显示出来的正文字数。"""
    try:
        body = strip_wikilinks(stored_bucket_content(bucket))
    except TypeError:
        body = str(bucket.get("content") or "")
    date = created_date(bucket) or "日期未知"
    return f"{line_title(bucket)} | {date} | 全文 {len(body)} 字 | {bucket.get('id', '')}"


def lines_cost(buckets: list, header: str, line_fn=overflow_line) -> int:
    """8c 小修：把这些桶各列成一行（连同 header）要多少 token；没有桶就是 0。"""
    if not buckets:
        return 0
    return count_tokens_approx(header) + sum(
        count_tokens_approx(line_fn(bucket)) for bucket in buckets
    )


def fit_lines(
    buckets: list, budget_left: int, header: str, limit: int | None = None
) -> tuple[list[str], int, int]:
    """把放不下的桶逐条退成一行，连同 header 一起算进剩下的预算。

    limit：最多列几行（8c 小修起两处都传 LINES_MAX）；超出的算进没列出来的条数。
    返回 (行, 用掉的 token, 没列出来的条数)。一行也放不下时不用预算、不出 header。
    """
    lines: list[str] = []
    used = count_tokens_approx(header)
    for bucket in (buckets if limit is None else buckets[:limit]):
        line = overflow_line(bucket)
        cost = count_tokens_approx(line)
        if used + cost > budget_left:
            break
        lines.append(line)
        used += cost
    if not lines:
        return [], 0, len(buckets)
    return lines, used, len(buckets) - len(lines)
