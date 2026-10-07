# 部署指南

> 从「能跑」到「跑在服务器上」。Docker 与裸机两条路都给了，另有反向代理、OIDC、备份升级。

---

## 1. 部署前必读

- 先读 [SECURITY.md](SECURITY.md) 的**部署安全清单** —— 尤其「必须放 HTTPS 反代之后」。
- **必须设置 `SOULMATE_APP_SECRET`**（≥32 位随机串）。生产环境不设会拒绝启动。
- 应用是**单进程** Streamlit。同一份 `data/` **不要**被多个副本同时挂载（JSON 非并发安全）。

---

## 2. 方式一：Docker Compose（推荐）

### 2.1 准备

```bash
git clone https://github.com/dzkyl666/Soulmate-Ai.git
cd Soulmate-Ai
cp .env.example .env
```

编辑 `.env`，至少改这几项：

```ini
SOULMATE_ENV=production
SOULMATE_APP_SECRET=<下面生成的随机串>
SOULMATE_ALLOW_SIGNUP=false
SOULMATE_DEBUG=false
```

生成根密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
# 或 Linux/macOS：
openssl rand -base64 48
```

### 2.2 起服务

```bash
docker compose up -d
docker compose logs -f          # 看启动日志
```

浏览器打开 `http://localhost:8501`，首屏创建管理员账号。

### 2.3 常用运维

```bash
docker compose ps                       # 状态（含健康检查）
docker compose restart                  # 重启
docker compose down                     # 停（数据卷保留）
docker compose up -d --build            # 改代码后重建
docker compose exec soulmate soulmate doctor   # 容器内自检
```

> compose 默认只把端口绑在 `127.0.0.1`。要从外部访问，请配反向代理（见第 4 节），
> 而不是把 `ports` 改成 `8501:8501`。

### 2.4 数据卷

数据在命名卷 `soulmate-data`（容器内 `/data`）。

```bash
# 备份整个数据卷到宿主机
docker run --rm -v soulmate-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/soulmate-backup-$(date +%Y%m%d).tar.gz -C /data .

# 恢复
docker run --rm -v soulmate-data:/data -v "$PWD":/backup alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/soulmate-backup-YYYYMMDD.tar.gz -C /data"
```

想直接用宿主机目录也行，把 compose 里的卷换成 bind mount：

```yaml
volumes:
  - ./data:/data
```

（此时注意宿主机目录权限：容器内以 uid 10001 运行。）

---

## 3. 方式二：裸机 / 虚拟机

### 3.1 环境

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv python3-pip
```

### 3.2 安装

```bash
git clone https://github.com/dzkyl666/Soulmate-Ai.git /opt/soulmate
cd /opt/soulmate
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3.3 配置

```bash
sudo mkdir -p /var/lib/soulmate
sudo chown $USER /var/lib/soulmate
cp .env.example .env      # 然后编辑，同 2.1
```

`.env` 里把数据目录指到持久位置：

```ini
SOULMATE_DATA_DIR=/var/lib/soulmate
```

### 3.4 systemd 服务

`/etc/systemd/system/soulmate.service`：

```ini
[Unit]
Description=Soulmate AI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=soulmate
Group=soulmate
WorkingDirectory=/opt/soulmate
EnvironmentFile=/opt/soulmate/.env
ExecStart=/opt/soulmate/.venv/bin/streamlit run main.py \
    --server.address 127.0.0.1 \
    --server.port 8501 \
    --server.headless true
Restart=always
RestartSec=5

# 加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/soulmate

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin soulmate
sudo chown -R soulmate:soulmate /opt/soulmate /var/lib/soulmate
sudo systemctl daemon-reload
sudo systemctl enable --now soulmate
sudo systemctl status soulmate
journalctl -u soulmate -f
```

---

## 4. 反向代理（HTTPS，必做）

### Caddy（最省事，自动申请证书）

`/etc/caddy/Caddyfile`：

```
soulmate.example.com {
    reverse_proxy 127.0.0.1:8501 {
        # Streamlit 用 WebSocket 推送界面更新，必须放行 Upgrade
        header_up Upgrade {http.request.header.Upgrade}
        header_up Connection {http.request.header.Connection}
    }

    # 额外的访问控制（可选，纵深防御）
    # basicauth {
    #     admin $2a$14$<bcrypt哈希>
    # }

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }

    encode gzip
}
```

### Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name soulmate.example.com;

    ssl_certificate     /etc/letsencrypt/live/soulmate.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/soulmate.example.com/privkey.pem;

    # 长连接 / 大响应（流式输出可能较久）
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    client_max_body_size 8m;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;

        # ★ Streamlit 必需：WebSocket 升级头
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        "upgrade";
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
}

server {
    listen 80;
    server_name soulmate.example.com;
    return 301 https://$host$request_uri;
}
```

> **WebSocket 头漏了会怎样**：页面能打开但一直转圈 / 交互无响应。这是最常见的部署坑。

### 容器里也要告诉 Streamlit 自己在反代后

在 `.env` 或 compose 环境里加：

```ini
STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=true
STREAMLIT_SERVER_ENABLE_CORS=false
```

---

## 5. OIDC 登录（可选）

默认用本机账号库。想接企业 SSO / Google / Microsoft，用 Streamlit 原生的 `st.login()`。

### 5.1 配置 provider

`.streamlit/secrets.toml`（**已在 .gitignore，别提交**）：

```toml
[auth]
redirect_uri = "https://soulmate.example.com/oauth2callback"
cookie_secret = "<一个随机串：python -c 'import secrets;print(secrets.token_hex(32))'>"

[auth.google]
client_id = "<Google OAuth Client ID>"
client_secret = "<Google OAuth Client Secret>"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

# 或者随便哪个 OIDC 提供方（Keycloak / Auth0 / Azure AD…）：
# [auth.myidp]
# client_id = "..."
# client_secret = "..."
# server_metadata_url = "https://idp.example.com/.well-known/openid-configuration"
```

提供方后台的**授权回调地址**要填：`https://soulmate.example.com/oauth2callback`

### 5.2 切到 OIDC 模式

```ini
SOULMATE_AUTH_MODE=oidc      # 只用 OIDC
# SOULMATE_AUTH_MODE=hybrid  # OIDC 配好就用 OIDC，否则回退本机账号
```

### 5.3 它是怎么映射的

OIDC 登录后拿到的身份（邮箱/sub）会被映射成一个**安全的本机用户名**
（`oidc_<本地部分>_<sha256前12位>`），并在 `users.json` 里建一条 `oidc_sub` 非空的记录。
这样账号管理、停用、诊断仍走同一套接口；邮箱里的 `@` 不会变成非法目录名。

第一个 OIDC 登录者会自动成为管理员。

---

## 6. 备份与升级

### 6.1 备份

应用内置了备份功能（设置页「💾 数据备份」），保留最近 `SOULMATE_BACKUP_KEEP` 份。
但它只备份**单个用户**的数据 —— 整机灾备仍建议在文件系统层做。

```bash
# 每天 3 点打包整个数据目录，保留 14 天
cat > /etc/cron.daily/soulmate-backup <<'EOF'
#!/bin/sh
DEST=/var/backups/soulmate
mkdir -p "$DEST"
tar czf "$DEST/soulmate-$(date +%F).tar.gz" -C /var/lib soulmate
find "$DEST" -name 'soulmate-*.tar.gz' -mtime +14 -delete
EOF
chmod +x /etc/cron.daily/soulmate-backup
```

> **`APP_SECRET` 要和数据一起备份**（分开存放），否则恢复后加密的 API Key 解不开。

### 6.2 升级

```bash
cd /opt/soulmate
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart soulmate
```

Docker：

```bash
git pull
docker compose up -d --build
```

升级前建议先停机备份一次。数据迁移（v1→v2）在用户首次登录时自动完成，见 [MIGRATION.md](MIGRATION.md)。

### 6.3 回滚

代码回滚：`git checkout <上一个 tag/commit>` 后重装重启。

**若已执行过 v1→v2 迁移**，数据布局已变（`data/*.json` → `data/users/<用户名>/*`）。
回滚到 v1 需要把文件搬回去，具体命令见 [MIGRATION.md](MIGRATION.md#4-手工回退v2--v1)。

---

## 7. 运维速查

| 需求 | 命令 |
|---|---|
| 自检配置 | `soulmate doctor` |
| 开号（关闭注册后） | `soulmate create-user --username bob` |
| 列账号 | `soulmate list-users` |
| 迁移老数据 | `soulmate migrate --user bob` |
| 看日志（容器） | `docker compose logs -f` |
| 看日志（裸机） | `journalctl -u soulmate -f` |
| 应用内日志 | `data/logs/soulmate.log`（5MB × 5 轮转） |
| 健康检查 | `curl -fsS http://127.0.0.1:8501/_stcore/health` |
| 用量统计 | 应用内 设置页 → 📈 用量统计 |
| 模型连通性 | 应用内 诊断页 → ⚡ 测试 |

---

## 8. 常见部署问题

| 现象 | 原因 |
|---|---|
| 页面转圈、点了没反应 | 反向代理没放行 **WebSocket** 的 `Upgrade`/`Connection` 头 |
| 启动即退出，日志说「生产配置有致命问题」 | 没设 `SOULMATE_APP_SECRET`，或 `ALLOW_SIGNUP` 还是 true，或 `DEBUG=true` |
| 重启后 API Key 全部失效 | `APP_SECRET` 变了（本地开发模式下用自动生成的密钥，重启机器换了目录也会变）。生产必须显式固定 |
| 容器里写不进数据 | 挂载目录属主不是 uid 10001。`chown -R 10001:10001 ./data` |
| 两个人同时用，数据偶尔错乱 | 同一份 `data/` 被多副本挂载了。JSON 非并发安全，请只跑一个实例 |
| 登录后立刻被踢出 | 反向代理没透传 `X-Forwarded-Proto`，或 `cookie_secret` 变了 |
| 中文日志乱码（Windows 控制台） | 设 `PYTHONIOENCODING=utf-8` |