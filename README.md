# 🧸 Soulmate AI

> 多伴侣 · 多模型 · 多用户的 AI 聊天应用。
> 一个 Key 接任意 OpenAI 兼容大模型，给每个「伴侣」独立人设、独立模型（可配备选降级）、独立聊天记录与跨会话长期记忆。
> **数据全部留在你自己的服务器上**，API Key 加密落盘。

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.63-FF4B4B?logo=streamlit&logoColor=white)
![Tests](https://img.shields.io/badge/tests-223%20passed-3fb950)
![Ruff](https://img.shields.io/badge/lint-ruff-261230)
![Mypy](https://img.shields.io/badge/type-mypy-2A6DB2)
![License](https://img.shields.io/badge/license-MIT-blue)

这是我自学 Python 之后做的完整 AI 应用项目。**v2 版本**已经从「单文件 demo」重构成了分层包化的生产级应用。

---

## 目录

- [这是什么](#这是什么)
- [v1 → v2 改了什么](#v1--v2-改了什么)
- [核心概念：三层结构](#核心概念三层结构)
- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [部署到服务器](#部署到服务器)
- [项目结构](#项目结构)
- [架构与分层](#架构与分层)
- [开发与测试](#开发与测试)
- [数据与安全](#数据与安全)
- [排错](#排错)
- [已知限制](#已知限制)
- [文档索引](#文档索引)

---

## 这是什么

大部分聊天 App 是「一个 AI，一种性格」。这个项目想验证另一种做法：

- **一个 Key，多个大脑** —— API Key 只填一次，之后可以在不同伴侣身上指派不同厂商的模型（硅基流动 / OpenAI / Claude / 通义 / DeepSeek / 智谱 / Kimi / Gemini……）
- **一个应用，多个人设** —— 工作伴侣、睡眠伴侣、旅游伴侣各自有独立的系统提示词和聊天记录，互不打扰
- **主模型挂了自动切备选** —— 每个伴侣可配 fallback 模型，主模型超时/限流时自动降级
- **跨会话的长期记忆** —— 伴侣记得「你是谁」，换会话、关程序、过一周都还在
- **多用户 + 物理隔离** —— 每个账号的数据在独立目录，互相看不到；API Key 加密存储
- **数据全在自己机器上** —— 就是本地几个 JSON 文件，随时能看、能备份、能删

---

## v1 → v2 改了什么

v1 是一个 8 文件的平铺 demo（已归档在 `legacy/`）。v2 是一次**面向生产**的重构：

| 维度 | v1 | v2 |
|---|---|---|
| 架构 | 8 个平铺模块 | `soulmate/` 包，6 层严格分层（core/storage/llm/services/auth/ui） |
| 用户 | 单用户，无登录 | 多用户 + 登录（本机账号或 OIDC），数据目录物理隔离 |
| Key 存储 | 明文 JSON | **Fernet 加密落盘**，界面只回显尾 4 位 |
| 错误处理 | 9 处 `except: pass` 静默吞 | 分级异常体系 + 结构化日志 + 给用户的安全文案 |
| 日志 | 无 | 结构化日志 + 请求追踪 ID + 滚动文件 |
| 模型调用 | 单层，失败即报错 | Provider 抽象 + 错误分类 + **降级链** + 可恢复流式 |
| 性能 | 每次 rerun 全量读盘 | mtime 缓存 + 目录列表缓存 + 上下文预算裁剪 |
| 安全 | 无校验，可填内网地址 | 输入校验 + SSRF 防护 + 限流 + 生产配置 fail-fast |
| 可靠性 | 流式中断丢回复 | 半截回复保留落盘 + 重试按钮 |
| 质量 | 20 个用例 | **223 个用例**（含 UI 冒烟与数据安全网）+ ruff + mypy 全绿 |
| 交付 | 手工 `streamlit run` | Dockerfile + compose + GitHub Actions CI + pre-commit |

新增能力：模型降级链、会话导出（Markdown/JSON）、数据备份与轮转、用量统计、系统诊断页、
管理员账号面板、命令行工具（`soulmate doctor / create-user / migrate`）。

---

## 核心概念：三层结构

理解这三层，整个应用就通了。**这也是第一次使用时最容易卡住的地方**：

| 层 | 是什么 | 类比 | 数量关系 |
|---|---|---|---|
| **模型服务** | 一组连接信息：Base URL + API Key + 模型名 | 大脑 | 一个 Key 可被多个伴侣共用 |
| **伴侣** | 名字 + 头像 + 用途 + 系统提示词 + 绑定的模型（可配备选） | 人设壳子 | 想建几个建几个 |
| **会话** | 一段聊天记录，隶属于某个伴侣 | 笔记本 | 每个伴侣各自一组，互不可见 |

聊天 = 在**某个伴侣**的**某个会话**上写字。

> **为什么模型要手动绑定？** 因为一个 Key 可能被好几个伴侣共用，程序没法替你猜。

### 两套「记忆」别搞混

| | 会话记忆 | 长期记忆 |
|---|---|---|
| 存哪 | `sessions/<伴侣>/<会话>.json` | `memory/<伴侣>.json` |
| 归属 | 某一个**会话** | 某一个**伴侣** |
| 范围 | 只在这个会话内 | 跨会话、跨时间 |
| 内容 | 对话**原文** | 模型**抽取**的稳定事实 |
| 换会话后 | ❌ 归零 | ✅ 还在 |

一句话：**会话记忆记的是「这次聊了什么」，长期记忆记的是「你是谁」。**

---

## 功能特性

### 伴侣

- **多伴侣**：任意多个，各自独立人设与聊天记录
- **模型先选后建**：新建时先挑模型服务，再填人设，一步绑定
- **备选模型（降级链）**：主模型失败自动切备选，界面提示「已切换」
- **自定义头像**：内置 40 个 emoji，也支持粘贴任意 emoji

### 模型接入（自带 Key，界面填写）

- **9 家厂商预设**：硅基流动 / OpenAI / Anthropic / Gemini / 通义千问 / DeepSeek / Kimi / 智谱 / 自定义
- **选预设自动带出地址与常用模型名**，只需补 Key
- **环境变量兜底**：Key 留空时读取同名环境变量（如 `SILICONFLOW_API_KEY`）
- **加密存储**：落盘是密文，界面只显示 `••••••1234`
- **一键连通性测试**：诊断页里真发一次最小请求，排查「为什么不回话」

### 会话

- 按伴侣隔离、自动标题（取首句）、✨ AI 起名、✏️ 手动改名
- 空会话不落盘，不留垃圾文件
- **导出**：Markdown（给人看）/ JSON（给程序读）
- 单条删除 + 批量管理模式

### 长期记忆

- 每轮对话后由模型抽取「关于你的稳定事实」，去重、限量、跨会话生效
- 侧边栏「🧠 TA 记得你的事」可逐条删、可一键清空、可整体关掉（省一次调用）

### 多用户与安全

- 首个账号自动成为管理员；可关闭自助注册，改用管理员面板或命令行开号
- 登录态用 HMAC 签名令牌；密码 bcrypt 哈希
- 每个用户数据在 `data/users/<用户名>/` 下**物理隔离**
- 接口限流（聊天/抽取/登录各自独立配额）
- 生产环境配置不合格会**拒绝启动**（弱密钥、开放注册、debug 开启）

### 界面与体验

- 4 套主题（月白 / 玄夜 / 樱粉 / 天青），偏好持久化，重启不丢
- 流式打字机输出；中断时保留已生成内容并可一键重试
- 设置页：资料、导出、备份、用量统计、管理员面板
- 诊断页：版本、数据体积、安全状态、模型连通性

---

## 快速开始

### 环境要求

- **Python 3.11+**（实测 3.12.13）
- 任意一家 OpenAI 兼容服务的 **API Key**（推荐硅基流动，有免费额度）

### 安装与运行

```bash
git clone https://github.com/dzkyl666/Soulmate-Ai.git
cd Soulmate-Ai

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
streamlit run main.py
```

浏览器会自动打开 `http://localhost:8501`。

### 首次使用（三步）

1. **创建管理员账号** —— 首屏表单，第一个账号自动是管理员
2. **添加模型服务** —— 侧边栏「模型服务」→ 选厂商预设 → 补 API Key → 保存
3. **创建伴侣** —— 侧边栏「伴侣」→ ➕ 新建伴侣 → 选模型、起名字、写人设

> 本地开发**不需要配任何环境变量**。根密钥会自动生成到 `data/.app_secret`。

---

## 部署到服务器

> ⚠️ v1 的说明写着「请勿部署到公网」。v2 已具备部署所需的安全基线（认证、隔离、加密、限流、fail-fast），
> 但请务必先读 [docs/SECURITY.md](docs/SECURITY.md) 的部署清单。

### 方式一：Docker Compose（推荐）

```bash
cp .env.example .env
# ★ 必填：生成随机根密钥填进 SOULMATE_APP_SECRET
python -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up -d
# 打开 http://localhost:8501，创建管理员账号
```

### 方式二：直接跑在服务器上

```bash
pip install -r requirements.txt

export SOULMATE_ENV=production
export SOULMATE_APP_SECRET='<上面生成的随机串>'
export SOULMATE_ALLOW_SIGNUP=false      # 关闭自助注册

streamlit run main.py --server.address 127.0.0.1 --server.port 8501
```

前面再挂一层 Nginx/Caddy 做 HTTPS 反代。完整步骤（含 OIDC、反代样例、备份策略）
见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

### 命令行工具

```bash
soulmate doctor                      # 环境与配置自检
soulmate create-user --username bob  # 管理员开号（不受注册开关限制）
soulmate list-users                  # 列出账号
soulmate migrate --user bob          # 把 v1 老数据搬进 bob 的目录
```

（未安装为脚本时用 `python -m soulmate.cli <命令>`。）

---

## 项目结构

```
.
├── main.py                       # 入口（薄）：配置页面 → 交给 ui.app
├── soulmate/                     # ★ 应用主体
│   ├── core/                     #   零业务依赖：配置/异常/日志/模型/安全/校验/限流
│   ├── storage/                  #   持久化：原子写/缓存/加密/按用户隔离的仓储
│   ├── llm/                      #   模型接入：Provider 抽象/重试/流式事件/降级链
│   ├── services/                 #   业务编排：模型服务/伴侣会话/记忆/导出/诊断/用量/迁移
│   ├── auth/                     #   身份：本机账号库/会话令牌/OIDC 适配
│   ├── ui/                       #   表现：页面编排/聊天/侧边栏/弹窗/设置/诊断/主题
│   └── cli.py                    #   命令行工具
├── tests/                        # 223 个用例（含 UI 冒烟 + 数据安全网）
├── legacy/                       # v1 平铺代码归档（只作对照，不是入口）
├── docs/                         # 架构/安全/部署/迁移文档
├── .streamlit/config.toml        # Streamlit 首屏主题
├── Dockerfile / docker-compose.yml
├── .github/workflows/ci.yml      # CI：lint + type + test + 镜像可构建
├── .pre-commit-config.yaml
├── pyproject.toml                # 项目元数据 + ruff/mypy/pytest 配置
├── requirements.txt              # 运行依赖
├── requirements-dev.txt          # 开发依赖
├── .env.example                  # 配置模板（复制成 .env）
└── data/                         # 本地数据（.gitignore，绝不上传）
    ├── users.json                #   账号列表
    ├── users/<用户名>/            #   ★ 每个用户一个目录（物理隔离）
    │   ├── providers.json        #     模型服务（API Key 是密文）
    │   ├── companions.json       #     伴侣列表
    │   ├── profile.json          #     我的资料 + 主题偏好
    │   ├── sessions/<伴侣>/      #     会话（会话记忆）
    │   ├── memory/<伴侣>.json    #     长期记忆
    │   ├── metrics/usage.json    #     用量统计
    │   └── backups/              #     备份（轮转保留 N 份）
    └── logs/                     #   滚动日志
```

---

## 架构与分层

```
        ┌──────────────────────────────────────────────┐
        │  ui/         Streamlit 页面与组件             │
        │  app · chat · sidebar · dialogs · settings   │
        │  diagnostics · auth_page · theme             │
        └───────────────────┬──────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────────┐
        │  services/   业务编排                         │
        │  provider · companion · memory · export      │
        │  diagnostics · metrics · migration           │
        └───────┬───────────────────────────┬──────────┘
                ▼                           ▼
   ┌────────────────────────┐   ┌────────────────────────┐
   │  llm/    模型接入       │   │  auth/   身份           │
   │  抽象·重试·流式·降级链  │   │  账号库·会话令牌·OIDC   │
   └───────────┬────────────┘   └───────────┬────────────┘
               ▼                            ▼
   ┌────────────────────────┐   ┌────────────────────────┐
   │  OpenAI SDK            │   │  storage/  持久化       │
   │  （唯一 import 处）     │   │  原子写·缓存·加密·仓储  │
   └────────────────────────┘   └───────────┬────────────┘
                                            ▼
        ┌──────────────────────────────────────────────┐
        │  core/  配置 · 异常 · 日志 · 领域模型          │
        │         安全 · 校验 · 限流 · 预设             │
        └──────────────────────────────────────────────┘
```

**铁律：依赖只能向下。** 任何一层都不许反向 import。好处是：
换界面（Streamlit → FastAPI）、换数据库、换模型供应商，都只动一层。

几个刻意的设计决定：

- **`llm/` 是全项目唯一 `import openai` 的地方** —— 将来接 Function Calling、换 SDK、适配厂商差异都只改这一层。
- **`core/` 不 import 任何业务模块** —— 所以它最好测（不需要 mock 全世界）。
- **UI 层只调 services** —— 页面里不会出现 `open(path)` 或 `client.chat.completions`。
- **系统提示词每次请求现拼** —— 昵称和长期记忆都是「会变的全局状态」，现拼永远同步，不用回写伴侣数据。

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 开发与测试

```bash
pip install -e ".[dev]"        # 或 pip install -r requirements-dev.txt -r requirements.txt
pre-commit install             # 可选：提交前自动检查

ruff check soulmate tests main.py   # 代码规范 + 安全规则
mypy soulmate main.py               # 类型检查
pytest tests -q                     # 全部测试
pytest tests -q --cov=soulmate      # 带覆盖率
pytest tests/test_storage.py -v     # 只跑某一层
```

测试特点：

- **不联网、不花钱**：模型调用用可编程的假 Provider（`tests/fakes.py`），能精确演出「中途断流」「限流」「超时」
- **不碰真实数据**：`tests/test_data_safety.py` 断言测试期间真实 `data/` 目录指纹不变
- **含 UI 冒烟**：用 Streamlit `AppTest` 真的把 `main.py` 跑起来，抓 import 错、widget 参数不兼容这类只有运行才暴露的问题
- 当前：**223 passed**，ruff 与 mypy 全绿

> ⚠️ 改完 `.py` 后必须**重启** Streamlit（Ctrl+C 再跑）。热更新只重执行入口，不会重新 import 子模块。

---

## 数据与安全

| 项 | 做法 |
|---|---|
| API Key | **Fernet 加密**后写入 `providers.json`；密钥由 `SOULMATE_APP_SECRET` 派生 |
| 登录密码 | bcrypt 哈希（自带盐），绝不存明文 |
| 登录态 | HMAC-SHA256 签名令牌，带有效期 |
| 用户隔离 | 每个用户在 `data/users/<用户名>/` 独立目录 |
| 日志脱敏 | 所有日志/异常文案先过 `redact()`，屏蔽 Key、Bearer、URL 凭据 |
| 内网防护 | 默认拒绝把模型地址指向内网/回环地址（防 SSRF） |
| 限流 | 聊天/记忆抽取/登录各自独立的令牌桶配额 |
| 生产校验 | 弱密钥、开放注册、debug 开启 → 拒绝启动 |

完整威胁模型与部署清单见 [docs/SECURITY.md](docs/SECURITY.md)。

---

## 排错

| 现象 | 原因与处理 |
|---|---|
| 改完代码没生效 | Streamlit 热更新不重载子模块。Ctrl+C 重启服务 |
| 整页红框 `removeChild on Node` | 浏览器标签页比服务器旧。`Ctrl+Shift+R` 强制刷新或重开标签页 |
| 伴侣聊不了天、输入框是灰的 | 没绑模型服务。主区黄条点「⚡ 一键绑定模型服务」 |
| 一直提示「模型服务拒绝了 API Key」 | Key 错/欠费。诊断页点「⚡ 测试」看详情 |
| 服务启动就红屏（生产） | 配置校验没过。按提示补 `SOULMATE_APP_SECRET` 或关掉 `ALLOW_SIGNUP`；`soulmate doctor` 可自检 |
| 忘记管理员密码 | 删 `data/users.json` 里该条目重开号，或用 `soulmate create-user` 另开一个管理员 |
| 想看 v1 老数据 | 首次登录会自动迁移；也可手动 `soulmate migrate --user <名>`。见 [docs/MIGRATION.md](docs/MIGRATION.md) |

---

## 已知限制

- **主题切换依赖 Streamlit 私有 API**（`st._config`）。未来版本若移除需要换方案（代码里已做防御与告警）。
- **限流是进程内的**。多 worker/多副本部署时各算各的，需要换成 Redis 之类的共享存储。
- **长期记忆是模型抽取的摘要**，不是原文；细节会丢，要保原文请翻会话记录。
- **持久化用 JSON 文件**，适合个人/小团队（几十用户量级）。上万用户需要换数据库。
- 长期记忆与聊天内容会进 system prompt，**存在提示注入的理论面**（见 SECURITY.md 的说明与缓解）。

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 分层设计、数据流、关键设计决策与取舍 |
| [docs/SECURITY.md](docs/SECURITY.md) | 威胁模型、已实现的防护、部署安全清单 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker/裸机部署、反向代理、OIDC、备份与升级 |
| [docs/MIGRATION.md](docs/MIGRATION.md) | v1/v0 → v2 数据迁移说明与手工回退方法 |
| [legacy/README.md](legacy/README.md) | v1 代码归档说明与新旧对照表 |

---

## 许可

MIT。本项目用于个人学习与作品展示。