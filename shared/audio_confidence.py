#!/usr/bin/env python3
"""
Voice Input Framework - 置信度估算工具

基于音频信号特征和文本分析估算识别置信度。
"""

import re
from typing import Optional


def estimate_confidence(
    audio_data: bytes,
    text: str,
    sample_rate: int = 16000,
) -> float:
    """
    估算识别置信度
    
    结合音频信号质量和文本特征综合评估。
    
    Args:
        audio_data: 原始音频数据 (int16 PCM)
        text: 识别出的文本
        sample_rate: 采样率
        
    Returns:
        置信度 (0.0 - 1.0)
    """
    if not text or len(text.strip()) == 0:
        return 0.0
    
    # 1. 音频信号质量分析 (权重: 40%)
    signal_score = _analyze_signal_quality(audio_data, sample_rate)
    
    # 2. 文本质量分析 (权重: 40%)
    text_score = _analyze_text_quality(text)
    
    # 3. 音频-文本一致性检查 (权重: 20%)
    consistency_score = _check_audio_text_consistency(audio_data, text, sample_rate)
    
    # 综合得分
    confidence = (
        signal_score * 0.4 +
        text_score * 0.4 +
        consistency_score * 0.2
    )
    
    # 确保在有效范围内
    return max(0.0, min(1.0, confidence))


def _analyze_signal_quality(audio_data: bytes, sample_rate: int) -> float:
    """分析音频信号质量"""
    import numpy as np
    
    try:
        # 转换为 numpy 数组
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        
        if len(audio_array) == 0:
            return 0.0
        
        # 计算 RMS (有效值)
        rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
        
        # 计算峰值
        peak = np.max(np.abs(audio_array))
        
        # 1. 音量适中度评分 (0-1)
        # 理想 RMS 在 2000-20000 之间
        if rms < 500:
            volume_score = rms / 500 * 0.5  # 太安静
        elif rms > 40000:
            volume_score = max(0.0, 1.0 - (rms - 40000) / 20000)  # 太吵
        else:
            # 在理想范围内，得分高
            volume_score = 1.0
        
        # 2. 削波检测 (0-1)
        # 削波比例过高说明录音失真
        clipping_threshold = 30000
        clipping_ratio = np.sum(np.abs(audio_array) > clipping_threshold) / len(audio_array)
        clipping_score = max(0.0, 1.0 - clipping_ratio * 10)
        
        # 3. 动态范围评分 (0-1)
        # 动态范围过低说明音频太平
        if peak > 0:
            dynamic_range = 20 * np.log10(peak / (rms + 1))
            # 理想动态范围 12-30 dB
            if dynamic_range < 6:
                dynamic_score = dynamic_range / 6
            elif dynamic_range > 40:
                dynamic_score = max(0.0, 1.0 - (dynamic_range - 40) / 20)
            else:
                dynamic_score = 1.0
        else:
            dynamic_score = 0.5
        
        # 综合信号得分
        signal_score = (
            volume_score * 0.4 +
            clipping_score * 0.3 +
            dynamic_score * 0.3
        )
        
        return signal_score
        
    except Exception:
        return 0.5  # 出错时返回中等分数


def _analyze_text_quality(text: str) -> float:
    """分析文本质量"""
    if not text:
        return 0.0
    
    text_lower = text.lower()
    words = text.split()
    
    # 1. 重复词检测 (0-1)
    # 连续重复的词可能表示识别错误
    repetition_count = 0
    for i in range(len(words) - 2):
        if words[i] == words[i+1] == words[i+2]:
            repetition_count += 1
    repetition_score = max(0.0, 1.0 - repetition_count * 0.3)
    
    # 2. 字符异常检测 (0-1)
    # 检查是否有大量连续相同字符或奇怪符号
    unusual_char_ratio = _count_unusual_chars(text)
    char_score = 1.0 - unusual_char_ratio
    
    # 3. 长度合理性 (0-1)
    # 太短或太长的文本可能不可靠
    word_count = len(words)
    if word_count == 0:
        length_score = 0.0
    elif word_count <= 3:
        length_score = 0.7  # 短句但可能正常
    elif word_count >= 100:
        length_score = 0.6  # 长句，可靠性略降
    else:
        length_score = 1.0
    
    # 4. 语言模型简单检查 (0-1)
    # 检查是否有常见词
    common_words = ['的', '是', '在', '我', '有', '和', '就', '不', '人', '都', 
                    'the', 'is', 'at', 'was', 'a', 'i', 'to', 'you', 'and']
    word_matches = sum(1 for w in words if w.lower() in common_words)
    language_score = min(1.0, word_matches / max(1, len(words) * 0.3))
    
    # 综合文本得分
    text_score = (
        repetition_score * 0.25 +
        char_score * 0.25 +
        length_score * 0.25 +
        language_score * 0.25
    )
    
    return text_score


def _check_audio_text_consistency(audio_data: bytes, text: str, sample_rate: int) -> float:
    """检查音频-文本一致性"""
    import numpy as np
    
    try:
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        
        # 计算音频时长（秒）
        audio_duration = len(audio_array) / sample_rate
        
        # 计算文本长度（字符数）
        text_length = len(text)
        
        # 文本长度（词数）
        word_count = len(text.split())
        
        # 1. 语速合理性检查 (0-1)
        # 正常语速: 2-6 词/秒
        if audio_duration > 0:
            words_per_second = word_count / audio_duration
            if words_per_second < 0.5:
                pace_score = 0.3  # 太慢
            elif words_per_second > 10:
                pace_score = 0.3  # 太快
            else:
                pace_score = 1.0
        else:
            pace_score = 0.5
        
        # 2. 音频能量与文本长度匹配 (0-1)
        # 有音频但文本太短或太长
        if audio_duration > 0.5 and word_count < 1:
            match_score = 0.2  # 有音频但没有识别出文本
        elif word_count > 0 and audio_duration / word_count > 2.0:
            match_score = 0.6  # 每个词占用时间过长
        else:
            match_score = 1.0
        
        consistency_score = (pace_score * 0.6 + match_score * 0.4)
        
        return consistency_score
        
    except Exception:
        return 0.5


def _count_unusual_chars(text: str) -> float:
    """统计异常字符比例"""
    import re
    
    # 移除正常字符
    # 中文、英文、数字、标点
    normal_chars = re.sub(r'[\u4e00-\u9fff\w\s,.!?;:\'\"-]', '', text)
    
    if len(text) == 0:
        return 0.0
    
    return len(normal_chars) / len(text)
