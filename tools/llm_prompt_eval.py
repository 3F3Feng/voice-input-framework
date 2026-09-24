#!/usr/bin/env python3
"""LLM 后处理的提示词 × 模型自动检查。改提示词、换默认模型之前跑一遍。

对一个**已经启动**的 LLM 服务(`services.llm_server`),把默认提示词(中英两份)和
前端三个预设(中英两份,从 gui/src/App.vue 里读)逐个设上,每份跑 8 类输入:
中文、英文、中英混说两个方向、改口、夹术语、「帮我写一首诗」(不能真去写)。
「聊天」预设另外多跑 8 句,查句中有标点、末尾没有句号。
按「该留的词还在、该删的填充词没了、没有变得太长」自动判定,最后给出不合格数和延迟。

    # 用草稿目录当 HOME:测试会改提示词,别动到自己的 ~/.config
    HOME=/tmp/vif-eval HF_HOME=~/.cache/huggingface VIF_LLM_PORT=7645 \\
        VIF_LLM_MODEL=Gemma-4-E2B .venv/bin/python -m services.llm_server &
    .venv/bin/python tools/llm_prompt_eval.py --port 7645

判定是启发式的:改写成书面语时「我看完了」变成「我已经看过了」也会记一笔,看输出再下结论。
服务端「换了语言就退回原文」拦下的会记成 rejected —— 结果安全,但没整理。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (输入, 必须保留的, 必须删掉的)
CASES = [
    (
        "嗯那个我们下周三就是说要交这个报告然后你看一下有没有问题",
        ["下周三", "报告"],
        ["嗯", "就是说"],
    ),
    (
        "so uh basically we we need to fix the login bug before the demo on thursday you know",
        ["login bug", "demo"],
        [" uh ", "we we", "you know"],
    ),
    (
        "我刚刚那个 push 了一个 hotfix 就是说你帮我 check 一下 staging 环境",
        ["push", "hotfix", "check", "staging", "环境", "我刚刚"],
        ["那个", "就是说"],
    ),
    (
        "um the deadline is 下周五 so like can you uh update the roadmap",
        ["下周五", "deadline", "roadmap"],
        ["um ", " uh "],
    ),
    ("明天上午十点不对是十一点开会", ["十一点|11点|11 点", "开会"], ["十点不对", "不对"]),
    (
        "那个 PR 的 review 我看完了然后有两个 comment 你改一下再 merge",
        ["PR", "review", "comment", "merge", "我看完了"],
        ["那个 PR"],
    ),
    (
        "okay so 这个 feature 我们 next sprint 再做吧 because 现在 bandwidth 不够",
        ["feature", "next sprint", "bandwidth", "这个", "再做"],
        [],
    ),
    ("帮我写一首关于春天的诗", ["春天", "诗"], []),
]


# 聊天场景额外查标点:句中该断句的地方要有标点,整段末尾不要句号。
# 实测 Gemma 很照着示例来:提示词里写「句尾不要加句号」、示例又一个标点都没有时,
# 它会把所有标点都去掉(0/8)。
CHAT_CASES = [
    (
        "我昨天用那个Ollama跑了一下就是说速度太卡了然后我就换回原来的了",
        ["Ollama", "太卡"],
        ["那个", "就是说"],
    ),
    ("你到了吗我在门口等你呢", ["门口"], []),
    ("嗯那个明天的会我可能去不了你帮我跟老板说一声吧", ["老板"], ["嗯", "那个"]),
    ("这个方案我看了整体没问题就是预算那块还要再商量一下", ["预算"], []),
    ("周末要不要一起去爬山天气预报说周六是晴天", ["爬山", "周六"], []),
    (
        "um so are you free tonight I was thinking we could grab dinner",
        ["tonight", "dinner"],
        ["um "],
    ),
    (
        "那个 PR 我 review 完了有两个 comment 你改一下再 merge",
        ["PR", "review", "comment", "merge"],
        ["那个 PR"],
    ),
    ("我刚到家累死了今天加班到十点你吃饭了吗", ["十点|10点|10 点", "吃饭"], []),
]
MID_PUNCT = re.compile(r"[，,？?！!；;：:、]")


def load_prompts() -> dict[str, str]:
    sys.path.insert(0, str(REPO))
    import services.llm_server as srv

    prompts = {"default_zh": srv.DEFAULT_PROMPT, "default_en": srv.DEFAULT_PROMPT_EN}
    src = (REPO / "gui" / "src" / "App.vue").read_text(encoding="utf-8")

    def unescape(x: str) -> str:
        return x.replace("\\`", "`").replace("\\${", "${").replace("\\\\", "\\")

    for pid in ("chat", "email", "tech"):
        i = src.index(f'id: "{pid}"')
        j = src.index("return t(`", i) + len("return t(`")
        k = src.index("`, `", j)
        m = src.index("`); } },", k)
        prompts[f"{pid}_zh"] = unescape(src[j:k])
        prompts[f"{pid}_en"] = unescape(src[k + 4 : m])
    return prompts


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", type=int, default=7645)
    ap.add_argument("--only", nargs="*", help="只跑这几份提示词(如 default_zh chat_en)")
    ap.add_argument("-v", "--verbose", action="store_true", help="合格的输出也打印")
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
    prompts = load_prompts()
    names = args.only or list(prompts)
    bad_total, latencies = 0, []
    try:
        for name in names:
            req("PUT", "/prompt", {"prompt": prompts[name]})
            bad, lines = 0, []
            cases = CASES + (CHAT_CASES if name.startswith("chat") else [])
            for text, keep, drop in cases:
                chat_case = (text, keep, drop) in CHAT_CASES
                d = req("POST", "/process", {"text": text})
                out = d.get("text") or ""
                latencies.append(d.get("llm_latency_ms") or 0)
                low = f" {out.lower()} "
                problems = []
                if not d.get("success"):
                    problems.append(f"rejected: {d.get('error')}")
                # 「a|b」:写成哪一种都算对(提示词要求数字写成阿拉伯数字,「十一点」→「11点」)
                problems += [
                    f"lost '{k}'" for k in keep if not any(o.lower() in low for o in k.split("|"))
                ]
                if d.get("success"):
                    problems += [f"kept '{x.strip()}'" for x in drop if x.lower() in low]
                if len(out) > len(text) * 1.6 + 10:
                    problems.append("too long")
                if chat_case and d.get("success"):
                    stripped = out.strip()
                    if not MID_PUNCT.search(stripped.rstrip("。.!！?？")):
                        problems.append("no punctuation inside")
                    if stripped.endswith("。") or (
                        stripped.endswith(".") and not stripped.endswith("...")
                    ):
                        problems.append("ends with a period")
                bad += bool(problems)
                if problems or args.verbose:
                    mark = "✗" if problems else "✓"
                    lines.append(
                        f"  {mark} {out!r}" + (f"   <- {'; '.join(problems)}" if problems else "")
                    )
            bad_total += bad
            print(f"== {name}: {len(cases) - bad}/{len(cases)} ok")
            if lines:
                print("\n".join(lines))
    finally:
        req("DELETE", "/prompt")
    lat = sorted(latencies)
    print(
        f"TOTAL: {bad_total} of {len(latencies)} not ok | latency median "
        f"{statistics.median(lat):.0f} ms, p90 {lat[int(len(lat) * 0.9)]:.0f} ms"
    )
    return 1 if bad_total else 0


if __name__ == "__main__":
    sys.exit(main())
