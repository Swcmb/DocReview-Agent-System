"""SectionIndex：Markdown section 解析与 location 归一化（规格 §6.2–§6.4）。

本模块只做**纯本地**的确定性解析，不触碰 Laya、不 import torch。
T-04 范围：SectionRecord / 行级状态机 / CRLF / fence / LocationResolution。
分块（SectionChunk、selected/omitted_indices）属 T-05，见 `chunk_sections`。

设计要点（每一条都对应规格里一条硬要求）：

- **行级状态机**而非 `split("#")`（§6.3）：fence 内的 `#` 必须当普通文本。
- **CRLF 是一个换行边界**，不是两个（§6.3.1）：`\\r\\n` 绝不能被拆成两次边界，
  否则 offset 和行号会全部错位。
- **绝不猜重复标题**（§6.3.6）：标题只有唯一匹配才能当 location。
- **失败只写错误码，不猜位置**（§6.4）：解析失败时保留原始 location，
  由调用方决定降级；Laya 不回答「这个 location 有效吗」。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from typing import Final, NamedTuple, TypedDict

__all__ = [
    "SectionIndex",
    "SectionRecord",
    "LocationResolution",
    "build_section_index",
    "normalize_location",
    "LOC_EMPTY",
    "LOC_INVALID_FORMAT",
    "LOC_UNKNOWN_SECTION",
    "LOC_AMBIGUOUS_TITLE",
    "LOC_LINE_OUT_OF_RANGE",
    "LOC_INDEX_UNAVAILABLE",
    "LOC_ENCODING_FALLBACK",
]


class SectionRecord(TypedDict):
    """单个 section 的索引记录（§6.2）。"""

    section_id: str
    title: str
    heading_level: int | None
    start: int
    end: int
    start_line: int
    end_line: int
    content_sha256: str


class LocationResolution(TypedDict):
    """location 归一化结果（§6.4）。"""

    valid: bool
    normalized: str | None
    error_code: str | None
    error_message: str | None


# ---------------------------------------------------------------------------
# §6.4 稳定错误码
# ---------------------------------------------------------------------------
LOC_EMPTY: Final = "LOC_EMPTY"
LOC_INVALID_FORMAT: Final = "LOC_INVALID_FORMAT"
LOC_UNKNOWN_SECTION: Final = "LOC_UNKNOWN_SECTION"
LOC_AMBIGUOUS_TITLE: Final = "LOC_AMBIGUOUS_TITLE"
LOC_LINE_OUT_OF_RANGE: Final = "LOC_LINE_OUT_OF_RANGE"
LOC_INDEX_UNAVAILABLE: Final = "LOC_INDEX_UNAVAILABLE"
LOC_ENCODING_FALLBACK: Final = "LOC_ENCODING_FALLBACK"

_ERROR_MESSAGES: Final[dict[str, str]] = {
    LOC_EMPTY: "location 为空",
    LOC_INVALID_FORMAT: "location 语法无法解析",
    LOC_UNKNOWN_SECTION: "section 不存在",
    LOC_AMBIGUOUS_TITLE: "完整标题重复出现，无法唯一确定",
    LOC_LINE_OUT_OF_RANGE: "行号不在 section 的闭区间内",
    LOC_INDEX_UNAVAILABLE: "section_index 不可用",
    LOC_ENCODING_FALLBACK: "文档非 UTF-8，已走全文 fallback，位置不可信",
}


class SectionIndex(list[SectionRecord]):
    """``list[SectionRecord]``，附带 encoding fallback 标记。

    规格 §6.2 声明 ``SectionIndex = list[SectionRecord]``。为了让
    ``normalize_location`` 能报出 ``LOC_ENCODING_FALLBACK``（§6.4），
    索引需要携带「这份索引来自 lossy decode」这一事实。继承 ``list``
    使它在类型与运行时上仍然是 ``list[SectionRecord]``（isinstance、
    索引、切片、比较全部照常工作），只多挂一个属性。
    """

    __slots__ = ("encoding_fallback",)

    def __init__(
        self, records: list[SectionRecord] | None = None, *, encoding_fallback: bool = False
    ) -> None:
        super().__init__(records if records is not None else [])
        self.encoding_fallback = encoding_fallback


# ---------------------------------------------------------------------------
# §6.3 行扫描：CRLF / LF / CR
# ---------------------------------------------------------------------------
class _Line(NamedTuple):
    """一条逻辑行。offset 均相对解码后的完整字符串。"""

    number: int  # 1-based
    start: int  # 行首 offset
    content_end: int  # 行末（不含换行符）offset
    next_start: int  # 下一行首 offset（已跳过 CRLF/LF/CR）
    text: str  # 不含换行符的行内容


def _iter_lines(text: str) -> Iterator[_Line]:
    """逐行产出，识别 CRLF / LF / CR；``\\r\\n`` 永不拆成两个边界（§6.3.1）。

    文本以换行符结尾时**不**额外产出一条空行——否则每个 section 的
    ``end_line`` 都会被多算一行。
    """
    n = len(text)
    pos = 0
    number = 1
    while pos < n:
        j = pos
        while j < n and text[j] not in "\r\n":
            j += 1
        k = j
        if k < n:
            if text[k] == "\r":
                k += 1
                if k < n and text[k] == "\n":  # CRLF 视为单个边界
                    k += 1
            else:
                k += 1
        yield _Line(number, pos, j, k, text[pos:j])
        number += 1
        pos = k


# ---------------------------------------------------------------------------
# §6.3 ATX 标题与 fence
# ---------------------------------------------------------------------------
#: fence 外只接受「最多三空格 + 1~6 个 # + 至少一个空白 + 标题文本」（§6.3.4）。
#: 纯 `#` 或 `#foo` 都不是标题。
_ATX_HEADING = re.compile(r"^ {0,3}(?P<hashes>#{1,6})[ \t]+(?P<title>\S.*?)[ \t]*$")

#: opening fence：最多三空格 + 三个及以上同类反引号/波浪线，其后可有 info（§6.3.2）。
#: 必须捕获**完整** marker 串：若只取恰好三个，```` ```` ```` 开栏会被记成长度 3，
#: 随后的 ``` ``` ``` 就能错误地关闭它（违反「closing 长度 ≥ opening」）。
_FENCE_OPEN = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")

#: closing fence：同类字符、长度 ≥ opening、缩进 ≤ 三空格、其余仅空白。
_FENCE_CLOSE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})[ \t]*$")


def _fence_marker(line: str) -> str | None:
    """返回该行若是 fence 界定符时的 marker，否则 ``None``。"""
    match = _FENCE_OPEN.match(line)
    return match.group("marker") if match else None


def _is_fence_close(line: str, marker: str) -> bool:
    """判断该行能否关闭以 ``marker`` 开头的 fence（§6.3.2）。"""
    match = _FENCE_CLOSE.match(line)
    if not match:
        return False
    candidate = match.group("marker")
    # 必须同类字符，且长度不少于 opening
    return candidate[0] == marker[0] and len(candidate) >= len(marker)


class _Frame:
    """ID 栈的一帧（§6.3.4）。``child_count`` 用于生成子编号。"""

    __slots__ = ("level", "section_id", "child_count")

    def __init__(self, level: int, section_id: str) -> None:
        self.level = level
        self.section_id = section_id
        self.child_count = 0


def _sha256(text: str) -> str:
    """``sha256:`` 前缀的 UTF-8 内容摘要（§6.2）。"""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# §6.2 build_section_index
# ---------------------------------------------------------------------------
def build_section_index(source: str | bytes) -> SectionIndex:
    """把文档解析成 SectionIndex（§6.2 / §6.3）。

    Args:
        source: 解码后的文本，或原始 bytes（用于 encoding fallback 判定）。

    Returns:
        SectionIndex。``encoding_fallback=True`` 表示做过 replacement decode。
    """
    encoding_fallback = False
    if isinstance(source, bytes):
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError:
            # §6.3.5：非 UTF-8 → replacement decode，生成覆盖全文的单个 S1
            text = source.decode("utf-8", errors="replace")
            encoding_fallback = True
    else:
        text = source

    # §6.3.5：空字符串生成一个 S1 空记录。
    # 扩展到「只有空白」：这类文档既无标题也无段落，若返回空 SectionIndex，
    # 下游每个 location 都会落成 LOC_UNKNOWN_SECTION，且分块阶段拿不到任何
    # section——一个静默的退化态。S1 覆盖全文，offset 仍然有效。
    if not text.strip():
        lines = list(_iter_lines(text))
        last_line = lines[-1].number if lines else 1
        return SectionIndex(
            [
                SectionRecord(
                    section_id="S1",
                    title="",
                    heading_level=None,
                    start=0,
                    end=len(text),
                    start_line=1,
                    end_line=last_line,
                    content_sha256=_sha256(text),
                )
            ],
            encoding_fallback=encoding_fallback,
        )

    if encoding_fallback:
        lines = list(_iter_lines(text))
        last = lines[-1] if lines else _Line(1, 0, 0, 0, "")
        return SectionIndex(
            [
                SectionRecord(
                    section_id="S1",
                    title="",
                    heading_level=None,
                    start=0,
                    end=len(text),
                    start_line=1,
                    end_line=last.number,
                    content_sha256=_sha256(text),
                )
            ],
            encoding_fallback=True,
        )

    records = _parse_headings(text) or _parse_blank_line_sections(text)
    return SectionIndex(records, encoding_fallback=False)


def _parse_headings(text: str) -> list[SectionRecord]:
    """按标题切分 section（§6.3.3–§6.3.5）。无标题时返回空列表。"""
    lines = list(_iter_lines(text))

    # 1) 状态机扫出所有标题（fence 内的 `#` 一律不算）
    headings: list[tuple[int, _Line, int, str]] = []  # (level, line, start_offset, title)
    fence: str | None = None
    for line in lines:
        marker = _fence_marker(line.text)
        if fence is None:
            if marker is not None:
                fence = marker
                continue
            match = _ATX_HEADING.match(line.text)
            if match:
                headings.append(
                    (len(match.group("hashes")), line, line.start, match.group("title"))
                )
        else:
            if marker is not None and _is_fence_close(line.text, fence):
                fence = None

    if not headings:
        return []

    # 2) 分配 section_id
    stack: list[_Frame] = []
    root_counter = 1
    assigned: list[tuple[str, int, int, str, int]] = []  # (id, level, start, title, start_line)

    first_heading_start = headings[0][2]
    preamble = text[:first_heading_start]
    if preamble.strip():
        # §6.3.4：首标题前的非空文本生成 S0
        assigned.append(("S0", -1, 0, "", 1))

    for level, line, start, title in headings:
        # §6.3.4：遇到同级或更高级标题先弹出更深层
        while stack and stack[-1].level > level:
            stack.pop()

        if not stack:
            section_id = f"S{root_counter}"
            root_counter += 1
            stack.append(_Frame(level, section_id))
        elif level == stack[-1].level:
            if len(stack) == 1:
                # 根级兄弟：回到顶层编号
                section_id = f"S{root_counter}"
                root_counter += 1
                stack[-1] = _Frame(level, section_id)
            else:
                parent = stack[-2]
                parent.child_count += 1
                section_id = f"{parent.section_id}.{parent.child_count}"
                stack[-1] = _Frame(level, section_id)
        else:
            # 跳级：按实际标题顺序递增子编号，不制造不存在的中间 section
            parent = stack[-1]
            parent.child_count += 1
            section_id = f"{parent.section_id}.{parent.child_count}"
            stack.append(_Frame(level, section_id))

        assigned.append((section_id, level, start, title, line.number))

    # 3) 定终点
    #
    # 规格 §6.3.5 原文是「section 终点是下一个层级小于或等于当前标题的标题起点」。
    # 逐字实现会让 range 重叠：`#### D`（level 4）不会终止 `S2.1`（level 3），
    # 于是 S2 / S2.1 / S2.1.1 三段互相包含，同一份正文被算进三个 section。
    # 这与 §6.5「分块单位是 SectionRecord，不跨 section 合并」直接冲突——重叠会
    # 让同一段文本按祖先层级重复进入 inference，evidence 预算翻倍。
    #
    # 因此这里采用唯一自洽的读法：「层级 ≤ 当前」约束的是 **ID 栈弹出**
    # （更深标题开启新区但保留嵌套，同级/更浅才出栈）；字节 range 则一律推进到
    # 下一个**任意层级**的标题，从而得到互不重叠、完整覆盖全文的划分。
    records: list[SectionRecord] = []
    for i, (section_id, level, start, title, start_line) in enumerate(assigned):
        end = assigned[i + 1][2] if i + 1 < len(assigned) else len(text)
        end_line = _last_line_at_or_before(lines, end)
        records.append(
            SectionRecord(
                section_id=section_id,
                title=title,
                heading_level=None if level == -1 else level,
                start=start,
                end=end,
                start_line=start_line,
                end_line=end_line,
                content_sha256=_sha256(text[start:end]),
            )
        )
    return records


def _parse_blank_line_sections(text: str) -> list[SectionRecord]:
    """无标题文档按空行分段，ID 为 P1、P2…（§6.3.5）。"""
    lines = list(_iter_lines(text))
    records: list[SectionRecord] = []
    current: list[_Line] = []

    def flush(group: list[_Line]) -> None:
        if not group:
            return
        start = group[0].start
        # 终点 = 最后一行内容末尾（不含其换行符），避免把尾部空行算进 section
        end = group[-1].content_end
        records.append(
            SectionRecord(
                section_id=f"P{len(records) + 1}",
                title="",
                heading_level=None,
                start=start,
                end=end,
                start_line=group[0].number,
                end_line=group[-1].number,
                content_sha256=_sha256(text[start:end]),
            )
        )

    for line in lines:
        if line.text.strip():
            current.append(line)
        else:
            flush(current)
            current = []
    flush(current)
    return records


def _last_line_at_or_before(lines: list[_Line], offset: int) -> int:
    """返回起始 offset 严格小于 ``offset`` 的最后一行行号（闭区间终点）。

    下一标题所在行的 ``start`` 恰好等于 ``offset``，因此天然被排除。
    """
    number = 1
    for line in lines:
        if line.start < offset:
            number = line.number
        else:
            break
    return number


# ---------------------------------------------------------------------------
# §6.4 normalize_location
# ---------------------------------------------------------------------------
_SID_PATTERN: Final = r"(?:S\d+(?:\.\d+)*|P\d+)"
_BRACKET_FORM: Final = re.compile(r"^\[(?P<body>[^\]]*)\]$")
_SECTION_FORM: Final = re.compile(
    rf"^(?:section:)?(?P<sid>{_SID_PATTERN})"
    r"(?::L(?P<from>\d+)(?:\s*-\s*L?(?P<to>\d+))?)?$",
    re.IGNORECASE,
)
_TITLE_FORM: Final = re.compile(r"^(?P<title>\S.*?)[ \t]*$")


def _fail(code: str, detail: str | None = None) -> LocationResolution:
    """构造失败结果。``normalized`` 保持 None——绝不猜位置（§6.4）。"""
    message = _ERROR_MESSAGES[code]
    if detail:
        message = f"{message}：{detail}"
    return LocationResolution(
        valid=False, normalized=None, error_code=code, error_message=message
    )


def normalize_location(
    location: str | None, section_index: SectionIndex | list[SectionRecord] | None
) -> LocationResolution:
    """归一化 location（§6.4）。

    只接受四种形态：精确 section ID（``S1`` / ``S1.1`` / ``P1``）、
    带包装的同一 ID（``section:S1`` / ``[S1]``）、**唯一**匹配的完整标题、
    以及带行号的 section（``S1:L10-L20``）。

    检查顺序固定为：空 → 索引不可用 → encoding fallback → 语法 → 解析 section
    → 行号。前三项都是「输入/依赖层面的问题」，比「语法错」更根本，先报出来
    对调用方更有诊断价值。

    Args:
        location: 原始 location 字符串。
        section_index: SectionIndex；``None`` 表示不可用。

    Returns:
        LocationResolution。失败时 ``valid=False`` 且 ``normalized=None``。
    """
    if location is None or not location.strip():
        return _fail(LOC_EMPTY)

    if section_index is None:
        return _fail(LOC_INDEX_UNAVAILABLE)

    # lossy decode 出来的文本，其 offset/行号不可信，一律拒绝
    if getattr(section_index, "encoding_fallback", False):
        return _fail(LOC_ENCODING_FALLBACK)

    raw = location.strip()
    records: list[SectionRecord] = list(section_index)

    # --- 语法解析 ---
    bracket = _BRACKET_FORM.match(raw)
    if bracket:
        raw = bracket.group("body").strip()
        if not raw:
            return _fail(LOC_INVALID_FORMAT, location)

    section_match = _SECTION_FORM.match(raw)
    if section_match:
        wanted = section_match.group("sid").upper()
        target = next((r for r in records if r["section_id"].upper() == wanted), None)
        if target is None:
            return _fail(LOC_UNKNOWN_SECTION, wanted)
        from_line = section_match.group("from")
        to_line = section_match.group("to")
        if from_line is None:
            return LocationResolution(
                valid=True, normalized=target["section_id"], error_code=None, error_message=None
            )
        start_line = int(from_line)
        end_line = int(to_line) if to_line is not None else start_line
        # §6.4：行号必须落在 section 的闭区间内
        if not (target["start_line"] <= start_line <= target["end_line"]):
            return _fail(
                LOC_LINE_OUT_OF_RANGE,
                f"L{start_line} 不在 {target['section_id']} 的 "
                f"[{target['start_line']}, {target['end_line']}] 内",
            )
        if not (target["start_line"] <= end_line <= target["end_line"]):
            return _fail(
                LOC_LINE_OUT_OF_RANGE,
                f"L{end_line} 不在 {target['section_id']} 的 "
                f"[{target['start_line']}, {target['end_line']}] 内",
            )
        span = f"L{start_line}" if end_line == start_line else f"L{start_line}-L{end_line}"
        return LocationResolution(
            valid=True,
            normalized=f"{target['section_id']}:{span}",
            error_code=None,
            error_message=None,
        )

    # --- 标题匹配：必须唯一，绝不猜（§6.3.6）---
    # 显式声明了 section: 前缀却解析不出合法 section ID，是语法错而非「标题不存在」：
    # 否则 'section:XYZ' 会被当成标题去全文匹配，报出误导性的 LOC_UNKNOWN_SECTION。
    if raw.lower().startswith("section:") or (
        bracket and raw.lower().startswith("section:")
    ):
        return _fail(LOC_INVALID_FORMAT, location)

    title_match = _TITLE_FORM.match(raw)
    if not title_match:
        return _fail(LOC_INVALID_FORMAT, location)
    wanted_title = title_match.group("title")
    hits = [r for r in records if r["title"] == wanted_title]
    if not hits:
        return _fail(LOC_UNKNOWN_SECTION, f"标题 {wanted_title!r} 不存在")
    if len(hits) > 1:
        return _fail(LOC_AMBIGUOUS_TITLE, f"标题 {wanted_title!r} 出现 {len(hits)} 次")
    return LocationResolution(
        valid=True, normalized=hits[0]["section_id"], error_code=None, error_message=None
    )
