"""
伴侣管理器（核心业务层）
========================
对标参考项目 04-multibot 的 bot/bot_session_manager.py：
把所有"业务状态 + 落盘"收进一个类，界面层只调方法、不碰文件。

管理三样东西：
    模型服务 providers  —— 用户手填的 Base URL / API Key / 模型名（全项目共用一份池子）
    伴侣     companions —— 名称 / 用途 / 头像 / 系统提示词 / 绑定哪个模型服务
    会话     sessions   —— 每个伴侣一份独立目录，伴侣之间互不干扰

【磁盘结构】
    data/providers.json                     全部模型服务
    data/companions.json                    全部伴侣（不含聊天内容）
    data/sessions/<伴侣ID>/<会话ID>.json     每个会话一个文件

【一句话理解】界面层问它"当前伴侣是谁、有哪些会话"，它负责去磁盘拿、并保证存回来。
"""
import os
import uuid
from datetime import datetime

import llm
import storage
from memory import CompanionMemory, EXTRACT_PROMPT, parse_extraction
from config import (
    PROVIDERS_FILE,
    COMPANIONS_FILE,
    SESSIONS_DIR,
    PROFILE_FILE,
    LEGACY_SESSION_DIR,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_HISTORY_LENGTH,
    DEFAULT_AVATAR,
    DEFAULT_USER_NICKNAME,
    DEFAULT_USER_AVATAR,
)


class CompanionManager:
    def __init__(self):
        # ── 数据 ──
        self.providers = storage.read_json(PROVIDERS_FILE, []) or []      # 模型服务池
        self.companions = storage.read_json(COMPANIONS_FILE, []) or []    # 伴侣列表
        self.profile = storage.read_json(PROFILE_FILE, {}) or {}          # 使用者自己的昵称/头像（全局一份）

        # ── 当前状态（只活在本次浏览器会话里，刷新页面会重新从上面推导）──
        self.current_companion_id = None
        self.current_session_id = None
        self.pending_session = False   # True = 用户主动新建、还没落盘的会话
        self.manage_mode = False       # True = 消息批量管理模式
        self.history_length = DEFAULT_HISTORY_LENGTH
        self.memory_enabled = True     # True = 每轮对话后自动抽取长期记忆（侧边栏可关）

    # ══════════════════════════════════════════════════
    # 模型服务 providers
    # ══════════════════════════════════════════════════
    def save_providers(self):
        storage.write_json(PROVIDERS_FILE, self.providers)

    def add_provider(self, provider):
        provider["id"] = provider.get("id") or str(uuid.uuid4())
        provider["created_at"] = provider.get("created_at") or datetime.now().isoformat()
        self.providers.append(provider)
        self.save_providers()
        return provider

    def update_provider(self, provider):
        idx = next((i for i, p in enumerate(self.providers) if p["id"] == provider["id"]), -1)
        if idx == -1:
            self.providers.append(provider)
        else:
            self.providers[idx] = provider
        self.save_providers()

    def delete_provider(self, provider_id):
        self.providers = [p for p in self.providers if p["id"] != provider_id]
        self.save_providers()

    def get_provider(self, provider_id):
        return next((p for p in self.providers if p["id"] == provider_id), None)

    def provider_label(self, provider_id):
        """给界面用的显示名：别名 > 模型名 > 未绑定"""
        p = self.get_provider(provider_id)
        if not p:
            return "（未绑定模型）"
        return p.get("alias") or p.get("model") or "（未命名）"

    def provider_in_use(self, provider_id):
        """这个模型服务被几个伴侣绑着（删之前要拦一下）"""
        return [c["name"] for c in self.companions if c.get("provider_id") == provider_id]

    # 注意：原本这里的 resolve_api_key() + build_client() 已整体搬去 llm.py。
    # 从此业务层不再直接碰 openai SDK —— 想换厂商 / 加重试超时 / 接 Function Calling，
    # 都只改 llm.py 一处，不会在这里和 main.py 之间发散。

    # ══════════════════════════════════════════════════
    # 伴侣 companions
    # ══════════════════════════════════════════════════
    def save_companions(self):
        storage.write_json(COMPANIONS_FILE, self.companions)

    def add_companion(self, companion):
        companion["id"] = companion.get("id") or str(uuid.uuid4())
        companion["created_at"] = companion.get("created_at") or datetime.now().isoformat()
        self.companions.append(companion)
        self.save_companions()
        return companion

    def update_companion(self, companion):
        idx = next((i for i, c in enumerate(self.companions) if c["id"] == companion["id"]), -1)
        if idx == -1:
            self.companions.append(companion)
        else:
            self.companions[idx] = companion
        self.save_companions()

    def delete_companion(self, companion_id):
        """删伴侣：连它名下的整个会话目录一起删掉（不留孤儿文件）"""
        self.companions = [c for c in self.companions if c["id"] != companion_id]
        self.save_companions()
        storage.delete_dir(self.session_dir(companion_id))
        if self.current_companion_id == companion_id:
            self.current_companion_id = None
            self.current_session_id = None
            self.pending_session = False

    def get_companion(self, companion_id):
        return next((c for c in self.companions if c["id"] == companion_id), None)

    def current_companion(self):
        return self.get_companion(self.current_companion_id)

    def count_sessions(self, companion_id):
        return len(storage.list_json_names(self.session_dir(companion_id)))

    def switch_companion(self, companion_id):
        """切换伴侣：自动打开它最近的一个会话；一个都没有就开一个新的"""
        self.current_companion_id = companion_id
        sessions = self.list_sessions(companion_id)
        if sessions:
            self.current_session_id = sessions[0]["session_id"]
            self.pending_session = False
        else:
            self.current_session_id = self.new_session_id()
            self.pending_session = True
        self.manage_mode = False

    # ══════════════════════════════════════════════════
    # 使用者资料（全局一份，所有伴侣共用）
    # ══════════════════════════════════════════════════
    # 和「伴侣」的区别：伴侣是"对面那个人"，可以有多个；
    # 这里是"你"自己，只有一个，存在 data/profile.json，换伴侣、开会话都不变。
    def user_nickname(self):
        """聊天气泡上显示的名字。没设置过就回落到默认的「我」"""
        return (self.profile.get("nickname") or "").strip() or DEFAULT_USER_NICKNAME

    def user_avatar(self):
        """聊天气泡上显示的头像。没设置过就回落到默认值"""
        return (self.profile.get("avatar") or "").strip() or DEFAULT_USER_AVATAR

    def user_has_profile(self):
        """是否已经认真设置过（侧边栏用它决定要不要提示"还没设置"）"""
        return bool((self.profile.get("nickname") or "").strip())

    def update_profile(self, nickname, avatar):
        """合并写入：以后再加新字段（比如个性签名）不会把已有的弄丢"""
        self.profile = {**(self.profile or {}), "nickname": nickname, "avatar": avatar}
        storage.write_json(PROFILE_FILE, self.profile)

    def build_system_prompt(self, companion):
        """拼出这次请求要用的系统提示词。

        三件事：
        1. 老数据里可能还残留 {name} / {purpose} 占位符，顺手替换掉；
        2. 把「正在和你说话的是谁」告诉模型；
        3. 【新增】把长期记忆贴上去 —— 「跨会话还记得你」就靠这一步。

        第 2 点为什么必须在这里动态拼、而不写进伴侣数据里：
        昵称是**全局资料**（存在 profile.json），不属于任何一个伴侣。
        写进伴侣的话，改一次名字要把所有伴侣都改一遍，还会和新伴侣不一致。
        每次请求现拼，改完立刻生效，也永远不用回写伴侣数据。

        第 3 点也必须是「现拼」：记忆存在 data/memory/<伴侣id>.json，
        每轮对话后可能刚新增几条。现读现拼 → 刚记住的下一轮立刻生效，
        不需要重启、不需要额外同步逻辑。
        """
        prompt = (companion.get("system_prompt") or "").strip()

        if "{name}" in prompt or "{purpose}" in prompt:
            try:
                prompt = prompt.format(
                    name=companion.get("name", ""),
                    purpose=companion.get("purpose", ""),
                )
            except Exception:
                pass        # 用户自己写的花括号占位符，format 不了就原样留着

        if self.user_has_profile():
            prompt += (
                f"\n\n【正在和你聊天的人】对方的名字叫「{self.user_nickname()}」。"
                f"在合适的时候可以自然地称呼 TA 的名字，但不必每句话都叫。"
            )

        # ── 长期记忆注入（今天的重点）──
        # 读的是 data/memory/<伴侣id>.json，与当前会话无关 → 所以换会话也还记得。
        mem = self.memory(companion.get("id"))
        if mem:
            prompt += mem.to_prompt_block()

        return prompt

    # ══════════════════════════════════════════════════
    # 长期记忆（跨会话）
    # ══════════════════════════════════════════════════
    # 和「会话」的区别：
    #   会话记的是「这次聊了什么」—— 按 session_id 存，换个会话就没了；
    #   长期记忆记的是「你是谁」—— 按伴侣存，换会话、关程序、过一周都还在。
    def memory(self, companion_id=None):
        """拿到某个伴侣的记忆对象；没有伴侣时返回 None（界面层不用自己拼路径）"""
        cid = companion_id or self.current_companion_id
        return CompanionMemory(cid) if cid else None

    def memory_facts(self, companion_id=None):
        """读当前伴侣的全部长期记忆（侧边栏展示用）"""
        mem = self.memory(companion_id)
        return mem.facts if mem else []

    def forget_fact(self, fact_id, companion_id=None):
        """忘掉一条（侧边栏的 🗑️）"""
        mem = self.memory(companion_id)
        if mem:
            mem.remove(fact_id)

    def forget_all(self, companion_id=None):
        """全部忘掉"""
        mem = self.memory(companion_id)
        if mem:
            mem.clear()

    def remember_from_exchange(self, provider, companion, messages, keep_last=6):
        """从最近几轮对话里抽取「值得长期记住的事」写进长期记忆，返回新增条数。

        三个设计决定，都不是随手定的：

        ① 只喂最近 keep_last 条，不是全部历史
           历史越长越烧 token，而「该记住的事」通常就发生在刚才这几轮。
           更早的内容，前面几轮已经抽过一遍了。

        ② 抽取失败一律静默（except 直接吞）
           记忆是锦上添花。抽取用的是同一家的模型，网络一抖就会失败，
           绝不能因为它把「聊天」这条主流程带崩 —— 用户还在等回复。

        ③ 同步调用，不搞后台线程
           Streamlit 是「全量重跑」架构，没有常驻后台任务的位置。
           同步 + 极短输出（一次百来个 token），延迟可以接受。
        """
        if not provider or not companion or not messages:
            return 0
        recent = messages[-keep_last:]
        convo = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '伴侣'}：{m.get('content', '')}"
            for m in recent
        )
        try:
            raw = llm.chat(
                provider,
                [{"role": "user", "content": convo}],
                system_prompt=EXTRACT_PROMPT,
                temperature=0,        # 抽取要稳定、别发挥
                # 下面两个是实测加的：SDK 默认会重试 2 次，抽取一旦失败
                # 就白等 10 秒（用户已经拿到回复了，却卡在这）。
                # 抽取是"顺手做的事"，失败就放弃，不值得重试。
                timeout=15,
                max_retries=0,
            )
        except Exception:
            return 0                  # 见上面 ②

        mem = self.memory(companion.get("id"))
        if not mem:
            return 0
        return mem.add(parse_extraction(raw), session_id=self.current_session_id)

    # ══════════════════════════════════════════════════
    # 会话 sessions
    # ══════════════════════════════════════════════════
    def session_dir(self, companion_id=None):
        cid = companion_id or self.current_companion_id
        return os.path.join(SESSIONS_DIR, cid) if cid else ""

    @staticmethod
    def new_session_id():
        """会话 ID = 时间戳 + 8 位随机串。

        纯秒级时间戳有个洞：同一秒内连点两次「新建会话」会撞 ID，后者直接覆盖前者。
        补 8 位随机串（2^32 种）后碰撞概率可忽略；前缀仍是时间戳，所以字典序依然
        按时间排列（list_sessions 是按 session_id 倒序排的，这一点不能破坏）。
        """
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return f"{ts}_{uuid.uuid4().hex[:8]}"

    def list_sessions(self, companion_id=None):
        """列出某个伴侣的全部会话（按时间倒序），返回 [{session_id,title,updated_at,count}]"""
        cid = companion_id or self.current_companion_id
        if not cid:
            return []
        d = self.session_dir(cid)
        rows = []
        for sid in storage.list_json_names(d):
            data = storage.read_json(os.path.join(d, f"{sid}.json"), {}) or {}
            if not data.get("messages"):
                continue   # 空会话/损坏文件自动过滤
            rows.append({
                "session_id": sid,
                "title": data.get("title") or "新会话",
                "updated_at": data.get("updated_at") or sid,
                "count": len(data.get("messages", [])),
            })
        rows.sort(key=lambda r: r["session_id"], reverse=True)
        # 用户刚点「新建会话」、还没落盘的那个，临时插到最前面（pending 机制）
        if (companion_id is None and self.pending_session and self.current_session_id
                and self.current_session_id not in [r["session_id"] for r in rows]):
            rows.insert(0, {"session_id": self.current_session_id, "title": "新会话",
                            "updated_at": self.current_session_id, "count": 0})
        return rows

    def load_messages(self, session_id=None, companion_id=None):
        sid = session_id or self.current_session_id
        cid = companion_id or self.current_companion_id
        if not (sid and cid):
            return []
        data = storage.read_json(os.path.join(self.session_dir(cid), f"{sid}.json"), {}) or {}
        return data.get("messages", [])

    def save_session(self, messages, session_id=None, companion_id=None, title=None, title_source=None):
        """保存当前会话。

        标题的三种来源（优先级从高到低）：
            调用时传了 title      → 用它（title_source: "ai" 或 "manual"）
            文件里已经有 title    → 沿用，不覆盖
            都没有                → 自动取第一条用户消息前 20 字（"auto"）
        """
        sid = session_id or self.current_session_id
        cid = companion_id or self.current_companion_id
        if not (sid and cid):
            return
        path = os.path.join(self.session_dir(cid), f"{sid}.json")

        # 空会话不落盘：一条消息都没有就不写文件；之前写过就顺手删掉
        if not messages:
            storage.delete_file(path)
            return

        old = storage.read_json(path, {}) or {}
        if title is not None:
            final_title, final_source = title, (title_source or "manual")
        else:
            final_title = old.get("title") or self.auto_title(messages)
            final_source = old.get("title_source") or "auto"

        storage.write_json(path, {
            "session_id": sid,
            "title": final_title,
            "title_source": final_source,
            "created_at": old.get("created_at") or datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "messages": messages,
        })

    def delete_session(self, session_id, companion_id=None):
        cid = companion_id or self.current_companion_id
        if not cid:
            return
        storage.delete_file(os.path.join(self.session_dir(cid), f"{session_id}.json"))
        if session_id == self.current_session_id:
            # 删掉的是当前会话 → 换一个新的空会话（pending=False，侧边栏不会冒出空的）
            self.current_session_id = self.new_session_id()
            self.pending_session = False

    def rename_session(self, title, session_id=None, companion_id=None):
        sid = session_id or self.current_session_id
        cid = companion_id or self.current_companion_id
        if not (sid and cid):
            return
        path = os.path.join(self.session_dir(cid), f"{sid}.json")
        data = storage.read_json(path, {}) or {}
        data["title"] = title
        data["title_source"] = "manual"
        storage.write_json(path, data)

    def session_title(self, session_id=None, companion_id=None):
        sid = session_id or self.current_session_id
        cid = companion_id or self.current_companion_id
        if not (sid and cid):
            return "新会话"
        data = storage.read_json(os.path.join(self.session_dir(cid), f"{sid}.json"), {}) or {}
        return data.get("title") or "新会话"

    # ══════════════════════════════════════════════════
    # 消息操作（删单条 / 删多条）
    # ══════════════════════════════════════════════════
    @staticmethod
    def delete_messages(indices, messages):
        """按下标删除。从大到小删，避免删着删着后面的下标错位。"""
        for i in sorted(set(indices), reverse=True):
            if 0 <= i < len(messages):
                messages.pop(i)
        return messages

    # ══════════════════════════════════════════════════
    # 会话标题
    # ══════════════════════════════════════════════════
    @staticmethod
    def auto_title(messages):
        """自动取第一条用户消息当标题（对标 multibot 的 fix_history_names）"""
        for m in messages:
            if m.get("role") == "user":
                text = (m.get("content") or "").replace("\n", " ").strip()
                if text:
                    return text[:20] + ("…" if len(text) > 20 else "")
        return "新会话"

    def generate_ai_title(self, provider, messages):
        """让伴侣自己绑定的模型给当前会话起一个更精炼的标题（非流式，一次调用）"""
        if not provider:
            raise ValueError("这个伴侣还没绑定模型服务")
        # 只喂前 6 条，省 token，也够模型看懂主题了
        convo = "\n".join(
            f"{'用户' if m['role'] == 'user' else '伴侣'}：{m.get('content', '')}"
            for m in messages[:6]
        )
        title = llm.chat(
            provider,
            [{"role": "user", "content": convo}],
            system_prompt="你是标题生成器。用不超过 12 个字概括这段对话的主题，"
                          "只输出标题本身，不要引号、不要标点、不要解释。",
        )
        return title.strip("《》\"'。.、 ")

    # ══════════════════════════════════════════════════
    # 一次性迁移：老版本的单层 session/*.json
    # ══════════════════════════════════════════════════
    def migrate_legacy_sessions(self):
        """把老版本 session/<时间戳>.json 搬进 data/sessions/<新伴侣>/。

        只在「一个伴侣都还没有、且老目录里有文件」时跑一次，
        用旧文件里的昵称/性格还原出一个伴侣，聊天记录原样保留。
        """
        if self.companions or not os.path.isdir(LEGACY_SESSION_DIR):
            return
        files = [f for f in os.listdir(LEGACY_SESSION_DIR) if f.endswith(".json")]
        if not files:
            return

        first = storage.read_json(os.path.join(LEGACY_SESSION_DIR, sorted(files)[0]), {}) or {}
        name = first.get("nickname") or "我的伴侣"
        purpose = (first.get("nature") or "").strip()

        companion = self.add_companion({
            "name": name,
            "purpose": purpose,
            "avatar": DEFAULT_AVATAR,
            "system_prompt": DEFAULT_SYSTEM_PROMPT.format(name=name, purpose=purpose or "温柔体贴"),
            "provider_id": None,   # 还没有模型服务；界面会提示去绑定
        })
        cid_dir = self.session_dir(companion["id"])

        latest = None
        for fn in sorted(files):
            src = os.path.join(LEGACY_SESSION_DIR, fn)
            data = storage.read_json(src, {}) or {}
            msgs = data.get("messages") or []
            if not msgs:
                continue
            sid = fn[:-5]
            storage.write_json(os.path.join(cid_dir, f"{sid}.json"), {
                "session_id": sid,
                "title": self.auto_title(msgs),
                "title_source": "auto",
                "created_at": data.get("current_session") or sid,
                "updated_at": datetime.now().isoformat(),
                "messages": msgs,
            })
            latest = sid
            storage.delete_file(src)   # 搬完就删旧文件，避免下次重复迁移

        self.current_companion_id = companion["id"]
        self.current_session_id = latest or self.new_session_id()
        self.pending_session = False
