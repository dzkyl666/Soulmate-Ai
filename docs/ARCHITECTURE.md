# 架构说明

> 面向「想改这个项目」的人。读完你应该能回答：代码为什么这么分层、一个新功能该加在哪、哪些地方是刻意的取舍。

---

## 1. 分层总览

```
ui/  ─────────► services/ ─────────► llm/ · auth/
                    │                    │
                    └──────► storage/ ◄──┘
                                 │
                                 ▼
                              core/
```

**铁律：依赖只能向下，不许反向 import。**

| 层 | 允许依赖 | 禁止依赖 | 判断标准 |
|---|---|---|---|
| `core/` | 标准库 + pydantic/cryptography/bcrypt | 项目内任何其它层、streamlit | 能否被复制到别的项目直接用？ |
| `storage/` | `core/` | streamlit、services | 换数据库时是否只改这层？ |
| `llm/` | `core/` | streamlit、services | 换模型供应商时是否只改这层？ |
| `services/` | `core/` `storage/` `llm/` | streamlit、ui | 换界面时是否一行不用改？ |
| `auth/` | `core/` `storage/` | ui（OIDC 除外） | — |
| `ui/` | 全部 | — | 能不能整层替换成 FastAPI？ |

### 为什么这么严

分层一旦可以反向依赖，就必然退化成互相 import 的一团：单测没法只测一层、
换界面没法只换顶层。这个项目里有一条可验证的边界：

```bash
# 除了 llm/openai_provider.py，全项目不该出现第二处 import openai
grep -rn "^import openai\|^from openai" soulmate/ | grep -v openai_provider.py
# 除 ui/ 与 cli.py，不该出现 import streamlit
grep -rn "^import streamlit" soulmate/ | grep -v "^soulmate/ui/"
```

---

## 2. 每层的职责与关键文件

### `core/` —— 地基（零业务）

| 文件 | 职责 |
|---|---|
| `settings.py` | 全部配置的唯一来源。环境变量 + `.env`，带类型校验与生产自检 |
| `exceptions.py` | 分级异常体系 + `redact()` 脱敏 + `mask_secret()` |
| `logging.py` | 结构化日志、请求追踪 ID、滚动文件、异常记录 |
| `models.py` | 领域模型（Provider / Companion / SessionDoc / MemoryFact / Profile / UserRecord / ChatMessage） |
| `security.py` | Fernet 加解密、bcrypt 口令哈希、HMAC 签名令牌、密码强度 |
| `validation.py` | ID/URL/文本/用户名校验 + SSRF 防护 |
| `ratelimit.py` | 令牌桶限流（进程内） |
| `presets.py` | 厂商预设、emoji 清单、默认人设、记忆抽取提示词 |

**设计要点**

- `settings.py` **不在 import 时实例化**（`get_settings()` 才），所以 `import soulmate` 零副作用、测试能干净控制配置。
- `core/` 不 import streamlit，也不 import 项目内其它层 —— 所以它最容易测。
- 所有日志/异常出口都过 `redact()`，这是**纵深防御**：即使某处忘了脱敏也不会漏 Key。

### `storage/` —— 持久化

| 文件 | 职责 |
|---|---|
| `atomic.py` | 原子 JSON 读写（唯一临时文件 + fsync + `os.replace`）；损坏文件留证 |
| `cache.py` | `MTimeCache`：文件没变就不重复读盘 |
| `secrets.py` | 按 `app_secret` 缓存 `SecretBox`（避免重复跑 PBKDF2） |
| `repositories/*` | 按用户隔离的仓储：providers / companions / sessions / memory / profile / users |

**设计要点**

- **原子写**：旧版用固定 `path + ".tmp"`，并发写会互相踩踏。现在用 `tempfile.mkstemp`（同目录保证同卷），写完 `fsync` 再 `os.replace`。
- **加密只在这一层**：`ProviderRepository` 是唯一处理 `api_key_enc` 的地方。内存里是明文，磁盘上绝不出现明文。
- **缓存失效靠显式 + mtime 双保险**：写操作主动 `invalidate`，目录 mtime 变化兜住跨进程改动。

### `llm/` —— 模型接入

| 文件 | 职责 |
|---|---|
| `types.py` | `ChatMessage` 复用、`LLMResult`、流式事件协议（chunk/fallback/error/end） |
| `base.py` | `LLMProvider` 抽象接口 |
| `openai_provider.py` | **唯一 `import openai` 的文件** |
| `registry.py` | 按配置构造 provider（将来加厂商只改这里） |
| `retry.py` | SDK 异常 → 项目异常的分类；指数退避 + 抖动 |
| `service.py` | `LLMService`：降级链、上下文预算裁剪 |

**关键契约**

1. `provider.stream()` **绝不抛异常** —— 失败一律发 `ErrorEvent` 收尾，事件流总是「有始有终」。这样 UI 不用 try 生成器。
2. `LLMService.stream()` 负责降级链：主模型失败 → 发 `FallbackEvent`（UI 清掉半截回复）→ 备选模型接手 → 仍失败则 `ErrorEvent`。
3. **每次调用新建 client**：`timeout` 与 `max_retries` 在 SDK 里是构造期参数，每次请求诉求不同（对话 60s / 抽取 15s）。构造 client 不建连接，无额外开销。

### `services/` —— 业务编排

| 文件 | 职责 |
|---|---|
| `container.py` | `ServiceContainer`：把某用户的仓储 + 服务组装到一起 |
| `provider_service.py` | 模型服务 CRUD + 校验 + 占用检查 + 连通性测试 |
| `companion_service.py` | 伴侣/会话 CRUD、标题策略、系统提示词拼装、AI 起名 |
| `memory_service.py` | 长期记忆抽取、解析、去重入库、拼 prompt 块 |
| `export_service.py` | 会话导出（Markdown/JSON）、数据备份与轮转 |
| `metrics.py` | 按天用量统计（调用数/token/错误） |
| `diagnostics.py` | 只读巡检（版本、数据体积、安全状态、启动自检） |
| `migration.py` | v1/v0 → v2 数据迁移（幂等） |

**设计要点**

- `ServiceContainer` 是 UI 与业务的唯一交汇点。UI 拿一个容器就能干所有事。
- `set_llm()` 会把新的 LLM 服务**传播**给已构造的业务服务（它们构造时就抓住了引用 —— 这是个容易踩的坑）。
- 服务方法**不叫 `list`**：`list` 会在类命名空间里遮蔽内置 `list` 类型，导致该类内所有 `list[X]` 注解解析失败。所以是 `list_companions()` / `list_providers()`。

### `auth/` —— 身份

| 文件 | 职责 |
|---|---|
| `user_store.py` | 本机账号库：bootstrap / 注册 / 登录 / 停用 / 管理员开号 |
| `session.py` | 登录令牌签发与校验（HMAC，无服务端存储） |
| `oidc.py` | Streamlit 原生 `st.login()` 适配 + 身份→安全用户名映射 |

**设计要点**

- 登录失败**不区分**「用户不存在」与「密码错」—— 统一 `InvalidCredentials`，防用户名枚举。
- OIDC 身份映射成**安全用户名**：邮箱里的 `@`、`:` 不能做目录名（Windows 非法），所以取 `oidc_<本地部分>_<sha256前12位>`。
- 删账号**不删数据目录** —— 数据可能还要抢救，删错了找不回。

### `ui/` —— 表现

| 文件 | 职责 |
|---|---|
| `app.py` | 编排：配置自检 → 认证 → 容器+迁移 → 主题 → 路由 |
| `chat.py` | 聊天主区：流式渲染、降级提示、半截保留、重试、批量管理 |
| `sidebar.py` | 页面切换、主题、伴侣/会话/模型服务/记忆管理 |
| `dialogs.py` | 各类表单弹窗 |
| `settings_page.py` | 资料、导出、备份、用量、管理员面板 |
| `diagnostics_page.py` | 系统诊断与模型连通性测试 |
| `auth_page.py` | 登录/注册/首次初始化管理员 |
| `theme.py` | 4 套配色 + 持久化 + 反查一致性 |

**设计要点**

- `st.session_state` 是动态字典，取出来的值是 `Any`。UI 层统一在入口处**显式收敛类型**（`str(...)`、None 守卫），避免到处写 cast。
- 主题偏好存在 `profile.json`，启动时 `apply_saved_theme()` 先应用再 rerun —— 修掉了旧版「重启回默认主题」的问题。
- **`ui/boundary.py`：UI 边界只捕根异常 `SoulmateError`**，不逐个捕具体子类。
  理由是一次真实事故：`RateLimitError` 是 `SoulmateError` 的直接子类、**不是** `AuthError` 的子类，
  而登录页写的是 `except AuthError` → 用户连点登录触发限流时异常冒到 Streamlit，**整页红框**。
  边界捕根异常后，「新增一种异常」不再等于「新增一个页面崩溃入口」。
  `boundary.guard()` 把「捕什么、怎么显示」收敛到一处，三个认证表单 + 弹窗保存 + 消息校验共用它。
  已知的有意例外（`chat.py` 的记忆抽取静默、AI 起名按类型分级提示）在该模块 docstring 里写明。
  这条策略有 **AppTest 守着**：`TestLoginRateLimitOnPage` 连点 (限额+1) 次并断言
  「无异常 + 出现限流文案」—— 已实测「改回窄捕获它变红、修好它变绿」。

---

## 3. 一次聊天的完整数据流

```
用户在输入框敲字
   │
   ▼
ui/chat.py  _send_and_stream()
   │  ① 校验长度（core.validation.validate_text）
   │  ② 追加 user 消息 → services/companion_service.save_messages() 落盘
   ▼
services/companion_service.build_system_prompt()
   │  人设（含 {name}/{purpose} 替换）
   │  + 「正在和你聊天的人」昵称
   │  + services/memory_service.to_prompt_block()（现读现拼长期记忆）
   ▼
llm/service.LLMService.stream()
   │  ③ trim_to_context_budget() 按字符预算裁历史
   │  ④ registry.build(provider) → OpenAIProvider.stream()
   │  ⑤ 主模型失败 → FallbackEvent → 备选模型重试
   ▼
ui/chat.py 逐 chunk 渲染（st.empty + markdown 累积）
   │
   ├─ EndEvent  → 追加 assistant 消息落盘 → 触发记忆抽取 → st.rerun()
   ├─ ErrorEvent（有半截）→ 半截作为 assistant 消息落盘 + 提示中断
   └─ ErrorEvent（无内容）→ 显示错误 + 置 _retry，界面出现「↻ 重试」
```

记忆抽取在回复之后**同步**执行：用户已经看到回复了，这一下慢一点感觉不到。
抽取失败**静默**（记忆是锦上添花，不能把聊天带崩），且 `max_retries=0`
（实测默认重试 2 次会让失败路径白等 10 秒）。

---

## 4. 数据布局

```
data/
├── .app_secret                 # 非生产环境自动生成的根密钥（0600）
├── users.json                  # 账号列表（全局一份）
├── users/<用户名>/              # ★ 物理隔离边界
│   ├── providers.json          # 模型服务；api_key 存为 api_key_enc（Fernet 密文）
│   ├── companions.json         # 伴侣列表
│   ├── profile.json            # 昵称/头像/主题
│   ├── sessions/<伴侣id>/<会话id>.json
│   ├── memory/<伴侣id>.json
│   ├── metrics/usage.json
│   ├── backups/<时间戳-微秒>/
│   └── .migrated               # 迁移标记
└── logs/soulmate.log           # 滚动日志（5MB × 5）
```

- **用户隔离是物理的**：另一个用户即使拿到你的 `companion_id` 也读不到文件（路径不同）。
- `data/` 整个在 `.gitignore` 里；`.dockerignore` 也排除了它，不会进镜像。
- 会话 ID = `时间戳_8位随机串`：纯时间戳在同一秒连点两次会撞 ID；前缀保留时间戳所以字典序仍按时间排列。

---

## 5. 刻意的取舍（面试可讲）

| 决定 | 为什么不选另一条路 |
|---|---|
| **JSON 文件持久化** | 个人/小团队量级（几十用户）下，零依赖、可直接查看/备份/手改 的价值大于数据库。上万用户才需要换 —— 而换的时候只动 `storage/`。 |
| **进程内限流** | 单进程 Streamlit 够用，省一个 Redis 依赖。多副本部署时必须换，已在文档标注为显式边界。 |
| **无状态 HMAC 令牌** | 不引入服务端 session 表。代价：停用账号后已签发令牌最长还能用到 TTL 到期。 |
| **服务端注入 system prompt** | 不把昵称/记忆写进伴侣数据 —— 那些是会变的全局状态，写进去就要同步，现拼永远一致。 |
| **流式失败保留半截** | 比「丢弃重来」体验好：用户已经读到的内容是有效的。 |
| **`ErrorEvent` 而非抛异常** | 让 UI 的流式循环保持线性，不用 try 包生成器；也让「哪一步失败」成为数据而非控制流。 |
| **同步记忆抽取** | Streamlit 是全量重跑架构，没有常驻后台任务的位置。 |

---

## 6. 加功能该往哪加

| 想做的事 | 改哪里 |
|---|---|
| 支持新的模型厂商（非 OpenAI 兼容） | `llm/` 加一个 `Provider` 实现 + `registry.py` 加分支 |
| 接入 Function Calling / 工具调用 | `llm/base.py` 扩接口 + `openai_provider.py` 实现；UI 只多渲染工具结果 |
| 换成 SQLite/Postgres | 只改 `storage/repositories/`，上层的 `services/` 一行不用动 |
| 加「伴侣模板市场」 | `core/presets.py` 加数据 + `services/companion_service.py` 加逻辑 |
| 加新的页面 | `ui/` 加模块 + `ui/sidebar.py` 的路由加一项 |
| 加一个全局配置项 | `core/settings.py` 加字段 + `.env.example` 加注释 + 必要时 `validate_for_startup()` 加校验 |
| 加接口限流 | `core/ratelimit.py` 拿 `get_limiter()`，在 `services/` 里 `hit()` |
| 换掉 Streamlit 做 API 服务 | 复用 `services/` 与 `auth/`，只重写 `ui/` 为 FastAPI 路由 |

---

## 7. 代码质量基线

```bash
ruff check soulmate tests main.py   # 规范 + 安全规则（bandit 子集）
mypy soulmate main.py               # 类型检查（53 files，全绿）
pytest tests -q                     # 302 passed
```

- **ruff 规则集**：pycodestyle / pyflakes / isort / bugbear / comprehensions / pyupgrade / simplify / ruff / **bandit(S)** / print 禁令
- **mypy 配置**：`check_untyped_defs`、`no_implicit_optional`、`warn_unused_ignores`；UI 层放宽 `disallow_untyped_defs`（动态 session_state 收益低）
- **不强制 `ruff format`**：中文注释较多，格式化收益有限、churn 大。pre-commit 里保留了手动入口
- **CI 四道关**：ruff → mypy → pytest（3.11/3.12 双版本）→ 镜像可构建且容器健康