"""SectionIndex 与 LocationResolution 契约测试（规格 T-04）。

覆盖 §1642 点名的全部场景：空文档、bytes fallback、CRLF、fence opening/closing、
fence 内标题、首标题非 H1、层级跳跃、重复标题、line out of range；
外加 §6.4 七个稳定错误码的可达性。
"""

from __future__ import annotations

import hashlib

import pytest

from src.state.section_index import (
    LOC_AMBIGUOUS_TITLE,
    LOC_EMPTY,
    LOC_ENCODING_FALLBACK,
    LOC_INDEX_UNAVAILABLE,
    LOC_INVALID_FORMAT,
    LOC_LINE_OUT_OF_RANGE,
    LOC_UNKNOWN_SECTION,
    build_section_index,
    normalize_location,
)

# ---------------------------------------------------------------------------
# §6.3.4 ID 栈 golden（L941）
# ---------------------------------------------------------------------------
_GOLDEN_DOC = "## A\n\nbody a\n\n# B\n\nb\n\n### C\n\nc\n\n#### D\n\nd\n\n# E\n\ne\n"


def test_id_stack_golden() -> None:
    """§6.3.4 点名示例：首个 `## A` 仍为 S1；随后 `# B` 为 S2；`### C` 为 S2.1；
    `#### D` 为 S2.1.1；下一个 `# E` 回到 S3。"""
    records = build_section_index(_GOLDEN_DOC)
    assert [r["section_id"] for r in records] == ["S1", "S2", "S2.1", "S2.1.1", "S3"]


def test_golden_heading_levels_preserved() -> None:
    """跳级不影响原始 heading_level 的记录（§6.3.4「原始 heading_level 仍保留」）。"""
    records = {r["section_id"]: r["heading_level"] for r in build_section_index(_GOLDEN_DOC)}
    assert records == {"S1": 2, "S2": 1, "S2.1": 3, "S2.1.1": 4, "S3": 1}


def test_golden_titles_preserved() -> None:
    records = {r["section_id"]: r["title"] for r in build_section_index(_GOLDEN_DOC)}
    assert records == {"S1": "A", "S2": "B", "S2.1": "C", "S2.1.1": "D", "S3": "E"}


def test_level_skip_does_not_fabricate_intermediate_sections() -> None:
    """§6.3.4：跳级时按实际标题顺序递增子编号，不制造不存在的中间 section。

    `### C` 直接挂在 `S2` 下成为 S2.1，**不**补一个虚构的 S2 下的 level-2 section。
    """
    records = build_section_index(_GOLDEN_DOC)
    assert "S2.1.1.1" not in {r["section_id"] for r in records}
    # S2.1 的父级就是 S2，中间没有别的节点
    assert [r["section_id"] for r in records].index("S2.1") == 2


def test_first_non_h1_heading_is_root_but_keeps_level() -> None:
    """§6.3.4：首个非 H1 标题按根标题处理（→ S1），原始 heading_level 仍保留。"""
    records = build_section_index("### Deep first\n\nbody\n")
    assert len(records) == 1
    assert records[0]["section_id"] == "S1"
    assert records[0]["heading_level"] == 3
    assert records[0]["title"] == "Deep first"


def test_preamble_before_first_heading_becomes_s0() -> None:
    """§6.3.4：首标题前的非空文本生成 S0。"""
    records = build_section_index("preamble text\n\n# First\n\nx\n")
    assert [r["section_id"] for r in records] == ["S0", "S1"]
    assert records[0]["heading_level"] is None
    assert records[0]["title"] == ""


def test_whitespace_only_preamble_is_not_s0() -> None:
    """只有空白的「前导文本」不算非空文本，不应凭空多出 S0。"""
    records = build_section_index("\n\n   \n\n# First\n\nx\n")
    assert [r["section_id"] for r in records] == ["S1"]


# ---------------------------------------------------------------------------
# range 划分不变量
# ---------------------------------------------------------------------------
def test_ranges_partition_document_without_gap_or_overlap() -> None:
    """section range 必须互不重叠且完整覆盖全文。

    这是 §6.5「分块单位是 SectionRecord，不跨 section 合并」的前提：一旦重叠，
    同一段正文会按祖先层级重复进入 inference。详见 `_parse_headings` 中对
    §6.3.5 逐字读法会致重叠的说明。
    """
    records = build_section_index(_GOLDEN_DOC)
    assert records[0]["start"] == 0
    assert records[-1]["end"] == len(_GOLDEN_DOC)
    for previous, current in zip(records, records[1:], strict=False):
        assert previous["end"] == current["start"], "range 之间不得有缝隙或重叠"


def test_content_sha256_matches_slice() -> None:
    """content_sha256 是 section 文本切片的 UTF-8 摘要（§6.2）。"""
    records = build_section_index(_GOLDEN_DOC)
    for record in records:
        expected = "sha256:" + hashlib.sha256(
            _GOLDEN_DOC[record["start"] : record["end"]].encode("utf-8")
        ).hexdigest()
        assert record["content_sha256"] == expected


def test_offsets_are_code_points_and_lines_are_one_based() -> None:
    """§6.2：start/end 是 Unicode code-point offset（左闭右开），行号从 1 开始。"""
    records = build_section_index(_GOLDEN_DOC)
    assert records[0]["start_line"] == 1
    assert records[0]["start"] == 0
    for record in records:
        assert 0 <= record["start"] < record["end"] <= len(_GOLDEN_DOC)


# ---------------------------------------------------------------------------
# §6.3.5 空文档 / bytes fallback
# ---------------------------------------------------------------------------
def test_empty_document_yields_single_empty_s1() -> None:
    """§6.3.5：空字符串生成一个 S1 空记录。"""
    records = build_section_index("")
    assert len(records) == 1
    assert records[0]["section_id"] == "S1"
    assert records[0]["start"] == records[0]["end"] == 0
    assert records[0]["content_sha256"] == "sha256:" + hashlib.sha256(b"").hexdigest()


def test_bytes_non_utf8_falls_back_to_replacement_decode() -> None:
    """§6.3.5：bytes 非 UTF-8 → replacement decode，生成覆盖全文的单个 S1。"""
    raw = b"# valid heading\n\xff\xfe invalid bytes\n"
    index = build_section_index(raw)
    assert index.encoding_fallback is True
    assert len(index) == 1
    assert index[0]["section_id"] == "S1"
    assert index[0]["start"] == 0
    assert index[0]["end"] == len(raw.decode("utf-8", errors="replace"))


def test_valid_utf8_bytes_do_not_fall_back() -> None:
    """正常 UTF-8 bytes 走正常解析，不标 fallback。"""
    index = build_section_index("# 标题\n\n内容\n".encode())
    assert index.encoding_fallback is False
    assert [r["section_id"] for r in index] == ["S1"]


# ---------------------------------------------------------------------------
# §6.3.1 CRLF / LF / CR
# ---------------------------------------------------------------------------
def test_crlf_is_single_newline_boundary() -> None:
    """§6.3.1：`\\r\\n` 永不拆成两个换行边界。"""
    doc = "# A\r\nbody\r\n# B\r\n"
    records = build_section_index(doc)
    assert [r["section_id"] for r in records] == ["S1", "S2"]
    # '# A\r\n' = 5 code points：'#',' ','A','\r','\n'
    assert records[0]["start"] == 0
    assert records[0]["end"] == doc.index("# B")
    assert records[0]["start_line"] == 1
    assert records[0]["end_line"] == 2
    assert records[1]["start_line"] == 3


def test_crlf_line_count_is_not_inflated() -> None:
    """CRLF 不得让行号虚增——每个 CRLF 只算一次换行。"""
    records = build_section_index("a\r\nb\r\nc\r\n# H\r\n")
    assert records[0]["end_line"] == 3


def test_lone_cr_is_line_break() -> None:
    """§6.3.1 同时识别 CR、LF、CRLF。"""
    doc = "# A\rbody\r# B\r"
    records = build_section_index(doc)
    assert [r["section_id"] for r in records] == ["S1", "S2"]
    assert records[0]["end_line"] == 2
    assert records[1]["start_line"] == 3


def test_mixed_line_endings() -> None:
    """三种换行混用也必须正确切分。"""
    doc = "# A\r\nbody\nmore\r# B\n"
    records = build_section_index(doc)
    assert [r["section_id"] for r in records] == ["S1", "S2"]
    assert records[1]["start"] == doc.index("# B")


def test_no_trailing_empty_line_inflation() -> None:
    """以换行符结尾的文档不得多出尾部空行（否则 end_line 虚增）。"""
    records = build_section_index("# A\nbody\n")
    assert records[0]["start_line"] == 1
    assert records[0]["end_line"] == 2


# ---------------------------------------------------------------------------
# §6.3.2–§6.3.3 fence 状态机
# ---------------------------------------------------------------------------
def test_heading_inside_backtick_fence_is_plain_text() -> None:
    """§6.3.3：fence 内所有 `#` 行按普通文本处理，不产生 section。

    以 `# Real` 起头，避免 fence 前的非空文本被正确地归为 S0 而干扰断言。
    """
    doc = "# Real\n\n```python\n# not a heading\n## also not\n```\n\n# After\n"
    records = build_section_index(doc)
    assert [r["title"] for r in records] == ["Real", "After"]


def test_heading_inside_tilde_fence_is_plain_text() -> None:
    """波浪线 fence 同样屏蔽标题。"""
    doc = "# Real\n\n~~~\n# hidden\n~~~\n# Visible\n"
    records = build_section_index(doc)
    assert [r["title"] for r in records] == ["Real", "Visible"]


def test_fence_close_requires_same_marker_class() -> None:
    """§6.3.2：closing fence 必须是同类字符——``` 不能被 ~~~ 关闭。"""
    doc = "# Real\n\n```\n~~~\n# still inside\n```\n# Outside\n"
    records = build_section_index(doc)
    # '~~~' 未关闭 fence，故 '# still inside' 仍是普通文本
    assert [r["title"] for r in records] == ["Real", "Outside"]


def test_fence_close_may_be_longer_than_open() -> None:
    """§6.3.2：closing fence 长度 ≥ opening。"""
    doc = "# Real\n\n```\ncode\n`````\n# Outside\n"
    records = build_section_index(doc)
    assert [r["title"] for r in records] == ["Real", "Outside"]


def test_shorter_fence_does_not_close_longer_open() -> None:
    """§6.3.2：closing fence 不得短于 opening。

    这是「完整 marker 长度」必须被记录的原因：4 反引号开栏后，3 反引号不能关闭它。
    """
    doc = "# Real\n\n````\ncode\n```\n# still inside\n````\n# Outside\n"
    records = build_section_index(doc)
    assert [r["title"] for r in records] == ["Real", "Outside"]


def test_fence_close_allows_up_to_three_spaces_indent() -> None:
    """§6.3.2：closing fence 缩进不超过三个空格。"""
    doc = "# Real\n\n```\ncode\n   ```\n# Outside\n"
    records = build_section_index(doc)
    assert [r["title"] for r in records] == ["Real", "Outside"]


def test_fence_with_info_string_opens() -> None:
    """§6.3.2：opening fence 其后可有 language/info 文本。"""
    doc = "# Real\n\n~~~python\n# hidden\n~~~\n# Outside\n"
    records = build_section_index(doc)
    assert [r["title"] for r in records] == ["Real", "Outside"]


def test_indented_four_spaces_is_code_not_fence() -> None:
    """四个空格缩进不是 fence（§6.3.2「最多三个空格」）。"""
    doc = "# Real\n\n    ```\n# Outside\n"
    records = build_section_index(doc)
    assert [r["title"] for r in records] == ["Real", "Outside"]


def test_unclosed_fence_swallows_rest_of_document() -> None:
    """未闭合的 fence 必须把后续 `#` 全部当普通文本，否则会漏判标题。"""
    doc = "# Real\n\n```\n# a\n# b\n"
    records = build_section_index(doc)
    assert [r["title"] for r in records] == ["Real"]


def test_fence_content_before_first_heading_becomes_s0() -> None:
    """fence 本身也是非空文本，位于首标题前时应归为 S0（§6.3.4）。"""
    doc = "```\ncode\n```\n# First\n"
    records = build_section_index(doc)
    assert [r["section_id"] for r in records] == ["S0", "S1"]


# ---------------------------------------------------------------------------
# §6.3.4 ATX 标题边界
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("line", ["#", "#no-space", "####### seven", "    # indented"])
def test_non_heading_lines_are_not_headings(line: str) -> None:
    """§6.3.4：必须同时满足「≤3 空格 + 1~6 个 # + ≥1 空白 + 标题文本」。"""
    doc = f"# Real\n\n{line}\n"
    records = build_section_index(doc)
    assert [r["title"] for r in records] == ["Real"]


def test_setext_underline_is_not_atx() -> None:
    """`===`/`---` 下划线式标题不在 ATX 范围内，不应被当作标题。"""
    doc = "# Real\n\nSetext\n======\n"
    records = build_section_index(doc)
    assert [r["title"] for r in records] == ["Real"]


# ---------------------------------------------------------------------------
# §6.3.5 无标题文档按空行分段
# ---------------------------------------------------------------------------
def test_headingless_document_splits_on_blank_lines() -> None:
    """§6.3.5：无标题文档按空行分段，ID 为 P1、P2。"""
    doc = "first para\n\nsecond para\n\nthird para\n"
    records = build_section_index(doc)
    assert [r["section_id"] for r in records] == ["P1", "P2", "P3"]
    assert all(r["heading_level"] is None for r in records)
    assert all(r["title"] == "" for r in records)


def test_p_sections_have_no_empty_trailing_segment() -> None:
    """尾部空行不得单独凑出一个 P 段。"""
    doc = "only para\n\n\n"
    assert [r["section_id"] for r in build_section_index(doc)] == ["P1"]


def test_whitespace_only_document_yields_single_s1() -> None:
    """只有空白的文档给出单个 S1，而非空 SectionIndex 或 P 段。

    规格只规定了「空字符串 → S1」。纯空白文档同样既无标题也无段落：返回 `[]`
    会让下游每个 location 都落成 LOC_UNKNOWN_SECTION，分块阶段也拿不到任何
    section——静默退化态。故按同一条规则处理，S1 覆盖全文，offset 仍有效。
    """
    doc = "\n\n\n"
    records = build_section_index(doc)
    assert len(records) == 1
    assert records[0]["section_id"] == "S1"
    assert records[0]["start"] == 0
    assert records[0]["end"] == len(doc)
    assert records[0]["heading_level"] is None
    assert records[0]["content_sha256"] == "sha256:" + hashlib.sha256(doc.encode()).hexdigest()


# ---------------------------------------------------------------------------
# §6.4 normalize_location：四种合法形态
# ---------------------------------------------------------------------------
_INDEX_DOC = "# Alpha\n\nbody a\n\n## Beta\n\nbody b\n\n# Gamma\n\nbody c\n"


@pytest.mark.parametrize(
    "location,expected",
    [
        ("S1", "S1"),
        ("S1.1", "S1.1"),
        ("section:S1", "S1"),
        ("[S1]", "S1"),
    ],
)
def test_accepted_section_id_forms(location: str, expected: str) -> None:
    """§6.4：精确 `S1`/`S1.1`/`P1` 与 `section:S1`、`[S1]` 包装形式都被接受。

    `_INDEX_DOC` 中 `# Alpha` 是 S1、`## Beta` 是其子级 S1.1，故 `S1.1` 归一化到
    自身而非 S1——包装形式（`section:` / `[]`）才折叠到裸 ID。
    """
    resolution = normalize_location(location, build_section_index(_INDEX_DOC))
    assert resolution["valid"] is True
    assert resolution["error_code"] is None
    assert resolution["normalized"] == expected


def test_accepted_unique_full_title() -> None:
    """§6.4：唯一匹配的完整标题归一化为 section ID。"""
    resolution = normalize_location("Alpha", build_section_index(_INDEX_DOC))
    assert resolution["valid"] is True
    assert resolution["normalized"] == "S1"


def test_accepted_section_with_line_range() -> None:
    """§6.4：`S1:L10-L20` 形态，行号在区间内则有效。"""
    index = build_section_index(_INDEX_DOC)
    resolution = normalize_location("S1:L1-L2", index)
    assert resolution["valid"] is True
    assert resolution["normalized"] == "S1:L1-L2"


def test_single_line_form_normalizes() -> None:
    index = build_section_index(_INDEX_DOC)
    assert normalize_location("S1:L2", index)["normalized"] == "S1:L2"


def test_section_id_is_case_insensitive_and_normalized() -> None:
    """大小写差异属纯语法归一化，不是「猜测」。"""
    assert normalize_location("s1", build_section_index(_INDEX_DOC))["normalized"] == "S1"


def test_p_section_id_accepted() -> None:
    """§6.4：`P1` 是合法 ID。"""
    index = build_section_index("alpha\n\nbeta\n")
    assert normalize_location("P2", index)["normalized"] == "P2"


# ---------------------------------------------------------------------------
# §6.4 七个稳定错误码
# ---------------------------------------------------------------------------
def test_loc_empty() -> None:
    """location 为空。"""
    index = build_section_index(_INDEX_DOC)
    for blank in ("", "   ", None):
        resolution = normalize_location(blank, index)
        assert resolution["valid"] is False
        assert resolution["error_code"] == LOC_EMPTY
        assert resolution["normalized"] is None


def test_loc_index_unavailable() -> None:
    """`section_index=None`。"""
    resolution = normalize_location("S1", None)
    assert resolution["valid"] is False
    assert resolution["error_code"] == LOC_INDEX_UNAVAILABLE


def test_loc_encoding_fallback() -> None:
    """bytes 非 UTF-8 已走全文 fallback 时，位置不可信。"""
    index = build_section_index(b"# heading\n\xff\xfe\n")
    resolution = normalize_location("S1", index)
    assert resolution["valid"] is False
    assert resolution["error_code"] == LOC_ENCODING_FALLBACK


def test_loc_invalid_format_for_malformed_section_ref() -> None:
    """显式 `section:` 前缀却解析不出合法 ID → 语法错，而非标题不存在。"""
    resolution = normalize_location("section:XYZ", build_section_index(_INDEX_DOC))
    assert resolution["valid"] is False
    assert resolution["error_code"] == LOC_INVALID_FORMAT


def test_loc_invalid_format_for_empty_bracket_body() -> None:
    """`[]` 去掉括号后为空体。"""
    resolution = normalize_location("[]", build_section_index(_INDEX_DOC))
    assert resolution["valid"] is False
    assert resolution["error_code"] == LOC_INVALID_FORMAT


def test_loc_unknown_section() -> None:
    """section ID 不存在。"""
    resolution = normalize_location("S999", build_section_index(_INDEX_DOC))
    assert resolution["valid"] is False
    assert resolution["error_code"] == LOC_UNKNOWN_SECTION


def test_loc_unknown_title() -> None:
    """完整标题不存在时同样归为 section 不存在。"""
    resolution = normalize_location("No Such Title", build_section_index(_INDEX_DOC))
    assert resolution["valid"] is False
    assert resolution["error_code"] == LOC_UNKNOWN_SECTION


def test_loc_ambiguous_title() -> None:
    """§6.3.6 / §6.4：标题重复时绝不猜测，报 LOC_AMBIGUOUS_TITLE。"""
    index = build_section_index("# Same\n\na\n\n# Same\n\nb\n")
    assert [r["section_id"] for r in index] == ["S1", "S2"]
    resolution = normalize_location("Same", index)
    assert resolution["valid"] is False
    assert resolution["error_code"] == LOC_AMBIGUOUS_TITLE
    assert resolution["normalized"] is None


def test_duplicate_title_still_addressable_by_id() -> None:
    """标题重复不影响按 ID 定位——绝不猜测 ≠ 不可定位。"""
    index = build_section_index("# Same\n\na\n\n# Same\n\nb\n")
    assert normalize_location("S1", index)["normalized"] == "S1"
    assert normalize_location("S2", index)["normalized"] == "S2"


def test_loc_line_out_of_range() -> None:
    """行号不在 section 闭区间内。"""
    index = build_section_index(_INDEX_DOC)
    resolution = normalize_location("S1:L9-L10", index)
    assert resolution["valid"] is False
    assert resolution["error_code"] == LOC_LINE_OUT_OF_RANGE


def test_loc_line_out_of_range_for_end_bound() -> None:
    """闭区间上界越界同样报错。"""
    index = build_section_index(_INDEX_DOC)
    resolution = normalize_location("S1:L1-L99", index)
    assert resolution["valid"] is False
    assert resolution["error_code"] == LOC_LINE_OUT_OF_RANGE


def test_line_at_section_boundary_is_valid() -> None:
    """闭区间端点必须被接受。"""
    index = build_section_index(_INDEX_DOC)
    resolution = normalize_location("S1:L1-L3", index)
    assert resolution["valid"] is True
    assert resolution["normalized"] == "S1:L1-L3"


def test_all_seven_error_codes_are_reachable() -> None:
    """§6.4 七个错误码必须都可达，避免出现永不触发的「防御性」错误码。"""
    index = build_section_index(_INDEX_DOC)
    observed = {
        normalize_location("", index)["error_code"],
        normalize_location("section:XYZ", index)["error_code"],
        normalize_location("S999", index)["error_code"],
        normalize_location("Same", build_section_index("# Same\n\na\n\n# Same\n\nb\n"))[
            "error_code"
        ],
        normalize_location("S1:L99", index)["error_code"],
        normalize_location("S1", None)["error_code"],
        normalize_location("S1", build_section_index(b"# h\n\xff\n"))["error_code"],
    }
    assert observed == {
        LOC_EMPTY,
        LOC_INVALID_FORMAT,
        LOC_UNKNOWN_SECTION,
        LOC_AMBIGUOUS_TITLE,
        LOC_LINE_OUT_OF_RANGE,
        LOC_INDEX_UNAVAILABLE,
        LOC_ENCODING_FALLBACK,
    }


def test_failure_never_invents_a_location() -> None:
    """§6.4：失败时保留原始 location，只写错误字段；不让 Laya 猜位置。"""
    index = build_section_index(_INDEX_DOC)
    for bad in ("S999", "Same", "S1:L99", "section:XYZ"):
        resolution = normalize_location(bad, index)
        assert resolution["valid"] is False
        assert resolution["normalized"] is None, f"{bad} 不应产出 normalized"
        assert resolution["error_message"], f"{bad} 必须带可诊断的错误信息"


def test_plain_list_index_is_accepted() -> None:
    """普通 list[SectionRecord] 也应可用（SectionIndex 只是加了标记的 list）。"""
    records = list(build_section_index(_INDEX_DOC))
    assert normalize_location("S1", records)["valid"] is True
