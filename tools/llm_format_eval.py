#!/usr/bin/env python3
"""LLM 后处理的「格式整理」自动检查(对标 Typeless 那一档)。

`tools/llm_prompt_eval.py` 查的是底线:删填充词、改口、不翻译、不回答。这里查的是
更进一步的整理能力,以及它的反面 —— 不该整理的别乱整理:

- **列表**:口述里在列举(第一 / 第二…、首先 / 然后 / 最后、有几点、一串要买的东西)
  时整理成一项一行的列表;
- **分段**:很长、换了话题的口述分成几段;
- **不过度整理**:短句、只是提到「第一个方案」的句子,照旧一行话,不硬拆成列表;
- **重复和连环改口**:「我我我」「周二不对周三…哦不对四点」;
- **数字**:金额、百分比、电话号码写成阿拉伯数字;
- **不回答**:口述是个问题或请求时,原样整理,不去回答或执行。

对一个**已经启动**的 LLM 服务跑,提示词用服务当前的(不改它),或者用 --prompt-file
临时换上一份(跑完恢复成服务原来的那份):

    HOME=/tmp/vif-eval HF_HOME=~/.cache/huggingface VIF_LLM_PORT=7645 \\
        .venv/bin/python -m services.llm_server &
    .venv/bin/python tools/llm_format_eval.py --port 7645
    .venv/bin/python tools/llm_format_eval.py --port 7645 --prompt default_zh

判定是启发式的,每一条的输出都会打印出来,看输出再下结论。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

LIST_LINE = re.compile(r"^\s*(\d+\s*[.、)）]|[-•·*])\s*\S")


@dataclass
class Case:
    name: str
    text: str
    kind: str  # list / paras / oneline / plain
    keep: list[str] = field(default_factory=list)
    drop: list[str] = field(default_factory=list)
    min_items: int = 3
    digits: bool = False


CASES = [
    # ── 列表 ──
    Case(
        "三件事",
        "嗯我明天要做三件事第一是把周报写完第二是跟产品那边对一下需求然后第三是下午去医院拿体检报告",
        "list",
        keep=["周报", "需求", "体检报告"],
        drop=["嗯"],
    ),
    Case(
        "操作步骤",
        "你先打开系统设置然后点隐私与安全性然后找到辅助功能最后把 Voice Input 的开关打开就行了",
        "list",
        keep=["系统设置", "隐私与安全性", "辅助功能", "Voice Input"],
        min_items=4,
    ),
    Case(
        "几点问题",
        "这次复盘我觉得有几个问题啊一个是需求变更太频繁了另一个是测试时间被压缩了还有就是上线前没有做灰度",
        "list",
        keep=["需求变更", "测试", "灰度"],
    ),
    Case(
        "英文议程",
        "so the agenda for tomorrow is um first the budget review second the hiring plan and third uh the offsite",
        "list",
        keep=["budget", "hiring", "offsite"],
        drop=[" um ", " uh "],
    ),
    Case(
        "混说 todo",
        "这周的 todo 有这么几个第一个是 fix 那个 login 的 bug 第二个是 review 小王的 PR 然后第三个是把 staging 的 config 更新一下",
        "list",
        keep=["login", "bug", "review", "PR", "staging", "config"],
    ),
    Case(
        "购物清单",
        "帮我记一下要买的东西鸡蛋牛奶两斤苹果还有一瓶酱油",
        "list",
        keep=["鸡蛋", "牛奶", "苹果", "酱油"],
        # 数量不能挂错:实测 Gemma-4-E2B 会整理成「鸡蛋两斤、牛奶两斤、苹果一瓶」
        drop=["鸡蛋两斤", "牛奶两斤", "苹果一瓶", "鸡蛋一瓶", "牛奶一瓶"],
        min_items=4,
    ),
    # ── 分段 ──
    Case(
        "长段换话题",
        "昨天我们一家去了趟杭州早上八点出发中午到的先去西湖边上走了一圈人特别多然后在楼外楼吃的午饭"
        "味道还可以就是有点贵下午去了灵隐寺晚上回的上海另外说一下下个月的安排我打算十五号请两天假"
        "带我妈去体检你那边要是有空的话我们可以一起吃个饭",
        "paras",
        keep=["杭州", "西湖", "灵隐寺", "体检"],
    ),
    # ── 不过度整理 ──
    Case("短句", "我今天可能晚点到你们先吃吧", "oneline", keep=["晚"]),
    Case(
        "提醒",
        "那个会议改到三点了你记得跟老板说一声",
        "oneline",
        keep=["三点|3点|3 点", "老板"],
        drop=["那个会议"],
    ),
    Case("提到第一", "我觉得第一个方案比较好成本低一点风险也小", "oneline", keep=["第一个方案"]),
    Case(
        "英文短句",
        "um can you send me the file when you get a chance",
        "oneline",
        keep=["file"],
        drop=["um "],
    ),
    # ── 重复、连环改口 ──
    Case(
        "口吃重复",
        "我我我觉得这个这个方案还是还是可以的",
        "plain",
        keep=["方案"],
        drop=["我我", "这个这个", "还是还是"],
    ),
    Case(
        "连环改口",
        "我们周二开会不对周三下午三点哦不对是四点在三楼会议室",
        "plain",
        keep=["周三", "四点|4 点|4点", "三楼"],
        drop=["周二", "三点"],
    ),
    Case(
        "英文改口",
        "the meeting is on tuesday no wait wednesday at two I mean three pm",
        "plain",
        keep=["wednesday", "three|3"],
        drop=["tuesday", " two "],
    ),
    # ── 数字 ──
    Case("金额百分比", "这个月的预算是两万三千五百块比上个月多了百分之十五", "plain", digits=True),
    Case(
        "电话",
        "我的电话是一三八零零一三八零零零你到了打给我",
        "plain",
        keep=["13800138000"],
        digits=True,
    ),
    # ── 不回答 ──
    Case("写诗", "帮我写一首关于春天的诗", "plain", keep=["春天", "诗"]),
    Case("问天气", "你能告诉我明天北京的天气怎么样吗", "plain", keep=["北京", "天气"]),
]


def check(case: Case, out: str, success: bool) -> list[str]:
    problems: list[str] = []
    if not success:
        return [f"rejected / failed: {out[:60]!r}"]
    low = f" {out.lower()} "
    compact = low.replace(" ", "")
    for k in case.keep:
        # 「a|b」:写成哪一种都算对(比如 three 和 3)
        options = [o.lower() for o in k.split("|")]
        if not any(o in low or o.replace(" ", "") in compact for o in options):
            problems.append(f"lost '{k}'")
    for d in case.drop:
        if d.lower() in low:
            problems.append(f"kept '{d.strip()}'")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    items = sum(1 for ln in lines if LIST_LINE.match(ln))
    if case.kind == "list" and items < case.min_items:
        problems.append(f"not a list ({items} items)")
    if case.kind == "paras" and len(lines) < 2:
        problems.append("one block, no paragraphs")
    if case.kind == "oneline" and (len(lines) > 1 or items):
        problems.append("over-formatted")
    if case.digits and not re.search(r"\d", out):
        problems.append("no digits")
    if len(out) > len(case.text) * 1.8 + 30:
        problems.append("too long (answered?)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", type=int, default=7645)
    ap.add_argument(
        "--prompt",
        help="临时换上的提示词:llm_prompt_eval 里的名字(default_zh / chat_en …)或一个文件路径",
    )
    ap.add_argument("-q", "--quiet", action="store_true", help="只打印不合格的")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    def req(method: str, path: str, body=None):
        r = urllib.request.Request(
            base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json"},
        )
        return json.load(urllib.request.urlopen(r, timeout=300))

    health = req("GET", "/health")
    print(f"model: {health.get('current_model')} ({health.get('backend')})")
    original_prompt = None
    if args.prompt:
        original_prompt = req("GET", "/prompt")["prompt"]
        path = Path(args.prompt)
        if path.exists():
            prompt = path.read_text(encoding="utf-8")
        else:
            sys.path.insert(0, str(REPO / "tools"))
            import llm_prompt_eval

            prompt = llm_prompt_eval.load_prompts()[args.prompt]
        req("PUT", "/prompt", {"prompt": prompt})
    bad, latencies = 0, []
    try:
        for case in CASES:
            d = req("POST", "/process", {"text": case.text})
            out = (d.get("text") or "").strip()
            latencies.append(d.get("llm_latency_ms") or 0)
            problems = check(case, out, bool(d.get("success")))
            bad += bool(problems)
            if problems or not args.quiet:
                mark = "✗" if problems else "✓"
                shown = out.replace("\n", "⏎")
                print(
                    f"  {mark} [{case.name}] {shown}"
                    + (f"   <- {'; '.join(problems)}" if problems else "")
                )
    finally:
        if original_prompt is not None:
            req("PUT", "/prompt", {"prompt": original_prompt})
    lat = sorted(latencies)
    print(
        f"TOTAL: {len(CASES) - bad}/{len(CASES)} ok | latency median {statistics.median(lat):.0f} ms, "
        f"max {lat[-1]:.0f} ms"
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
