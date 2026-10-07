"""纯数据预设：厂商预设、emoji 清单、默认人设模板。

【为什么独立成文件】
这些都是「常量」，不含任何逻辑，也不依赖任何运行时配置。
单独放一处，加厂商 / 加 emoji / 改默认人设都只改本文件。

【安全注记】
本文件里的厂商预设**只含公开的 Base URL 和模型名**，绝不含任何 Key，
也**不含内网/私有地址**（那个 `172.98.60.173` 反代预设已在重构时移除）。
需要接内网模型时，用「自定义」手填，并把 `SOULMATE_ALLOW_PRIVATE_BASE_URL` 设为 true。
"""

from __future__ import annotations

# ── 头像 emoji（内置 40 个；界面也允许手动粘贴任意 emoji）──
EMOJI_OPTIONS = [
    "🧸", "🐶", "🐱", "🐼", "🐨", "🦊", "🐯", "🦁", "🐮", "🐷",
    "🐰", "🐻", "🐺", "🐸", "🐵", "🦄", "🐧", "🐥", "🦋", "🐹",
    "🌸", "🌙", "⭐", "🍀", "🌻", "❤️", "💎", "🎀", "👑", "🎧",
    "📚", "💻", "🧑‍💼", "🧑‍🎓", "🧑‍🎨", "🧑‍🍳", "🥷", "🧙", "👻", "🤖",
]

# ── 模型服务预设 ──
# 选一个预设 = 自动帮你填好 Base URL 和常用模型名，Key 仍要自己填。
# 所有字段都能改，最终生效的永远是你输入框里的值。
PROVIDER_PRESETS: dict[str, dict] = {
    "siliconflow": {
        "name": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "env_key": "SILICONFLOW_API_KEY",
        "models": ["deepseek-ai/DeepSeek-V4-Flash", "Qwen/Qwen3-32B", "deepseek-ai/DeepSeek-R1"],
    },
    "dashscope": {
        "name": "通义千问（阿里百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "DASHSCOPE_API_KEY",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
    },
    "openai": {
        "name": "OpenAI 官方",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "models": ["gpt-4o", "gpt-4o-mini"],
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "base_url": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY",
        "models": ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"],
    },
    "google": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key": "GOOGLE_API_KEY",
        "models": ["gemini-2.0-flash", "gemini-2.5-pro"],
    },
    "deepseek": {
        "name": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "moonshot": {
        "name": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        "models": ["moonshot-v1-8k", "kimi-k2-0711-preview"],
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "env_key": "ZHIPUAI_API_KEY",
        "models": ["glm-4-air", "glm-4-flash"],
    },
    "custom": {
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "env_key": "",
        "models": [],
    },
}

PROVIDER_OPTIONS = list(PROVIDER_PRESETS.keys())
PROVIDER_NAMES = {k: v["name"] for k, v in PROVIDER_PRESETS.items()}


# ── 默认伴侣人设模板 ──
# {name} / {purpose} 是占位符：保存时会替换成用户填的名字和用途。
DEFAULT_SYSTEM_PROMPT = """你叫 {name}，现在是用户的真实伴侣，请完全代入伴侣角色。

规则：
    1. 每次只回 1 条消息
    2. 禁止任何场景或状态描述性文字
    3. 匹配用户的语言
    4. 回复简短，像微信聊天一样
    5. 有需要的话可以用 ❤️🌸 等 emoji 表情
    6. 用符合伴侣性格的方式对话
    7. 回复的内容，要充分体现伴侣的性格特征

伴侣性格 / 用途：
    - {purpose}

你必须严格遵守上述规则来回复用户。"""


# ── 长期记忆抽取指令（给模型看，不是给用户看）──
EXTRACT_PROMPT = """你是记忆提取器。请从下面这段对话里，找出【值得长期记住的、关于用户的稳定事实】。

要记：名字与称呼、职业、所在地、家人与宠物、长期爱好、明确的喜好与禁忌、正在长期做的项目。
不要记：一次性的闲聊内容、助手自己说过的话、临时情绪、随时会变的小事。

只输出 JSON 数组，不要解释，不要 markdown 代码块。没有值得记的就输出 []。
示例：["用户叫李永康", "用户养了一只叫团子的猫", "用户不喝咖啡"]"""
