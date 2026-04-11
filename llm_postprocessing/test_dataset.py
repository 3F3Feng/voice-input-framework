"""
固定测试数据集 - 语音输入后处理场景
用于评估不同LLM在标点修正、分段、文本规整任务上的表现
"""

TEST_CASES = [
    # ===== 基础标点修正 =====
    {
        "id": "punc_001",
        "category": "标点修正",
        "input": "今天天气真好啊我们出去玩吧",
        "description": "简单陈述句无标点",
        "expected_traits": ["。", "，"],
    },
    {
        "id": "punc_002",
        "category": "标点修正",
        "input": "你好我叫张三来自北京很高兴认识你",
        "description": "多个短句无标点",
        "expected_traits": ["，", "。"],
    },
    {
        "id": "punc_003",
        "category": "标点修正",
        "input": "老板给我来一碗牛肉面多放点葱花和香菜",
        "description": "口语化请求句",
        "expected_traits": ["，"],
    },
    
    # ===== 问句处理 =====
    {
        "id": "question_001",
        "category": "问句处理",
        "input": "你今天晚上有空吗要不要一起去看电影",
        "description": "选择疑问句",
        "expected_traits": ["？", "，"],
    },
    {
        "id": "question_002",
        "category": "问句处理",
        "input": "明天会不会下雨啊我看天气预报说可能有雨",
        "description": "自问自答",
        "expected_traits": ["？", "，", "。"],
    },
    
    # ===== 感叹句处理 =====
    {
        "id": "exclaim_001",
        "category": "感叹句处理",
        "input": "太棒了这次考试我居然考了一百分",
        "description": "先感叹后陈述",
        "expected_traits": ["！", "，"],
    },
    {
        "id": "exclaim_002",
        "category": "感叹句处理",
        "input": "哎呀我的天哪这也太贵了吧",
        "description": "多重感叹",
        "expected_traits": ["！"],
    },
    
    # ===== 分段处理 =====
    {
        "id": "segment_001",
        "category": "分段处理",
        "input": "大家好我是小明今天给大家介绍一款非常好用的手机这款手机拍照特别清晰而且运行速度很快价格也很便宜",
        "description": "产品介绍式长句",
        "expected_traits": ["。", "，", "，"],
    },
    {
        "id": "segment_002",
        "category": "分段处理",
        "input": "首先打开浏览器然后输入网址接着点击搜索按钮最后查看结果",
        "description": "步骤描述",
        "expected_traits": ["，", "，", "，", "。"],
    },
    
    # ===== 专有名词保持 =====
    {
        "id": "name_001",
        "category": "专有名词",
        "input": "我今天去了北京理工大学见到了李明教授他研究的是人工智能方向",
        "description": "人名地名保持",
        "expected_traits": ["北京理工大学", "李明", "人工智能"],
    },
    {
        "id": "name_002",
        "category": "专有名词",
        "input": "特斯拉model3电动汽车用的是什么电池技术",
        "description": "品牌产品名保持",
        "expected_traits": ["特斯拉", "Model 3", "电池"],
    },
    
    # ===== 数字格式 =====
    {
        "id": "number_001",
        "category": "数字格式",
        "input": "我的手机号是一三八零零零零零零零零",
        "description": "连续数字分组",
        "expected_traits": ["138", "0000", "0000", "或", "138-0000-0000"],
    },
    {
        "id": "number_002",
        "category": "数字格式",
        "input": "今天是二零二六年四月十一号",
        "description": "日期中文数字转阿拉伯数字",
        "expected_traits": ["2026", "4", "11"],
    },
    
    # ===== 英文处理 =====
    {
        "id": "english_001",
        "category": "英文处理",
        "input": "我最近在学python和javascript这两门编程语言",
        "description": "英文词大小写",
        "expected_traits": ["Python", "JavaScript"],
    },
    {
        "id": "english_002",
        "category": "英文处理",
        "input": "iPhone和Android手机都有各自的优势",
        "description": "英文首字母大写",
        "expected_traits": ["iPhone", "Android"],
    },
    
    # ===== 复杂口语 =====
    {
        "id": "colloquial_001",
        "category": "复杂口语",
        "input": "那个啥就是吧我跟你说哦这个东西真的特别特别好你懂我意思吧",
        "description": "口语填充词",
        "expected_traits": ["！", "。"],  # 应该去除冗余填充词
    },
    {
        "id": "colloquial_002",
        "category": "复杂口语",
        "input": "嗯对没错就是那样我跟你的想法完全一样",
        "description": "口语确认词",
        "expected_traits": ["，", "。"],
    },
    
    # ===== 混合场景 =====
    {
        "id": "mixed_001",
        "category": "混合场景",
        "input": "老公今天下班早我们去超市买菜吧我想吃西红柿炒鸡蛋还有红烧肉",
        "description": "家庭对话",
        "expected_traits": ["，", "，", "，", "。"],
    },
    {
        "id": "mixed_002",
        "category": "混合场景",
        "input": "好的没问题这事儿包在我身上不过我需要点时间你先等我消息",
        "description": "工作对话",
        "expected_traits": ["，", "，", "。"],
    },
]

# 性能测试用例（用于延迟测量）
PERF_TEST_CASES = [
    {"id": "perf_001", "input": "今天天气真好"},
    {"id": "perf_002", "input": "你好"},
    {"id": "perf_003", "input": "再见"},
    {"id": "perf_004", "input": "我想吃火锅"},
    {"id": "perf_005", "input": "明天要下雨记得带伞"},
]


def get_test_cases():
    """获取所有测试用例"""
    return TEST_CASES


def get_perf_test_cases():
    """获取性能测试用例"""
    return PERF_TEST_CASES


def get_categories():
    """获取所有分类"""
    categories = set()
    for case in TEST_CASES:
        categories.add(case["category"])
    return sorted(list(categories))
