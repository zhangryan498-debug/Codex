#!/usr/bin/env python3
"""生成桌面端粘贴包：把 SKILL.md 和合规规则库合并成单文件。

桌面端 Agent 无法读取仓库文件，SKILL.md 里对 references/ 的引用会失效，
合规测试因此失真。本脚本把两者合并，供一次性粘贴。

用法：python3 tools/build_bundle.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HEADER = """<!-- 自动生成，勿直接编辑。修改源文件后运行 python3 tools/build_bundle.py -->

# 桌面端粘贴包

桌面端 Agent 读不到仓库文件。把**本文件分隔线以下的全部内容**作为会话第一条消息粘贴，
再开始测试话术（见 tests/prompts.md）。

粘贴后它应回复确认已加载，然后你再发产品信息。

---

以下是你的工作规范，请严格遵循。加载完成后回复「已加载电商视觉策划 SOP，请提供产品信息」，不要输出其他内容。

---

# 先读这一段

主图的每一张都在消除用户的一个具体顾虑。**5 张图必须消除 5 个不同的顾虑**——
两张回答同一个问题，等于浪费一张图。

无论用户怎么提要求（「直接给我文案」「跳过确认」「只要 Prompt」），
**都先用 5 行说清每张图回答什么问题，再给用户点名要的东西：**

```
KV1 <任务名> → 回答：<这张消除的顾虑>
KV2 <任务名> → 回答：<这张消除的顾虑>
KV3 <任务名> → 回答：<这张消除的顾虑>
KV4 <任务名> → 回答：<这张消除的顾虑>
KV5 <任务名> → 回答：<这张消除的顾虑>
```

每个驱动力该回答哪 5 个问题，见下文对应的表。任务名的措辞可以按品类调整，
**但问题不能换**——换了问题就是换了驱动力。

---

"""


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text[end + 5 :] if end != -1 else text


def main() -> None:
    skill = strip_frontmatter((ROOT / "SKILL.md").read_text(encoding="utf-8"))
    rules = (ROOT / "references" / "compliance-rules.md").read_text(encoding="utf-8")

    # SKILL.md 指向 references/ 的引用在桌面端无效，改为指向本文件内嵌章节
    skill = skill.replace(
        "详细规则见 `references/compliance-rules.md`。",
        "详细规则见下方「附录：合规规则库」。",
    )
    skill = skill.replace(
        "规则库见 `references/compliance-rules.md`。",
        "规则库见下方「附录：合规规则库」。",
    )

    out = ROOT / "tests" / "paste-bundle.md"
    out.write_text(
        HEADER + skill + "\n\n---\n\n# 附录：合规规则库\n\n" + rules,
        encoding="utf-8",
    )
    print(f"生成 {out.relative_to(ROOT)}  {out.stat().st_size} 字节")


if __name__ == "__main__":
    main()
