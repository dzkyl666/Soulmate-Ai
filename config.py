"""
全局配置与预设数据
==================
对标参考项目 04-multibot 的 config.py + bot/config.py：
把「路径 / 表情清单 / 模型服务预设」这类**纯数据**集中在这里，
其它模块只 import 数据、不各自写死常量 —— 以后加一家模型厂商，只改本文件。

【分层说明】（从上到下，越往下越"懂业务"）
    config.py            ← 纯数据（本文件，不 import streamlit）
    storage.py           ← 纯文件读写（不 import streamlit）
    companion_manager.py ← 业务逻辑（伴侣 / 会话 / 模型服务）
    dialogs.py sidebar.py main.py ← 界面层
"""
import os

# ══════════════════════════════════════════════════
# 路径
# ══════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PROVIDERS_FILE = os.path.join(DATA_DIR, "providers.json")     # 模型服务（含 Key，明文，不上传）
COMPANIONS_FILE = os.path.join(DATA_DIR, "companions.json")   # 伴侣定义
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")             # 每个伴侣一个子目录
PROFILE_FILE = os.path.join(DATA_DIR, "profile.json")         # 使用者自己的昵称/头像（全局一份，不分伴侣）
# 老版本"单层会话"目录：只在第一次启动时用来把旧聊天搬进新结构，之后不再使用
LEGACY_SESSION_DIR = os.path.join(BASE_DIR, "session")

# ══════════════════════════════════════════════════
# 头像表情（内置 40 个备选；界面上也允许手动粘贴任意 emoji）
# ══════════════════════════════════════════════════
EMOJI_OPTIONS = [
    "🧸", "🐶", "🐱", "🐼", "🐨", "🦊", "🐯", "🦁", "🐮", "🐷",
    "🐰", "🐻", "🐺", "🐸", "🐵", "🦄", "🐧", "🐥", "🦋", "🐹",
    "🌸", "🌙", "⭐", "🍀", "🌻", "❤️", "💎", "🎀", "👑", "🎧",
    "📚", "💻", "🧑‍💼", "🧑‍🎓", "🧑‍🎨", "🧑‍🍳", "🥷", "🧙", "👻", "🤖",
]

# ══════════════════════════════════════════════════
# 模型服务预设（对标 multibot 的 ENGINE_CONFIG）
# ══════════════════════════════════════════════════
# 选一个预设 = 自动帮你填好 Base URL 和常用模型名，Key 仍要自己填；
# 所有字段都还能改，最终生效的永远是你输入框里的值。
# 选「自定义（OpenAI 兼容）」则四个字段全部手填。
PROVIDER_PRESETS = {
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

# ══════════════════════════════════════════════════
# 默认伴侣人设模板
# ══════════════════════════════════════════════════
# {name} / {purpose} 是占位符：新建伴侣保存时会自动替换成你填的名字和用途。
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

# ══════════════════════════════════════════════════
# 其它默认值
# ══════════════════════════════════════════════════
DEFAULT_HISTORY_LENGTH = 15   # 每次请求携带的历史消息条数上限（侧边栏可调）
DEFAULT_AVATAR = "🧸"          # 伴侣头像的兜底值
DEFAULT_USER_NICKNAME = "我"   # 使用者昵称未设置时，消息气泡上显示的默认名
DEFAULT_USER_AVATAR = "🐶"     # 使用者头像未设置时的兜底值
