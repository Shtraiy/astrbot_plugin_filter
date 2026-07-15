"""
⑦ 智能分段 & 文风优化：LLM 优先 → 规则降级，以及多消息逐段发送。
"""

from __future__ import annotations

import asyncio
import random
import re
import logging

logger = logging.getLogger(__name__)

# ---- 规则分段常量 ----
_SENTENCE_SPLIT = re.compile(r'(?<=[。！？])\s*')
SEGMENT_THRESHOLD = 150       # 短于此值不处理
_CHARS_PER_PARA = 300          # 目标每段字数
_MAX_PARAS = 5                 # 最多段数

# 列表行检测：编号 / 圆圈数字 / 符号前缀
_LIST_LINE_RE = re.compile(
    r'^\s*(?:\d+[\.\)、]|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|[-•·▪▸►●○➤✓✅])\s'
)

# ---- LLM 提示词 ----

_SEGMENT_PROMPT = (
    "按语义将文本分段，用\\n\\n分隔后输出：\n"
    "- 按话题的自然转换处分段，不要强行分成\"开场\"\"正文\"\"收尾\"\n"
    "- 一个话题说完了就换段，无论这段有多短\n"
    "- 密集信息部分可以适当多分几段，让阅读更轻松\n"
    "通常3~5段，原文>600字可扩至6段。若含无关话题用\\n---\\n分隔。\n"
    "严禁修改任何一个字、标点、语气词、emoji——仅调整分段和换行，不改原文内容。只输出结果。\n\n"
    "原文：\n{text}"
)

_STYLE_PROMPT = (
    "将以下文本改写成更自然、更像真人随口聊天的风格。\n\n"
    "【硬约束——必须严格遵守】\n"
    "1. 人设不变：语气、口头禅、emoji、称呼方式完全保留，不改动任何一个体现性格的词\n"
    "2. 内容不变：原文所有信息必须保留，不添加原文没有的事实或建议\n"
    "3. 语义不变：每句话的原意不能有任何偏差\n\n"
    "【去AI味——必须执行】\n"
    "1. 禁止使用任何AI开场白和过渡句，如\"我来给你整理一下\"\"我这就把...列出来\"\"接下来是...\"\"以下是...\"\"让我为你展开说说\"等\n"
    "2. 禁止使用\"（括号备注）\"——所有补充信息必须融入句子自然表达，不能用括号附加说明\n"
    "3. 禁止使用\"首先...其次...最后...\"\"第一...第二...\"等论文式衔接词。操作步骤可保留序号（1. 2. 3.），但不要加\"第一步是\"等冗长引导\n"
    "4. 禁止使用\"值得注意的是\"\"需要提醒的是\"\"此外\"\"另外\"\"顺便一提\"\"补充一点\"等教科书过渡词\n"
    "5. 不要强行制造\"开场寒暄→正文→收尾关怀\"的三段式结构。像真人聊天一样想到哪说到哪，自然开始、自然结束\n"
    "6. 如果原文结尾已经表达了关心或收束的意思，不要额外再追加\"如果还有什么想了解的随时问我\"\"希望能帮到你\"等AI常用收尾句\n\n"
    "【输出风格——尽量贴近真人】\n"
    "- 像朋友之间随口分享，不要像写文章或写教程\n"
    "- 句子长短错落，不要每句话都差不多长\n"
    "- 段落自然断开即可，不需要每段都写成完整的\"小块\"\n"
    "- 可以一句话一段，也可以一段说很多句——完全按语感来\n\n"
    "【输出格式】\n"
    "只输出改写后的文本。不要加任何前缀、后缀或解释。\n\n"
    "原文：\n{text}"
)


# ============================================================
#  LLM 语义分段
# ============================================================

async def try_llm_segment(text: str, context, get_config) -> str | None:
    """
    尝试用 LLM 做语义分段。
    成功返回分段后文本，失败返回 None（触发规则降级）。
    注意：此函数的 LLM 调用失败不会影响主对话——插件会自动降级到规则分段。
    """
    if not get_config("enable_llm_segment"):
        return None

    provider_id = get_config("llm_provider_id", "")
    if not provider_id:
        logger.info("[LLM分段] 未配置 llm_provider_id，跳过。请在插件配置中选择 LLM 模型。")
        return None

    if len(text) <= SEGMENT_THRESHOLD:
        return None

    try:
        logger.info("[LLM分段] 正在调用 LLM（provider=%s）...", provider_id)
        llm_resp = await context.llm_generate(
            chat_provider_id=provider_id,
            prompt=_SEGMENT_PROMPT.format(text=text),
        )
        result = llm_resp.completion_text.strip()

        if not result:
            logger.warning("[LLM分段] 返回空结果，降级到规则分段")
            return None
        if len(result) < len(text) * 0.3:
            logger.warning("[LLM分段] 输出过短（可能截断），降级到规则分段")
            return None

        # 验证中文字数不得明显增减（允许 ±5%）
        orig_han = len(re.findall(r'[一-鿿]', text))
        result_han = len(re.findall(r'[一-鿿]', result))
        if orig_han > 0 and abs(orig_han - result_han) > orig_han * 0.05:
            logger.warning(
                "[LLM分段] 原文内容被篡改（中文字数 %d → %d），降级到规则分段",
                orig_han, result_han,
            )
            return None

        # 检测无关话题混入
        if '\n---\n' in result:
            topic_count = result.count('\n---\n') + 1
            logger.warning(
                "[LLM分段] 检测到 %d 个无关话题混入。"
                "建议检查 LLM 回复质量，避免无关内容混入用户回复。",
                topic_count,
            )
            result = result.replace('\n---\n', '\n\n')

        logger.info("[LLM分段] 语义分段完成")
        return result

    except Exception:
        logger.warning(
            "[LLM分段] LLM 调用失败（provider=%s），自动降级到规则分段。"
            "这不影响主对话回复，仅分段功能回退。"
            "如不需要 LLM 分段，可在配置中关闭 enable_llm_segment。",
            provider_id, exc_info=True,
        )
        return None


# ============================================================
#  LLM 文风优化
# ============================================================

async def try_llm_style_optimize(text: str, context, get_config) -> str | None:
    """
    尝试用 LLM 做结构化重组 + 文风润色。
    成功返回优化后文本，失败返回 None（触发降级）。
    """
    enable = get_config("enable_llm_style", False)
    provider_id = get_config("llm_provider_id", "")

    if not enable:
        return None

    if not provider_id:
        logger.info("[LLM文风] 未配置 llm_provider_id，跳过。请在插件配置中选择 LLM 模型。")
        return None

    if len(text) <= SEGMENT_THRESHOLD:
        return None

    try:
        logger.info("[LLM文风] 正在调用 LLM（provider=%s）...", provider_id)
        llm_resp = await context.llm_generate(
            chat_provider_id=provider_id,
            prompt=_STYLE_PROMPT.format(text=text),
        )
        result = llm_resp.completion_text.strip()
        logger.info("[LLM文风] LLM 返回 %d 字符", len(result))

        if not result:
            logger.warning("[LLM文风] 返回空结果，降级到下一级")
            return None
        if len(result) < len(text) * 0.3:
            logger.warning(
                "[LLM文风] 输出过短（%d < %d），降级到下一级",
                len(result), int(len(text) * 0.3),
            )
            return None

        # 文风优化允许 ±10% 中文字数浮动
        orig_han = len(re.findall(r'[一-鿿]', text))
        result_han = len(re.findall(r'[一-鿿]', result))
        if orig_han > 0 and abs(orig_han - result_han) > orig_han * 0.10:
            logger.warning(
                "[LLM文风] 内容偏差过大（中文字数 %d → %d），降级到下一级",
                orig_han, result_han,
            )
            return None

        para_count = len([p for p in result.split('\n\n') if p.strip()])
        logger.info("[LLM文风] ✓ 文风优化完成，%d 段", para_count)
        return result

    except Exception:
        logger.warning(
            "[LLM文风] LLM 调用失败（provider=%s），自动降级到规则分段。"
            "这不影响主对话回复。如不需要 LLM 文风优化，可在配置中关闭 enable_llm_style。",
            provider_id, exc_info=True,
        )
        return None


# ============================================================
#  分段/文风统一入口
# ============================================================

async def apply_segmentation_and_style(text: str, context, get_config) -> str:
    """优先级：LLM 文风优化 > LLM 语义分段 > 规则分段"""
    if len(text) <= SEGMENT_THRESHOLD:
        return text

    # 1) LLM 文风优化（含结构重组）
    result = await try_llm_style_optimize(text, context, get_config)
    if result:
        logger.info("[分段/文风] 使用 LLM 文风优化")
        return result

    # 2) LLM 语义分段
    result = await try_llm_segment(text, context, get_config)
    if result:
        logger.info("[分段/文风] 使用 LLM 语义分段")
        return result

    # 3) 规则分段
    logger.info("[分段/文风] 使用规则分段")
    return _segment_text(text)


# ============================================================
#  多消息逐段发送
# ============================================================

async def send_followups(context, umo, paragraphs: list,
                         delay_min: float, delay_max: float) -> None:
    """逐段发送后续消息，段间随机延迟，模拟真人打字节奏"""
    from astrbot.api.all import MessageChain

    for i, para in enumerate(paragraphs):
        delay = random.uniform(delay_min, delay_max)
        await asyncio.sleep(delay)
        try:
            chain = MessageChain().message(para)
            await context.send_message(umo, chain)
            logger.info(
                "[多消息发送] 第 %d/%d 段已发送（延迟 %.1fs）",
                i + 2, len(paragraphs) + 1, delay,
            )
        except Exception:
            logger.warning("[多消息发送] 第 %d 段发送失败", i + 2, exc_info=True)


# ============================================================
#  规则分段（fallback）
# ============================================================

def _is_list_block(text: str) -> bool:
    """检测文本是否为列表结构（≥2 行带编号/符号前缀）"""
    lines = text.split('\n')
    list_count = sum(1 for ln in lines if _LIST_LINE_RE.match(ln))
    return list_count >= 2


def _split_long_para(text: str) -> list:
    """将单个长段落按句子均分成 ~200 字的子段落"""
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if len(sentences) <= 2:
        # 句子虽少但文本很长 → 按逗号/分号做子句切分再分组
        if len(text) > _CHARS_PER_PARA:
            sub_clauses = re.split(r'(?<=[，,；;])\s*', text)
            if len(sub_clauses) >= 4:
                total = len(text)
                n = max(2, min(_MAX_PARAS, total // _CHARS_PER_PARA))
                size = max(1, len(sub_clauses) // n)
                paras = []
                for i in range(n):
                    start = i * size
                    end = len(sub_clauses) if i == n - 1 else start + size
                    para = ''.join(sub_clauses[start:end])
                    if para:
                        paras.append(para)
                if len(paras) >= 2:
                    return paras
        return [text]

    total = sum(len(s) for s in sentences)
    n = max(2, min(_MAX_PARAS, total // _CHARS_PER_PARA))
    size = max(1, len(sentences) // n)

    paras = []
    for i in range(n):
        start = i * size
        end = len(sentences) if i == n - 1 else start + size
        para = ''.join(sentences[start:end])
        if para:
            paras.append(para)
    return paras


def _segment_text(text: str) -> str:
    """
    改进的规则分段：
    1. 短文本不处理
    2. 密集文本（无换行）在小节标题前自动插入段落分隔
    3. 按已有空行拆段，尊重 LLM/用户手动分段
    4. 对超长段落进一步细分（列表块除外）
    5. 段数不足时拆分最长段；段数超限时合并最短段
    6. 最终目标：3~5 段
    """
    if len(text) <= SEGMENT_THRESHOLD and text.count('\n\n') < 2:
        return text

    # ---- 预处理：密集文本的段落拆分 ----
    if text.count('\n') <= 2:
        text = re.sub(
            r'([。！？：])\s+(?=[^\s。！？：]{2,12}：)',
            r'\1\n\n', text,
        )

    # ---- 预处理：单换行规范化 ----
    text = re.sub(r'([。！？])\n(?!\n)', r'\1\n\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 按已有空行拆段
    raw = [p.strip() for p in text.split('\n\n') if p.strip()]

    # 处理每段：超长且非列表 → 细分
    result = []
    for para in raw:
        if len(para) <= _CHARS_PER_PARA or _is_list_block(para):
            result.append(para)
        else:
            result.extend(_split_long_para(para))

    # 段数太少（<3）→ 拆分最长段
    if len(result) < 3 and len(result) > 0:
        longest_idx = max(range(len(result)), key=lambda i: len(result[i]))
        longest = result.pop(longest_idx)
        subs = _split_long_para(longest)
        result[longest_idx:longest_idx] = subs

    # 段数超限（>_MAX_PARAS）→ 合并最短段到较短邻居
    while len(result) > _MAX_PARAS:
        i = min(range(len(result)), key=lambda i: len(result[i]))
        if i == 0:
            j = 1
        elif i == len(result) - 1:
            j = i - 1
        else:
            j = i - 1 if len(result[i - 1]) <= len(result[i + 1]) else i + 1
        left, right = (i, j) if i < j else (j, i)
        result[left] = result[left] + '\n' + result[right]
        result.pop(right)

    # 合并孤立的过渡句：短句（≤80字）以冒号结尾 → 跟下一段合并
    # 避免 "除此以外，她还唱过很多歌哦：\n\n比如..." 的割裂感
    merged = []
    skip_next = False
    for i, para in enumerate(result):
        if skip_next:
            skip_next = False
            continue
        stripped = para.rstrip()
        if (i < len(result) - 1
                and len(stripped) <= 80
                and stripped.endswith(('：', ':'))
                and '\n' not in stripped):  # 纯单行过渡句，不含内部换行
            merged.append(stripped + '\n' + result[i + 1].lstrip())
            skip_next = True
        else:
            merged.append(para)
    result = merged

    return '\n\n'.join(result)
