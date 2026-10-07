"""
模型调用层（全项目唯一出口）
============================
全项目**只有本文件** import openai。业务层（companion_manager）和界面层（main）
只调这里的函数，不直接碰 SDK。

【在分层里的位置】
    config.py            ← 纯数据（厂商预设、默认值）
    storage.py           ← 纯文件读写
    llm.py               ← 纯模型调用（本文件）★
    companion_manager.py ← 业务逻辑（伴侣 / 会话 / 模型服务池）
    dialogs.py sidebar.py main.py ← 界面层

【为什么值得单抽一层】
抽之前，模型调用散在两处、各写一遍：
    companion_manager.py  的 build_client() + generate_ai_title() 里一次
    main.py               的聊天输入区里一次
后果是「改一处忘一处」。将来要加的东西全是横切关注点，会在两处同时发散：
    · Function Calling（要传 tools / tool_choice）
    · 超时与重试、生成参数（temperature / max_tokens）
    · 厂商差异（有的端点不认某些参数）
    · 换 SDK / 换 LangChain
抽成一层后，这些都只改本文件。

【本模块的边界（重要）】
只认两样东西：
    ① provider 配置字典（base_url / api_key / model / preset）
    ② 标准 messages 列表（[{"role": "user", "content": "..."}, ...]）
**不认识**"伴侣""会话""人设"这些业务概念 —— 边界清楚才好替换。

【和「记忆」的分工】
本模块**不管历史从哪来、存到哪去**，只负责"把给我的这批消息发出去"。

历史的取与存全在业务层（现在存在 data/sessions/<伴侣id>/<会话id>.json）。
所以：
    · 现在这种「只带最近 N 条」的临时记忆，是 main.py 里切片实现的
    · 将来做长期记忆（把用户偏好抽出来存 memory.json、跨会话注入）时，
      在业务层拼好 [长期记忆 + 最近 N 条]，再整体传进来
    · **无论哪种，本文件一行都不用改**

这正是 LangChain 里 RunnableWithMessageHistory 的位置：它管历史的取/存，
模型只管收消息。你现在手写的 FileChatMessageHistory + 切片，是它的手工版。
"""
import os
from typing import Iterator

from config import PROVIDER_PRESETS


# ══════════════════════════════════════════════════
# 配置解析
# ══════════════════════════════════════════════════
def resolve_api_key(provider: dict) -> str:
    """取 API Key：优先用界面上填的；没填就回退到预设对应的同名环境变量。

    为什么要有回退：输入框留空 = 用户想用系统环境变量里那份，
    免得同一个 Key 在界面和系统环境里各存一份、改了一处忘了另一处。
    """
    key = (provider.get("api_key") or "").strip()
    if key:
        return key
    preset = PROVIDER_PRESETS.get(provider.get("preset", ""), {})
    env_name = preset.get("env_key")
    return os.getenv(env_name, "") if env_name else ""


def build_client(provider: dict, max_retries=None):
    """按 provider 配置建一个 OpenAI 客户端。

    ⚠️ base_url 必须显式传：OpenAI 的 SDK 自己会读 OPENAI_BASE_URL 环境变量，
    不传的话请求可能被悄悄发到别的地方去（你电脑上正好有这个变量）。

    max_retries：SDK 默认失败重试 2 次。聊天时重试是好事（网络抖一下能自愈），
    但「长期记忆抽取」这种顺手做的事不该重试 —— 实测失败一次要白等 10 秒。
    所以留个口子给它传 0。
    （注：max_retries 是**客户端级**参数，只能在这里设，不能像 timeout 那样
        从 create() 传进去。）
    """
    from openai import OpenAI
    kwargs = {
        "api_key": resolve_api_key(provider),
        "base_url": provider.get("base_url"),
    }
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return OpenAI(**kwargs)


# ══════════════════════════════════════════════════
# 内部：拼 messages
# ══════════════════════════════════════════════════
def _build_payload(messages, system_prompt=None):
    """把「系统提示词 + 对话历史」拼成 SDK 要的 messages 列表。

    system 单独抽成参数、而不是让调用方自己往列表里塞，是为了让
    "system 永远排在最前面"这条格式规则只存在于一个地方。
    """
    payload = []
    if system_prompt:
        payload.append({"role": "system", "content": system_prompt})
    payload.extend(messages)
    return payload


# ══════════════════════════════════════════════════
# 对外：两种调用方式
# ══════════════════════════════════════════════════
def chat(provider: dict, messages, system_prompt=None, max_retries=None, **options) -> str:
    """非流式：发一轮，等它说完，返回纯文本（已去首尾空白）。

    用于「AI 起名」「记忆抽取」这类不需要打字机效果、只要最终结果的场景。

    **options 会原样透传给 SDK 的 create()，所以 temperature / max_tokens /
    timeout 这些都能直接写。唯独 max_retries 要单独走形参（见 build_client 说明）。
    """
    client = build_client(provider, max_retries=max_retries)
    resp = client.chat.completions.create(
        model=provider.get("model"),
        messages=_build_payload(messages, system_prompt),
        stream=False,
        **options,
    )
    return (resp.choices[0].message.content or "").strip()


def stream_chat(provider: dict, messages, system_prompt=None, **options) -> Iterator[str]:
    """流式：逐字 yield 文本片段（自动跳过空片段）。

    用于聊天主区，配合 st.write_stream 出打字机效果。
    """
    client = build_client(provider)
    response = client.chat.completions.create(
        model=provider.get("model"),
        messages=_build_payload(messages, system_prompt),
        stream=True,
        **options,
    )
    for chunk in response:
        # 部分厂商在流末尾会多发一个只有用量统计、没有 choices 的 chunk，
        # 直接取 chunk.choices[0] 会 IndexError —— 防一手。
        if not chunk.choices:
            continue
        content = chunk.choices[0].delta.content
        if content:
            yield content
