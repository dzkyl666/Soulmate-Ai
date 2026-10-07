# 安全说明

> 这份文档回答两件事：**这个应用防住了什么**、**部署时你必须做什么**。
> 最后附威胁模型与已知残余风险（不吹「绝对安全」）。

---

## 0. 一句话结论

v2 已具备部署到服务器的**基本安全基线**（认证、用户隔离、密钥加密、输入校验、限流、生产配置 fail-fast）。
但它是**自托管应用**：安全下限由你的部署方式决定 —— **必须放在 HTTPS 反代之后**，
且**必须设置强 `SOULMATE_APP_SECRET`**。

---

## 1. 已实现的防护

### 1.1 认证与授权

| 项 | 实现 |
|---|---|
| 口令存储 | bcrypt 哈希（自动加盐、慢哈希），**绝不存明文** |
| 口令强度 | 至少 8 位 + 大小写 + 数字（`core/security.password_strength`） |
| 登录态 | HMAC-SHA256 签名令牌，带过期时间；签名密钥由 `app_secret` 派生 |
| 防枚举 | 「用户不存在」与「密码错」返回**同一个** `InvalidCredentials` |
| 停用账号 | `UserRecord.disabled`，登录时拒绝 |
| 管理员 | `role` 字段；账号管理面板仅管理员可见 |
| 自助注册 | 可通过 `SOULMATE_ALLOW_SIGNUP=false` 关闭，改用 `soulmate create-user` |
| 登录限流 | `SOULMATE_RATE_LIMIT_LOGIN_PER_MINUTE`（默认 10 次/分） |

### 1.2 用户数据隔离

- 每个用户的数据在 `data/users/<用户名>/` 下，是**物理隔离**（不同路径）。
- 用户名经 `validate_username()` 校验（仅字母数字 `._-`），**不含路径分隔符**。
- 所有 ID（`companion_id` / `session_id`）经 `validate_id()` 校验并用于文件名拼接 ——
  这是**路径穿越**的防线：不允许 `../`。

### 1.3 密钥与敏感信息

| 项 | 做法 |
|---|---|
| API Key 落盘 | **Fernet 对称加密**（AES-128-CBC + HMAC），密文存 `api_key_enc` |
| 加密密钥来源 | 由 `SOULMATE_APP_SECRET` 经 PBKDF2-HMAC-SHA256（200k 次迭代、按用途分盐）派生 |
| 界面回显 | 只显示 `••••••1234`（`mask_secret`） |
| 日志脱敏 | 所有日志/异常文案先过 `redact()`：屏蔽 `sk-*`、Bearer、URL 内凭据、`api_key=` 形态 |
| 自动根密钥 | **仅非生产环境**自动生成到 `data/.app_secret`（权限 0600）；生产必须显式设置 |
| 密钥失效处理 | 解不开的 Key 置空并记警告，**不崩**（提示用户重填） |

> **为什么按用途分盐派生**：加密密钥与签名密钥共用一把是安全反模式，分开后一处泄露不直接波及其它用途。

### 1.4 输入校验与 SSRF 防护

- `base_url` 只允许 `http` / `https`，必须有主机名。
- **默认拒绝内网/回环/链路本地/保留地址** —— 防止「用户填一个 URL，服务器替他去请求内网」。
  确需接内网模型（Ollama、内网反代）时显式设 `SOULMATE_ALLOW_PRIVATE_BASE_URL=true`。
- 文本有长度上限（消息 8000 字、用途 200 字等），防 token 烧光与存储膨胀。
- 模型名拒绝换行/空字节。

### 1.5 限流

进程内令牌桶，三档独立配额：

| 入口 | 默认 |
|---|---|
| 聊天 | 30 次/分 |
| 记忆抽取 | 60 次/分 |
| 登录 | 10 次/分 |

### 1.6 生产配置 fail-fast

`SOULMATE_ENV=production` 时，以下任一情况会**拒绝启动**（而不是带病运行）：

- `SOULMATE_APP_SECRET` 未设置 / 短于 32 字符 / 是常见弱值
- `SOULMATE_DEBUG=true`
- 使用本机账号且 `SOULMATE_ALLOW_SIGNUP=true`（任何人可开号）

### 1.7 容器与依赖

- 镜像**非 root 运行**（uid 10001）、`no-new-privileges`、`cap_drop: ALL`
- compose 默认只监听 `127.0.0.1:8501`（不直接暴露公网）
- CI 里跑 ruff 的 **bandit 规则集**（`S` 前缀）扫常见安全问题

---

## 2. ★ 部署安全清单（照着做）

### 必做

- [ ] **设置强 `SOULMATE_APP_SECRET`**：`python -c "import secrets; print(secrets.token_urlsafe(48))"`
- [ ] **放在 HTTPS 反向代理之后**（Nginx/Caddy）。裸 HTTP 会让登录口令与令牌明文过网
- [ ] `SOULMATE_ENV=production`
- [ ] `SOULMATE_ALLOW_SIGNUP=false`，用 `soulmate create-user` 开号
- [ ] `SOULMATE_DEBUG=false`
- [ ] 数据目录**只给服务账号读写**；定期备份（见 DEPLOYMENT.md）
- [ ] 不把 `data/`、`.env`、`.streamlit/secrets.toml` 提交到任何仓库
      （`.gitignore` 与 pre-commit 已挡，但别绕过）

### 强烈建议

- [ ] 反向代理层再加一层访问控制（IP 白名单 / Basic Auth / SSO），做纵深防御
- [ ] 开启 fail2ban 或云厂商 WAF，挡住登录爆破
- [ ] 定期 `docker compose pull && up -d --build` 更新依赖（`pip-audit` 可扫已知漏洞）
- [ ] 用 `soulmate doctor` 在启动流程里做自检
- [ ] 数据目录加密（如 LUKS / BitLocker），防磁盘被物理拿走

### 别做

- [ ] ❌ 不要把 `data/` 直接挂到公网可访问的静态目录
- [ ] ❌ 不要多个副本共用同一个 `data/`（JSON 文件非并发安全）
- [ ] ❌ 不要在未设 `APP_SECRET` 的情况下跑生产 —— 一重启密钥就变，已存 Key 全部解不开

---

## 3. 威胁模型

### 防住的

| 威胁 | 缓解 |
|---|---|
| 数据库/文件泄露导致 API Key 泄露 | Key 加密落盘；密钥与数据分离（`APP_SECRET` 在环境变量/密钥管理里） |
| 撞库 / 口令爆破 | bcrypt 慢哈希 + 登录限流 + 统一错误文案 |
| 用户名枚举 | 统一错误文案 |
| 越权访问他人数据 | 物理目录隔离 + ID 校验（无路径穿越） |
| 服务端被当作 SSRF 跳板 | 默认拒绝内网地址 |
| 日志泄露密钥 | 全出口 `redact()` |
| 刷接口烧钱 | 三档限流 + 单条消息长度上限 |
| 误把密钥提交到仓库 | `.gitignore` + pre-commit 钩子 + `.dockerignore` |
| 配置疏漏上生产 | `validate_for_startup()` fail-fast |

### 不在防护范围内（诚实说明）

| 情况 | 说明 |
|---|---|
| **提示注入（Prompt Injection）** | 聊天内容与长期记忆会进 system prompt。恶意用户可尝试让模型「忘记人设」或越权输出。**缓解**：所有输出只回到该用户自己的会话，不会跨用户；不接工具调用，所以无法通过注入触发服务端动作。**这是 LLM 应用的共性残余风险。** |
| **拿到宿主机 root 的攻击者** | 能读 `APP_SECRET` 就能解密 Key。这类攻击者已越过应用边界，需靠主机安全解决 |
| **恶意模型供应商** | 你填的 Base URL 是该供应商的服务器，它会看到你发过去的对话内容。**只填你信任的服务商。** |
| **DDoS / 大流量** | 应用内限流挡不住 L4/L7 洪水，需在反代或云厂商层解决 |
| **多副本下的限流** | 进程内令牌桶各算各的。多副本部署需换 Redis 共享存储 |
| **传输中加密** | 应用自身不做 TLS，**必须**由反代终结 HTTPS |

---

## 4. 密钥管理

### 生成

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 存放

| 环境 | 建议 |
|---|---|
| 本地开发 | 不设，自动生成到 `data/.app_secret` |
| Docker | `.env` 文件（已在 `.gitignore`），或 `docker secret` / 云厂商 Secret Manager |
| K8s | `Secret` 挂成环境变量，不要写进镜像 |

### 轮换的代价（重要）

`APP_SECRET` 一旦更换：

- **已加密的 API Key 全部解不开**（会置空并提示重填，不会崩）
- **所有人登录态失效**（需要重新登录）

所以：把它当长期密钥管理，别随手改。真要轮换，流程是
「备份 `data/` → 换密钥 → 逐个重填 API Key → 通知用户重新登录」。

---

## 5. 事件响应

怀疑密钥泄露时：

1. **立即轮换 `APP_SECRET`**（并接受上面说的代价）
2. **去各模型供应商后台吊销旧 API Key**（这一步比换 APP_SECRET 更紧急 ——
   泄露的若是明文 Key，加密与否已无意义）
3. 检查 `data/logs/soulmate.log` 是否有异常登录/调用
4. 审查 `data/users.json` 是否有多出来的账号
5. 若数据目录被篡改，从 `backups/` 恢复

---

## 6. 自助检查

```bash
# 配置自检（生产问题会列出来）
soulmate doctor

# 应用内：侧边栏 → 🩺 诊断页
#   能看到：根密钥是否设置、是否允许内网地址、限流配额、启动自检问题数

# 依赖漏洞扫描（需另装）
pip install pip-audit && pip-audit

# 确认没有把敏感文件提交上去
git ls-files | grep -E "^(data/|session/|\.env$)" && echo "❌ 有敏感文件被跟踪" || echo "✅ 干净"
```