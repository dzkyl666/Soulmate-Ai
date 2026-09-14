"""
长期记忆存储层（跨会话）
========================
对标你刚手写的 FileChatMessageHistory —— 同样是「一个类管一份 JSON」，
同样用 @property 每次现读盘，只不过存的东西从「聊天消息」换成了「关于用户的事实」。

【在分层里的位置】
    config.py            ← 纯数据（多了一个 MEMORY_DIR）
    storage.py           ← 纯文件读写
    memory.py            ← 长期记忆读写 + 解析（本文件）★
    llm.py               ← 纯模型调用
    companion_manager.py ← 业务逻辑（调 memory 读、调 llm 抽、再调 memory 写）
    dialogs.py sidebar.py main.py ← 界面层

本文件不 import streamlit，也不 import llm —— 只管「怎么把记忆安全地存下来、读出来」。

【和「会话记忆」的分工 —— 这就是今天的重点】

                    会话记忆（已有）              长期记忆（本文件，新增）
存储路径    data/sessions/<伴侣id>/<会话id>.json    data/memory/<伴侣id>.json
归属对象    某一个「会话」                          某一个「伴侣」
作用范围    只在这个会话内                          跨会话、跨时间
换会话后    从零开始（伴侣「失忆」）                 还记得（伴侣「记得你」）
写入方式    每次对话自动 append 原文                 由模型抽取「值得记的事实」后追加
读取方式    main.py 里 messages[-history_length:]   to_prompt_block() → 拼进 system prompt

一句话：
    会话记忆记的是「这次聊了什么」；
    长期记忆记的是「你是谁」。

【为什么写入要用模型抽取，而不是直接把原文塞进去】
会话原文太长太杂，全塞进 system prompt 会：① 烧 token ② 淹没有效信息 ③ 每次都带一遍
三年前的一句"今天下雨了"。所以只留「稳定的、关于人的事实」：名字、职业、爱好、禁忌……
"""
import os
import uuid
from datetime import datetime

import storage
from config import MEMORY_DIR


# ══════════════════════════════════════════════════
# 给模型看的「抽取指令」（不是给用户看的）
# ══════════════════════════════════════════════════
# 设计要点：
#   1. 正反两面都举例 —— 只说"记什么"，模型会把闲聊也记进去
#   2. 强制 JSON 数组输出 —— 好解析；且明说"不要 markdown 代码块"
#      （很多模型习惯性包一层 ```json，必须在提示词里先堵死）
#   3. 没有就输出 [] —— 给它一个「什么都不记」的正当选项，
#      否则它会硬凑几条出来
EXTRACT_PROMPT = """你是记忆提取器。请从下面这段对话里，找出【值得长期记住的、关于用户的稳定事实】。

要记：名字与称呼、职业、所在地、家人与宠物、长期爱好、明确的喜好与禁忌、正在长期做的项目。
不要记：一次性的闲聊内容、助手自己说过的话、临时情绪、随时会变的小事。

只输出 JSON 数组，不要解释，不要 markdown 代码块。没有值得记的就输出 []。
示例：["用户叫李永康", "用户养了一只叫团子的猫", "用户不喝咖啡"]"""


def parse_extraction(raw: str) -> list:
    """把模型返回的文本解析成事实列表。

    模型不会 100% 听话，所以这里要兜三种情况：
        1. 包了 ```json ... ``` 代码块  → 剥壳
        2. 前后带解释文字               → 只取第一个 [ ... ] 区间
        3. 压根不是 JSON                → 返回 []（宁可不记，也不能崩）
    解析失败绝不能抛异常 —— 记忆是锦上添花，不该把聊天带崩。
    """
    import json
    if not raw:
        return []
    text = raw.strip()

    # 1. 剥掉 markdown 代码块
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]          # 去掉第一行的 ```json
        text = text.rsplit("```", 1)[0]         # 去掉结尾的 ```

    # 2. 只取第一个 [ ... ] 区间（防止模型前后加话）
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    text = text[start:end + 1]

    # 3. 解析
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if str(x).strip()]


# ══════════════════════════════════════════════════
# 记忆存储
# ══════════════════════════════════════════════════
class CompanionMemory:
    """某个伴侣的长期记忆：一串「TA 记得关于你的事」条目。

    用法（和 FileChatMessageHistory 一样，先建对象、再增删读）：
        mem = CompanionMemory(companion_id)
        mem.add(["用户叫李永康"], session_id="xxx")
        mem.facts              # 读全部
        mem.to_prompt_block()  # 拼成能塞进 system prompt 的一段
    """

    def __init__(self, companion_id: str):
        self.companion_id = companion_id
        self.file_path = os.path.join(MEMORY_DIR, f"{companion_id}.json")

    # ── 读 ──
    @property
    def facts(self) -> list:
        """读全部记忆条目（按记录顺序）。

        ⚠️ 刻意用 @property 每次现读盘、不做内存缓存 —— 和你手写的
        FileChatMessageHistory.messages 一个思路：
        状态的唯一来源是磁盘，就不可能出现「内存里改了、文件里没有」的不一致。
        （反过来，如果写成普通方法或缓存进 self._facts，就会出现
          「刚记住的事这一轮用不上、要等下次重启」这种隐蔽 bug。）
        """
        return storage.read_json(self.file_path, []) or []

    # ── 写 ──
    def add(self, texts, session_id=None) -> int:
        """追加若干条记忆，返回真正新增的条数。

        已存在的文本会被跳过 —— 否则「用户叫李永康」会被记一百遍
        （每轮对话都抽一次，模型很容易反复抽出同一件事）。
        """
        existing = self.facts
        seen = {(f.get("text") or "").strip() for f in existing}
        added = 0
        for t in texts or []:
            t = (t or "").strip()
            if not t or t in seen:
                continue
            existing.append({
                "id": uuid.uuid4().hex[:8],
                "text": t,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "session_id": session_id,      # 从哪个会话学来的（方便追溯）
            })
            seen.add(t)
            added += 1
        if added:
            storage.write_json(self.file_path, existing)
        return added

    def remove(self, fact_id: str) -> None:
        """忘掉某一条（用户在侧边栏点的 🗑️）"""
        storage.write_json(
            self.file_path,
            [f for f in self.facts if f.get("id") != fact_id],
        )

    def clear(self) -> None:
        """忘掉全部（直接删文件，比写空数组更干净）"""
        storage.delete_file(self.file_path)

    # ── 注入 ──
    def to_prompt_block(self, limit: int = 30) -> str:
        """拼成一段可以贴进 system prompt 的文本；没有记忆就返回空串。

        为什么要 limit：记忆条数会随时间无限增长，全部塞进 system prompt
        迟早烧光 token。取最近 limit 条 —— 越近的记忆通常越相关。
        """
        facts = self.facts[-limit:]
        if not facts:
            return ""
        lines = "\n".join(f"- {f.get('text', '')}" for f in facts)
        return (
            "\n\n【你记得关于 TA 的事】\n"
            f"{lines}\n"
            "像老朋友一样自然地把这些用上，但不要生硬地逐条复述。"
        )
