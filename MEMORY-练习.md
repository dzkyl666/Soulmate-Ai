# Soulmate-Ai · 长期记忆练习（对照敲指南）

> 日期：2026-09-14
> 对应课程：**P37 Memory 临时会话记忆** + **P38 Memory 长期会话记忆**（黑马《大模型 RAG 与 Agent 智能体项目实战》）
> 对应任务：本周表 9/16「动手：给 Soulmate-Ai 抽一个 llm.py 出来」
> 用法：**逐处自己敲**（改动已落地，可以 `git diff` 对照看；想重敲就 `git stash` 后照着本文敲）。
> 说明：本文按 FIX-P0.md 的格式写 —— 位置 / 改前 / 改后 / 为什么 / 自检。

---

## 0. 先对齐：今天学的，落到项目里是什么

| 今天学的（LangChain） | Soulmate-Ai 里对应的东西 | 本次状态 |
|---|---|---|
| **P37 临时会话记忆**<br>`InMemoryChatMessageHistory`、会话内有效 | `main.py` 里的 `messages[-mgr.history_length:]` 切片 | ✅ 本来就有<br>这次只是**显式化 + 讲清它的边界** |
| **P38 长期会话记忆**<br>`FileChatMessageHistory`、落盘、关程序还在 | ① 会话落盘 `data/sessions/<伴侣id>/<会话id>.json`（已有）<br>② **跨会话记忆 `data/memory/<伴侣id>.json`（本次新增）** | ⭐ 新增 |
| 「模型适配层」<br>`langchain_openai` 在 `langchain_core` 下面的位置 | **`llm.py`（本次新增）** | ⭐ 新增 |
| `MessagesPlaceholder` / 拼 messages 的顺序规则 | `llm.py` 里的 `_build_payload()` | ⭐ 新增 |

> **为什么这两个 Memory 概念在 LangChain 里是两个类，在这里也是两套文件**：
> 因为它们解决的是两个不同的问题 —— 一个管「这次聊了什么」，一个管「你是谁」。

---

## 1. 核心概念：会话记忆 ≠ 长期记忆

这是本次练习唯一需要真正想通的东西：

| | 会话记忆（已有） | 长期记忆（本次新增） |
|---|---|---|
| **存哪** | `data/sessions/<伴侣id>/<会话id>.json` | `data/memory/<伴侣id>.json` |
| **归属** | 某一个**会话** | 某一个**伴侣** |
| **范围** | 只在这个会话内 | 跨会话、跨时间 |
| **内容** | 每句话的**原文** | 模型**抽取**出的稳定事实 |
| **写入时机** | 每轮自动 append | 每轮对话后由模型判断该不该记 |
| **读取方式** | `messages[-N:]` 切片 | 拼进 system prompt |
| **换会话后** | ❌ 归零（伴侣"失忆"） | ✅ 还在（伴侣"记得你"） |
| **量级** | 会长到几百条 | 应该只有几十条 |

**一句话**：
> 会话记忆记的是「这次聊了什么」，长期记忆记的是「你是谁」。

**为什么长期记忆不用直接塞原文**：
会话原文又长又杂，全塞进 system prompt 会 ① 烧 token ② 淹没有效信息
③ 每次都带一遍三年前那句"今天下雨了"。所以只留稳定的、关于人的事实。

---

## 2. 改动总览

| 文件 | 类型 | 干什么 |
|---|---|---|
| **`llm.py`** | 🆕 新增 | 模型调用层。全项目唯一 `import openai` 的地方 |
| **`memory.py`** | 🆕 新增 | 长期记忆存储层。对标你手写的 `FileChatMessageHistory` |
| `config.py` | ✏️ 1 行 | 加 `MEMORY_DIR` |
| `companion_manager.py` | ✏️ 5 处 | 搬走 SDK 调用 + 加长期记忆业务方法 + 注入 system prompt |
| `main.py` | ✏️ 2 处 | 聊天段改走 `llm.py`，回复后触发记忆抽取 |
| `sidebar.py` | ✏️ 3 处 | 记忆面板（看/删/清空）+ 自动记忆开关 |

规模：`+165 / -56` 行（含新增的两个文件共 ~360 行）。

---

## 3. 逐处改法

### 3.1 🆕 新增 `llm.py` —— 模型调用层

#### 为什么要单独抽一层

改之前，模型调用**散在两个地方，各写一遍**：

```
companion_manager.py  →  build_client() + generate_ai_title() 里的 create()
main.py               →  聊天输入区里的 create() + 手写生成器
```

后果是「改一处忘一处」。而将来要加的东西**全是横切关注点**，会在两处同时发散：

- Function Calling（要传 `tools` / `tool_choice`）
- 超时与重试、生成参数（`temperature` / `max_tokens`）
- 厂商差异（有的兼容端点不认某些参数）
- 换 SDK / 换 LangChain

抽成一层后，这些只改 `llm.py` 一处。

#### 边界在哪（重要）

`llm.py` 只认两样东西：

1. `provider` 配置字典（`base_url` / `api_key` / `model` / `preset`）
2. 标准 `messages` 列表（`[{"role": "user", "content": "..."}]`）

**不认识**「伴侣」「会话」「人设」这些业务概念。边界清楚才好替换。

#### 对外四个函数

| 函数 | 作用 |
|---|---|
| `resolve_api_key(provider)` | 取 Key：界面填的优先，没填回退环境变量 |
| `build_client(provider, max_retries=None)` | 建 OpenAI 客户端（**必须显式传 base_url**） |
| `chat(provider, messages, system_prompt=None, ...)` | 非流式，返回纯文本 |
| `stream_chat(provider, messages, system_prompt=None, ...)` | 流式，逐字 yield |

两个细节值得记：

**① `system_prompt` 单独抽成参数**，而不是让调用方自己往 list 里塞：

```python
def _build_payload(messages, system_prompt=None):
    payload = []
    if system_prompt:
        payload.append({"role": "system", "content": system_prompt})
    payload.extend(messages)
    return payload
```

这样「system 永远排最前面」这条格式规则**只存在于一个地方**。
（对应 LangChain 里 `ChatPromptTemplate.from_messages([("system", ...), MessagesPlaceholder(...), ("human", ...)])` 的写法 —— 那里也是把 system 和 history 分开声明的。）

**② 流式要防「空 choices」**：

```python
for chunk in response:
    if not chunk.choices:      # ← 没有这一行，会 IndexError
        continue
    content = chunk.choices[0].delta.content
    if content:
        yield content
```

部分厂商在流末尾会多发一个只有用量统计、没有 `choices` 的 chunk。

---

### 3.2 🆕 新增 `memory.py` —— 长期记忆存储层

**这个文件就是把今天的 `FileChatMessageHistory` 换个存储内容写一遍。**

对照着看：

| 你手写的 `FileChatMessageHistory` | 这里的 `CompanionMemory` |
|---|---|
| `__init__(self, session_id, storage_path)` | `__init__(self, companion_id)` |
| `self.file_path = os.path.join(storage_path, session_id)` | `self.file_path = os.path.join(MEMORY_DIR, f"{companion_id}.json")` |
| `@property messages` → 读盘还原消息 | `@property facts` → 读盘还原事实 |
| `add_messages()` → append 后写盘 | `add()` → 去重后写盘 |
| `clear()` | `clear()` |
| — | `to_prompt_block()` ← **多出来的：拼成能塞进 system prompt 的文本** |

#### 关键点 1：`@property` 每次现读盘，不做内存缓存

```python
@property
def facts(self) -> list:
    return storage.read_json(self.file_path, []) or []
```

为什么和 `FileChatMessageHistory.messages` 一样用 `@property`：

- **状态的唯一来源是磁盘** → 不可能出现「内存里改了、文件里没有」的不一致
- 反例：如果缓存进 `self._facts`，就会出「刚记住的事这一轮用不上、要等下次重启」这种隐蔽 bug

#### 关键点 2：`add()` 要去重

```python
seen = {(f.get("text") or "").strip() for f in existing}
if t in seen:
    continue
```

因为每轮对话都抽一次，模型**很容易反复抽出同一件事**。不去重的话
「用户叫李永康」会被记一百遍。

#### 关键点 3：`parse_extraction()` 要兜住模型不听话

模型不会 100% 输出合法 JSON。实测的 8 种情况：

| 模型返回 | 解析结果 |
|---|---|
| `["用户叫李永康", "用户养猫"]` | ✅ 正常 |
| ` ```json\n["用户叫李永康"]\n``` ` | ✅ 剥掉代码块 |
| `好的，提取结果如下：["用户叫李永康"] 希望有帮助` | ✅ 只取 `[ ... ]` 区间 |
| `[]` | ✅ 空列表 |
| `没有值得记住的信息` | ✅ `[]` |
| `{"name": "李永康"}` | ✅ `[]`（不是数组） |
| `""` | ✅ `[]` |

**核心原则：解析失败返回空列表，绝不抛异常。**
记忆是锦上添花，不该因为它把聊天带崩。

---

### 3.3 `config.py` —— 加一行

**位置：路径区，`SESSIONS_DIR` 下面**

```python
# 改前
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")             # 每个伴侣一个子目录
PROFILE_FILE = os.path.join(DATA_DIR, "profile.json")         # 使用者自己的昵称/头像（全局一份，不分伴侣）

# 改后
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")             # 每个伴侣一个子目录
MEMORY_DIR = os.path.join(DATA_DIR, "memory")                 # 长期记忆：每个伴侣一个文件（跨会话）
PROFILE_FILE = os.path.join(DATA_DIR, "profile.json")         # 使用者自己的昵称/头像（全局一份，不分伴侣）
```

> `data/` 已经在 `.gitignore` 里 → 记忆文件不会被提交，隐私安全。

---

### 3.4 `companion_manager.py` —— 5 处

#### ① 顶部 import 区

```python
# 改前
import os
import uuid
from datetime import datetime

import storage
from config import (
    ...
    PROVIDER_PRESETS,          # ← 这行也要删
    ...
)

# 改后
import os
import uuid
from datetime import datetime

import llm
import storage
from memory import CompanionMemory, EXTRACT_PROMPT, parse_extraction
from config import (
    ...
    # PROVIDER_PRESETS 删掉 —— 它唯一的用处（取 Key）已经搬去 llm.py
)
```

#### ② 删掉 `resolve_api_key()` 和 `build_client()`

**位置：原第 92–112 行，整段删掉**

```python
# 删掉的（两个方法，共 21 行）
    @staticmethod
    def resolve_api_key(provider):
        """取 Key：优先界面里填的；没填就回退到同名环境变量（兜底，不算白配）"""
        ...
    def build_client(self, provider):
        """按 provider 建 OpenAI 客户端。..."""
        ...
```

换成一行注释说明它搬去哪了：

```python
    # 注意：原本这里的 resolve_api_key() + build_client() 已整体搬去 llm.py。
    # 从此业务层不再直接碰 openai SDK —— 想换厂商 / 加重试超时 / 接 Function Calling，
    # 都只改 llm.py 一处，不会在这里和 main.py 之间发散。
```

#### ③ `generate_ai_title()` 改用 `llm.chat()`

**位置：文件末尾附近**

```python
# 改前（21 行）
    def generate_ai_title(self, provider, messages):
        if not provider:
            raise ValueError("这个伴侣还没绑定模型服务")
        client = self.build_client(provider)
        convo = "\n".join(...)
        resp = client.chat.completions.create(
            model=provider.get("model"),
            messages=[
                {"role": "system", "content": "你是标题生成器。..."},
                {"role": "user", "content": convo},
            ],
            stream=False,
        )
        return (resp.choices[0].message.content or "").strip().strip("《》\"'。.、 ")

# 改后（11 行）
    def generate_ai_title(self, provider, messages):
        if not provider:
            raise ValueError("这个伴侣还没绑定模型服务")
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
```

**注意最后一行**：`llm.chat()` 内部已经 `.strip()` 过了，这里只需要再剥掉标点。

#### ④ `build_system_prompt()` 加长期记忆注入

**位置：`return prompt` 之前**

```python
# 改后（新增 6 行）
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
```

docstring 也从「两件事」改成「三件事」。

**为什么必须在这里现拼，而不是缓存下来**：
记忆文件每轮对话后都会新增。现读现拼 → **刚记住的下一轮立刻生效**，不需要重启、不需要额外同步逻辑。
（这和原来昵称那段是同一个道理 —— 那段注释里已经写过了。）

#### ⑤ 新增长期记忆业务方法（一整段）

**位置：`build_system_prompt()` 之后、`# 会话 sessions` 之前**

```python
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
        """从最近几轮对话里抽取「值得长期记住的事」写进长期记忆，返回新增条数。"""
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
                timeout=15,
                max_retries=0,
            )
        except Exception:
            return 0

        mem = self.memory(companion.get("id"))
        if not mem:
            return 0
        return mem.add(parse_extraction(raw), session_id=self.current_session_id)
```

**`remember_from_exchange` 的四个设计决定**（都写进 docstring 了，面试能讲）：

| # | 决定 | 理由 |
|---|---|---|
| ① | 只喂最近 6 条，不是全部历史 | 历史越长越烧 token；「该记住的事」通常就在刚才这几轮；更早的前几轮已经抽过 |
| ② | 失败一律静默（`except` 吞掉） | 记忆是锦上添花。网络一抖就失败，绝不能把「聊天」主流程带崩 |
| ③ | 同步调用，不搞后台线程 | Streamlit 是「全量重跑」架构，没有常驻后台任务的位置。实测正常路径 0.7s，可接受 |
| ④ | `timeout=15` + `max_retries=0` | **这条是实测加出来的** —— SDK 默认重试 2 次，抽取一旦失败要**白等 10 秒**（见 §5） |

#### ⑥ `__init__` 加一个开关字段

```python
        self.manage_mode = False       # True = 消息批量管理模式
        self.history_length = DEFAULT_HISTORY_LENGTH
        self.memory_enabled = True     # True = 每轮对话后自动抽取长期记忆（侧边栏可关）
```

---

### 3.5 `main.py` —— 2 处

#### ① 顶部加 import

```python
import streamlit as st

import llm                                    # ← 新增
from theme import THEMES, apply_theme, current_theme_name
```

#### ② 聊天段改成「三段式」

**位置：`if prompt := st.chat_input(...)` 那一段**

```python
# 改前（24 行，SDK 调用裸露在这里）
    try:
        client = mgr.build_client(provider)
        history_messages = messages[-mgr.history_length:]
        response = client.chat.completions.create(
            model=provider.get("model"),
            messages=[{"role": "system", "content": sys_prompt}, *history_messages],
            stream=True,
        )

        def generate_response():
            for chunk in response:
                content = chunk.choices[0].delta.content
                if content:
                    yield content

        with st.chat_message(assistant_name, avatar=assistant_avatar):
            full_response = st.write_stream(generate_response)

        messages.append({"role": "assistant", "content": full_response})
        mgr.save_session(messages)

    except Exception as e:
        st.error(f"❌ 与 AI 通信发生错误: {e}")

# 改后（三段，各管一件事）
    try:
        # ── ① 会话记忆（临时）──
        history_messages = messages[-mgr.history_length:]

        # ── ② 模型调用 ──
        with st.chat_message(assistant_name, avatar=assistant_avatar):
            full_response = st.write_stream(
                llm.stream_chat(provider, history_messages, system_prompt=sys_prompt)
            )

        messages.append({"role": "assistant", "content": full_response})
        mgr.save_session(messages)

        # ── ③ 长期记忆（跨会话）──
        if mgr.memory_enabled:
            mgr.remember_from_exchange(provider, companion, messages)

    except Exception as e:
        st.error(f"❌ 与 AI 通信发生错误: {e}")
```

**这一段的注释是全项目最值得看的地方** —— 它把两个 Memory 概念摆在同一屏：

```python
        # 这行切片就是"会话内记忆"的全部实现：把最近 N 条历史带上。
        # 切片的范围只限当前 messages —— 换个会话，messages 换了一批，
        # 伴侣就"忘了"。所以它只在会话内有效。
        history_messages = messages[-mgr.history_length:]
```

**为什么 ③ 放在回复之后**：用户已经看到回复了，抽取这一下慢一点也感觉不到。
如果放前面，用户就得为「记忆」多等 0.7 秒。

---

### 3.6 `sidebar.py` —— 3 处

#### ① 两个回调（放在其他回调旁边）

```python
def forget_fact(fact_id):
    """让当前伴侣忘掉某一条长期记忆"""
    st.session_state["manager"].forget_fact(fact_id)


def forget_all_facts():
    """清空当前伴侣的全部长期记忆"""
    st.session_state["manager"].forget_all()
```

> 注意函数名用 `forget_all_facts`，不和 `mgr.forget_all` 撞名，
> 避免看代码时混淆「这是回调」还是「这是业务方法」。

#### ② 聊天设置里加开关

```python
            mgr.memory_enabled = st.toggle(
                "自动记住关于我的事", value=mgr.memory_enabled,
                help="每轮对话后让模型挑出值得长期记住的事（会多花一次很小的模型调用）",
            )
```

#### ③ 新增「🧠 TA 记得你的事」折叠块

**位置：聊天设置之后、我的资料之前**

```python
        with st.expander("🧠 TA 记得你的事", expanded=False):
            cc_mem = mgr.current_companion()
            if not cc_mem:
                st.caption("先创建一个伴侣")
            else:
                facts = mgr.memory_facts()
                if not facts:
                    st.caption("还没记住什么。多聊几句，重要的信息会自动记下来。")
                for f in facts:
                    col1, col2 = st.columns([5, 1])
                    with col1:
                        st.caption(f.get("text", ""))
                    with col2:
                        st.button("", icon="🗑️", key=f"forget_{f['id']}",
                                  help="让 TA 忘掉这条",
                                  on_click=forget_fact, args=(f["id"],))
                if facts:
                    st.button("🧹 清空全部记忆", width="stretch", key="forget_all_btn",
                              on_click=forget_all_facts)
```

---

## 4. 实测证据

全部在**项目副本**里跑，你的真实 `data/` 一个字没动。

### 4.1 `parse_extraction` 边界（8/8 通过）

```
正常数组         -> ['用户叫李永康', '用户养猫']
包了代码块        -> ['用户叫李永康']
前后带解释        -> ['用户叫李永康']
空数组          -> []
纯文字无数组       -> []
空字符串          -> []
非数组 JSON     -> []
数组里混空串       -> ['用户叫李永康', '养猫']
```

### 4.2 `CompanionMemory` 读写

```
初始（文件不存在）: []
add 3 条（含 1 条重复）-> 实际新增 2 条          ← 去重生效
每条带 id/时间/session: {"id": "6b5d5959", "text": "用户叫李永康", ...}
删掉第 1 条后: ['用户养了一只叫团子的猫']
clear 后: [] | 文件还在吗: False
```

### 4.3 真实抽取（qwen-turbo）

输入：
```
用户：我叫李永康，最近在学 LangChain，还养了一只叫团子的猫
伴侣：记住啦～团子听起来很可爱
用户：今天天气还不错，随便聊聊
```

抽出：
```
- 用户叫李永康
- 用户养了一只叫团子的猫
```

✅ **正确忽略了「今天天气还不错」**（一次性闲聊），这正是 prompt 里正反举例的作用。

### 4.4 ★ 跨会话验证（今天的核心命题）

```
当前会话 s-real 的消息数: 2
切到另一个会话后 → 会话消息: 0 条        ← 会话记忆归零
但长期记忆仍: ['用户叫李永康', '用户养了一只叫团子的猫', '用户是软件测试员', '用户在学 LangChain']
新会话的 system prompt 仍带记忆: True     ← 长期记忆生效 ✓
```

### 4.5 最强证据：伴侣真的"用上"了记忆

一次真实聊天（记忆里已有"团子"），伴侣的**第一句话**是：

```
嗯，团子又在沙发上打盹了，你今天测试得怎么样？LangChain 学得顺利吗？
```

**在全新的一轮对话里主动提到了猫的名字** —— 说明注入链路端到端通了。

### 4.6 Streamlit 应用级验证（AppTest）

```
at.exception: 无 ✓
侧边栏 expander: 我的伴侣 / 会话历史 / 模型服务 / 聊天设置 / 🧠 TA 记得你的事
记忆文本渲染: 用户叫李永康 ✓ / 用户养了一只叫团子的猫 ✓
forget_* 按钮数: 3 ['forget_m1', 'forget_m2', 'forget_all_btn']
侧边栏 toggle: [('管理消息（批量删除）', False), ('自动记住关于我的事', True)]
主区 error / warning: 无 ✓
```

### 4.7 抽取失败必须静默（含性能优化）

| | 优化前 | 优化后 |
|---|---|---|
| provider 端口不通 | 未抛异常 ✓ 但**耗时 10.1s** | 未抛异常 ✓ 耗时 **5.0s** |
| 正常路径 | 0.7s | 0.7s |

原因：OpenAI SDK 默认 `max_retries=2`，三次尝试 × ~3s。
抽取是「顺手做的事」，失败重试毫无意义 → `max_retries=0`。

> ⚠️ 留下的 5s 是 httpx 的 connect timeout，属于底层默认行为。
> 只在「模型服务完全挂掉」时才会遇到，可以接受。

---

## 5. 自测清单（自己动手验）

- [ ] `streamlit run main.py` 能起来，无明显报错
- [ ] 侧边栏出现「🧠 TA 记得你的事」折叠块
- [ ] 跟伴侣说一句「我叫 XXX，在做软件测试」→ 等回复
- [ ] 展开记忆面板 → 应该出现「用户叫 XXX」
- [ ] **点「➕ 新建会话」→ 再打开记忆面板 → 记忆还在**（核心验证）
- [ ] 在新会话里问它「你知道我是谁吗」→ 应该能答上来
- [ ] 去 `data/memory/<伴侣id>.json` 看文件，应该是可读的中文 JSON
- [ ] 点某条记忆的 🗑️ → 那条消失且文件同步变化
- [ ] 关掉「自动记住关于我的事」开关 → 再聊天 → 记忆不再增加
- [ ] 故意把 API Key 改错 → 聊天报错，但**不应该多卡 10 秒**
- [ ] `data/` 下不该出现 `.tmp` 残留文件

---

## 6. 面试能讲什么（把这段背下来）

**问：这个项目的长期记忆是怎么做的？**

> 分两层。会话记忆是原始的最近 N 条消息切片，存在会话 JSON 里，换个会话就归零。
> 长期记忆我单独做了一个 `memory.py`，按伴侣存一份 `memory.json`。
> 写入不是简单 append 原文，而是每轮对话后让模型做一次抽取 —— 只留「稳定的、关于人的事实」，
> 因为原文太长太杂，全塞进 system prompt 会烧 token 也会淹没有效信息。
> 读取时在 `build_system_prompt` 里现读现拼，所以刚记住的下一轮立刻生效。
> 三个细节：抽取失败静默处理，因为记忆是锦上添花不能把聊天带崩；
> 加了 `max_retries=0`，实测把失败路径从 10 秒降到 5 秒；
> 抽取做了去重，否则每轮都会把同一件事记一遍。

**问：为什么把模型调用抽成 `llm.py`？**

> 因为它是一层横切关注点。抽之前 SDK 调用散在业务层和界面层两处，
> 而后面要加的东西——Function Calling 的 tools 参数、超时重试、生成参数、厂商差异——
> 全都是两边同时发散。抽成一层后，业务层完全不 import openai。
> 这个分层的思路和 LangChain 里 `langchain_core`（通用层）/
> `langchain_openai`（厂商适配层）是一样的：业务逻辑写在通用层，换模型只动适配层。

---

## 附：本次练习和你手写的 `FileChatMessageHistory` 的对应关系

这是最有价值的一条对照 —— 你把课程案例的代码"搬家"到了真实项目里：

| 课程案例（`day1.py`） | 本项目 |
|---|---|
| 存聊天消息（`List[BaseMessage]`） | 存关于用户的事实（`List[dict]`） |
| `session_id` 定位文件 | `companion_id` 定位文件 |
| `@property messages` 现读盘 | `@property facts` 现读盘 |
| `add_messages()` 写盘 | `add()` 去重后写盘 |
| `clear()` | `clear()` |
| `RunnableWithMessageHistory` 自动把历史喂给链 | `build_system_prompt()` 手动把记忆拼进 system prompt |
| `InMemoryChatMessageHistory`（进程内存） | `st.session_state["messages"]`（浏览器会话内存） |

**差别只剩一个**：LangChain 用 `RunnableWithMessageHistory` 把「取历史/存历史」自动化了；
本项目因为没引 LangChain，这两步是手写的。**但机制完全一样** —— 这就是为什么值得先手写一遍。
