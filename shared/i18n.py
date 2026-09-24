"""服务端给用户看的提示,按客户端的界面语言回中文或英文(F22)。

客户端在每个 HTTP 请求和 WebSocket 握手上带 `Accept-Language: zh` / `en`。没带、
或者带的是别的语言(老客户端、curl、别的调用方),一律回中文——和以前完全一样。

和前端(gui/src/i18n.ts)同一个约定:只有两种语言,不搞键值表,每处文案就地写成
``t(lang, "中文", "English")``。

有些提示是在「事发时」生成、存起来、之后才被某个请求读走的(最近一次加载失败的原因、
不支持的原因)。那时还不知道是谁来读、要哪种语言,所以存成 :class:`Bilingual`:
它**就是**那句中文(当普通 str 用、拼接、比较、写日志都和以前一样),另外带着
一份英文,由请求的处理函数用 :func:`localize` 挑。

日志、注释、机器读的错误码不翻译;给 LLM 的提示词也不是界面文案,不走这里。
"""

from __future__ import annotations

from typing import Any

ZH = "zh"
EN = "en"
HEADER = "Accept-Language"


def lang_from_header(value: str | None) -> str:
    """解析 `Accept-Language`,返回 ``"zh"`` 或 ``"en"``。

    按 q 值从高到低找第一个认得的语言:``zh*`` → 中文,``en*`` → 英文。
    没带、解析不了、全是别的语言时回中文(向后兼容)。
    """
    if not value:
        return ZH
    candidates: list[tuple[float, int, str]] = []
    for index, part in enumerate(value.split(",")):
        fields = part.strip().split(";")
        tag = fields[0].strip().lower()
        if not tag:
            continue
        q = 1.0
        for param in fields[1:]:
            name, _, raw = param.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(raw.strip())
                except ValueError:
                    q = 0.0
        if q > 0:
            # 同一 q 值保持原来的先后顺序
            candidates.append((-q, index, tag))
    for _q, _i, tag in sorted(candidates):
        if tag == ZH or tag.startswith("zh-") or tag.startswith("zh_"):
            return ZH
        if tag == EN or tag.startswith("en-") or tag.startswith("en_"):
            return EN
    return ZH


def lang_of(conn: Any) -> str:
    """一个请求(FastAPI 的 Request / WebSocket,任何带 `.headers` 的对象)要哪种语言。"""
    headers = getattr(conn, "headers", None)
    return lang_from_header(headers.get("accept-language") if headers is not None else None)


def header(lang: str) -> dict[str, str]:
    """转发给下游服务时带上的语言头(STT → LLM),让下游的提示也是同一种语言。"""
    return {HEADER: lang}


def t(lang: str, zh: str, en: str) -> str:
    """按语言挑一句。英文里要插值就用 f-string:``t(lang, f"还剩 {n} 秒", f"{n}s left")``。"""
    return en if lang == EN else zh


class Bilingual(str):
    """一句中文,顺带一份英文。本体是中文,当普通 str 用就是中文。"""

    en: str

    def __new__(cls, zh: str, en: str) -> Bilingual:
        obj = super().__new__(cls, zh)
        obj.en = en
        return obj


def bi(zh: str, en: str) -> Bilingual:
    return Bilingual(zh, en)


def en_of(value: Any) -> str:
    """英文那一份;不是 :class:`Bilingual` 的(第三方库的原始报错)原样返回。"""
    if isinstance(value, Bilingual):
        return value.en
    return str(value)


def localize(lang: str, value: Any) -> Any:
    """按语言渲染存起来的提示。``None`` 和普通 str 原样返回。"""
    if isinstance(value, Bilingual):
        return value.en if lang == EN else str.__str__(value)
    return value


def _exc_message(e: BaseException) -> Any:
    # 抛出时传的是 Bilingual 的话,它还在 args 里(str(e) 会变回普通的中文 str)。
    if len(e.args) == 1 and isinstance(e.args[0], Bilingual):
        return e.args[0]
    return str(e)


def exc_text(lang: str, e: BaseException) -> str:
    """异常的消息,按语言。第三方库的原始报错原样给。"""
    return localize(lang, _exc_message(e))


def exc_bilingual(e: BaseException) -> str:
    """``类型名: 消息``,消息能分语言时存成 :class:`Bilingual`,留到请求时再挑。"""
    msg = _exc_message(e)
    name = type(e).__name__
    if isinstance(msg, Bilingual):
        return bi(f"{name}: {msg}", f"{name}: {msg.en}")
    return f"{name}: {msg}"
