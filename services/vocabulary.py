"""个人词库:人名、产品名、术语总被识别错,是语音输入最常见的抱怨。

一条一行,两种写法:

- ``石枫``:**热词**。作为上下文交给识别模型(Qwen3-ASR 的 system prompt、
  mlx-whisper 的 initial_prompt),让它在同音字之间偏向这个写法;也交给 LLM,
  叫它别把这个词「改正」掉。本机实测:Qwen3-ASR 把「石峰」认成了词库里的「石枫」。
- ``陶睿 => Tauri``:**替换规则**。识别完之后按字面替换,确定性的。热词只能「偏向」,
  模型没听出来的(中文语境里说英文产品名)还得靠它兜底。``->`` 和 ``→`` 也认。

transformers 的 Whisper 没接上下文(要把提示编码成 prompt_ids,和版本耦合较深),
只有替换规则和 LLM 提示对它生效。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 给识别模型的上下文不宜太长:它和音频共用上下文窗口,太长还会拖慢首字。
MAX_CONTEXT_CHARS = 400
#: 一次最多存多少条,防止误粘贴一大段文本进来。
MAX_ENTRIES = 200

_RULE = re.compile(r"^(.+?)\s*(?:=>|->|→)\s*(.+)$")


@dataclass
class Vocabulary:
    hotwords: list[str] = field(default_factory=list)
    #: (识别出来的错写, 应该的写法)
    rules: list[tuple[str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.hotwords and not self.rules


def parse(entries: list[str]) -> Vocabulary:
    """把用户写的若干行解析成热词和替换规则。空行、重复项、只有一边的规则都跳过。"""
    vocab = Vocabulary()
    seen: set[str] = set()
    for raw in entries[:MAX_ENTRIES]:
        line = (raw or "").strip()
        if not line or line in seen:
            continue
        seen.add(line)
        m = _RULE.match(line)
        if m:
            wrong, right = m.group(1).strip(), m.group(2).strip()
            if wrong and right and wrong != right:
                vocab.rules.append((wrong, right))
        else:
            vocab.hotwords.append(line)
    return vocab


def context_text(vocab: Vocabulary) -> str | None:
    """交给识别模型的上下文:热词加上规则的正确写法。"""
    words: list[str] = []
    for w in [*vocab.hotwords, *(right for _, right in vocab.rules)]:
        if w not in words:
            words.append(w)
    if not words:
        return None
    text = ""
    for w in words:
        candidate = f"{text}、{w}" if text else w
        if len(candidate) > MAX_CONTEXT_CHARS:
            break
        text = candidate
    return text or None


def apply_rules(text: str, vocab: Vocabulary) -> str:
    """按替换规则改写识别结果。长的错写先换,免得短规则把长规则的一部分先吃掉。"""
    for wrong, right in sorted(vocab.rules, key=lambda r: len(r[0]), reverse=True):
        text = text.replace(wrong, right)
    return text


def llm_hint(vocab: Vocabulary) -> str | None:
    """给 LLM 的一句提示:这些词照这个写法写,别「纠正」。"""
    ctx = context_text(vocab)
    if not ctx:
        return None
    return f"以下专有名词请严格保持这样的写法,不要改动:{ctx}"
