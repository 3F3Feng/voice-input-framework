"""
tools/llm_dictation_eval.py 里「数字的值」那部分判定,和提示词里举的示例数字。

评测本身要对着真模型跑,CI 跑不了;这里钉住的是判定自己别出错 —— 它读错一个数,
「两万三千件被写成 23500 件」这种错就又查不出来了:
- 汉字、英文单词、阿拉伯数字几种写法读出来是同一个值;
- 值变了、多出一个数、该转成数字的没转,各报各的;
- 用例里声明的数,从它自己的输入里读得出来(防止用例写错);
- 每一份提示词(默认的、按模型另写的、前端的预设)规则里举的金额示例都是整数 —— 那个错
  就是示例里的非零数字被抄进了别的数。
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

import services.llm_server as srv  # noqa: E402


def _load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, project_dir / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass 要按模块名找回自己
    spec.loader.exec_module(module)
    return module


de = _load_tool("llm_dictation_eval")


@pytest.mark.parametrize(
    ("spoken", "value"),
    [
        ("两万三千", "23000"),
        ("两万三千五百", "23500"),
        ("两万三", "23000"),
        ("两万三千六", "23600"),
        ("二十三万五千", "235000"),
        ("三千五", "3500"),
        ("一百五", "150"),
        ("一百零五", "105"),
        ("一千零八十", "1080"),
        ("两万零三", "20003"),
        ("十", "10"),
        ("十二万八千", "128000"),
        ("三千五百万", "35000000"),
        ("一亿两千万", "120000000"),
        ("一点二亿", "120000000"),
        ("九十八点五", "98.5"),
        ("零点零零一", "0.001"),
        ("一三八零零一三八零零零", "13800138000"),
        ("二零二六", "2026"),
        ("二点七点二", "2.7.2"),
    ],
)
def test_chinese_numerals(spoken, value):
    assert de.cn_number(spoken) == value


@pytest.mark.parametrize(
    ("spoken", "values"),
    [
        ("twenty three thousand", ["23000"]),
        ("twenty three thousand eight hundred", ["23800"]),
        ("fifteen hundred", ["1500"]),
        ("three hundred and fifty", ["350"]),
        ("ninety eight point five", ["98.5"]),
        ("zero point zero zero three", ["0.003"]),
        ("two point three million", ["2300000"]),
        ("five five five zero one four seven", ["5550147"]),
        # 接不上的是两个数
        ("two thirty", ["2", "30"]),
        ("nineteen ninety nine", ["19", "99"]),
    ],
)
def test_english_number_words(spoken, values):
    assert de.en_numbers(spoken.split()) == values


@pytest.mark.parametrize(
    ("text", "arabic"),
    [
        ("这批货一共 23,000 件，合格率是 98.5%。", {"23000", "98.5"}),
        ("这批货一共 2.3 万件", {"23000"}),
        ("去年营收是 3500 万，今年目标 1.2 亿。", {"35000000", "120000000"}),
        ("Revenue was $2.3 million, up 15% from last year.", {"2300000", "15"}),
        ("有事打我手机，159-2653-5897。", {"15926535897"}),
        ("有事打我手机，159 2653 5897。", {"15926535897"}),
        ("My number is 555-0147.", {"5550147"}),
        ("上午 10:00 或 11:00 之前发给我就行。", {"10", "11"}),
        ("升级到 3.12.4 版本，端口 9090", {"3.12.4", "9090"}),
        # 列表编号不是口述里的数;日期不会被当成电话拼起来
        ("1. 写周报\n2. 约客户\n3. 报销 300 元", {"300"}),
        ("2026-10-13 发的", {"2026", "10", "13"}),
        ("这批货一共两万三千件，合格率是百分之九十八点五。", set()),
    ],
)
def test_digits_found_in_text(text, arabic):
    assert de.numbers_in(text)[0] == arabic


def test_spelled_out_numbers_are_read_too():
    _, spelled = de.numbers_in("这批货一共两万三千件，合格率是百分之九十八点五。")
    assert {"23000", "98.5"} <= spelled
    _, spelled = de.numbers_in(
        "We shipped twenty three thousand units, ninety eight point five percent."
    )
    assert {"23000", "98.5"} <= spelled
    # 「三点四十」是钟点,不是 3.4
    assert {"3", "40"} <= de.numbers_in("三点四十开会")[1]


class TestCheckNumbers:
    case = de.Case("x", "这批货一共两万三千件，合格率是百分之九十八点五。", nums=["23000", "98.5"])

    @pytest.mark.parametrize(
        "out",
        [
            "这批货一共 23000 件，合格率是 98.5%。",
            "这批货一共 23,000 件，合格率是 98.5%。",
            "这批货一共 2.3 万件，合格率是 98.5%。",
            "这批货一共两万三千件，合格率是百分之九十八点五。",
        ],
    )
    def test_any_spelling_of_the_same_value_passes(self, out):
        assert de.check_numbers(self.case, out) == []

    def test_a_changed_value_is_caught(self):
        # 2026-10 实际出过的错:照着提示词里的示例「两万三千五百块 → 23500 元」补全了
        problems = de.check_numbers(self.case, "这批货一共 23500 件，合格率是 98.5%。")
        assert problems == ["lost number 23000", "wrong number 23500"]

    def test_a_longer_number_containing_the_digits_does_not_pass(self):
        # keep=["23000"] 按子串找,这两个都会放过去
        assert de.check_numbers(self.case, "这批货一共 230000 件，合格率是 98.5%。")
        assert de.check_numbers(self.case, "这批货一共 23000 件，合格率是 198.5%。")

    def test_a_changed_value_left_in_chinese_is_caught(self):
        problems = de.check_numbers(
            self.case, "这批货一共两万三千五百件，合格率是百分之九十八点五。"
        )
        assert problems == ["lost number 23000"]

    def test_digits_required(self):
        case = de.Case("x", "定金先付两万三千块。", nums=["23000"], digits=True)
        assert de.check_numbers(case, "定金先付 23000 元。") == []
        assert de.check_numbers(case, "定金先付两万三千块。") == ["number 23000 not in digits"]

    def test_alternatives(self):
        case = de.Case("x", "下午两点开始。", nums=["2|14"])
        assert de.check_numbers(case, "下午 2 点开始。") == []
        assert de.check_numbers(case, "14:00 开始。") == []
        assert de.check_numbers(case, "下午 3 点开始。") == ["lost number 2|14", "wrong number 3"]

    def test_check_reports_number_problems(self):
        problems, _ = de.check(self.case, "这批货一共 23500 件，合格率是 98.5%。", True)
        assert "wrong number 23500" in problems


NUMBER_CASES = [c for c in de.CASES + de.HELD_OUT if c.nums]


def test_there_are_number_cases_in_both_groups():
    assert sum(c in de.CASES for c in NUMBER_CASES) >= 10
    assert sum(c in de.HELD_OUT for c in NUMBER_CASES) >= 5


@pytest.mark.parametrize("case", NUMBER_CASES, ids=lambda c: c.name)
def test_declared_numbers_can_be_read_from_the_input(case):
    """用例声明的每个数都在它自己的输入里;原样不动的输入只会因为「没转成数字」不合格。"""
    arabic, spelled = de.numbers_in(case.text)
    for n in case.nums:
        assert {de.canon(o) for o in n.split("|")} & (arabic | spelled), n
    problems = [p for p in de.check_numbers(case, case.text) if "not in digits" not in p]
    assert problems == []


# ── 提示词里举的示例数字 ──


def _all_prompts() -> dict[str, str]:
    """通用的默认提示词、按模型另写的、前端的三个预设(中英各一份)。"""
    prompts = _load_tool("llm_prompt_eval").load_prompts(None)
    for model, by_lang in srv.MODEL_PROMPTS.items():
        for lang, text in by_lang.items():
            prompts[f"{model}_{lang}"] = text
    return prompts


PROMPTS = _all_prompts()
AMOUNT_EXAMPLE = re.compile(r"→ ([\d,.]+) 元")


def test_every_prompt_is_checked():
    assert {"default_zh", "default_en", "chat_zh", "chat_en", "email_zh", "email_en"} <= set(
        PROMPTS
    )
    assert {"tech_zh", "tech_en", "Gemma-4-E2B-GGUF_en"} <= set(PROMPTS)


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_amount_examples_in_the_rules_are_round_numbers(name):
    """规则里举的金额示例只能是整数:最高位以外全是零。

    示例里的非零数字会被抄进别的数。规则里举着「两万三千五百块 → 23500 元」的时候,「这批货一共
    两万三千件」被写成了「23500 件」(Gemma-4-E4B,默认提示词和三个预设都是)—— 输入的数和示例
    开头相同,后面没说出来的那几位,模型照着示例补上了。整数示例没有可抄的非零数字。

    要换示例数字:先看 services/llm_server.py 里 DEFAULT_PROMPT 上面记的几种试过的写法;换完在
    tools/llm_dictation_eval.py 的 n 组里加上和新数字开头相同的例子,对着真模型跑一遍。
    """
    rules = re.split(r"\n(?:示例：|Examples:)\n", PROMPTS[name])[0]
    amounts = AMOUNT_EXAMPLE.findall(rules)
    assert amounts, "格式那条里没有金额示例了(去掉示例之后 E4B 会把「两万三千八百」只转一半)"
    for amount in amounts:
        assert re.fullmatch(r"[1-9]0*", amount), f"{name}: 示例「{amount} 元」不是整数"
