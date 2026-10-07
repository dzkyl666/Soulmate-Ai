# 数据迁移说明（v1 / v0 → v2）

> v2 改了数据布局（多了「用户」这一层）。**老数据不会丢**，首次登录时自动迁移。
> 这份文档说明迁移做了什么、怎么验证、出问题怎么退回。

---

## 1. 布局变了什么

### v1（平铺，单用户）

```
data/
├── providers.json        # 模型服务（API Key 明文）
├── companions.json       # 伴侣
├── profile.json          # 我的资料
├── sessions/<伴侣id>/<会话id>.json
└── memory/<伴侣id>.json
```

### v0（更老的版本，单层会话）

```
<项目根>/session/<时间戳>.json     # 一个文件 = 一段完整对话，昵称/性格都在文件里
```

### v2（多用户，物理隔离）

```
data/
├── users.json                    # 账号列表
└── users/<用户名>/
    ├── providers.json            # API Key 变成密文 api_key_enc
    ├── companions.json
    ├── profile.json
    ├── sessions/<伴侣id>/<会话id>.json
    ├── memory/<伴侣id>.json
    └── .migrated                 # 迁移完成标记
```

---

## 2. 什么时候迁移

**自动**：用户首次登录（或首次访问）时，`soulmate/services/migration.run_all()` 会跑一遍。

**手动**（推荐在服务器上这么做，能看到输出）：

```bash
soulmate migrate --user <用户名>
# 未装为脚本时：
python -m soulmate.cli migrate --user <用户名>
```

> 注意：`soulmate create-user` **不会**自动迁移（迁移会移动真实文件，不该藏在「开个号」里顺手做掉）。
> 开号后请显式执行上面的 migrate 命令。

---

## 3. 迁移到底做了什么

### 3.1 v1 平铺 → 用户目录（`migrate_legacy_flat`）

把 `data/` 顶层的这 5 项**移动**（`shutil.move`，不是复制）到 `data/users/<用户名>/`：

- `providers.json`、`companions.json`、`profile.json`、`sessions/`、`memory/`

**规则**：

| 情况 | 行为 |
|---|---|
| 目标已存在 | **跳过**，不覆盖（保护用户目录里的新数据） |
| 源不存在 | 跳过 |
| 搬了东西 | 在用户目录写 `.migrated` 标记 |

### 3.2 v0 单层会话 → 一个伴侣 + 会话（`migrate_legacy_session_dir`）

读 `<项目根>/session/*.json`：

1. 用第一个文件里的 `nickname` / `nature` 造一个伴侣（人设用默认模板填充）
2. 每个文件的 `messages` 搬成一个会话（标题自动取首句）
3. 搬完**删除**旧文件

**前提**：该用户**一个伴侣都没有**。已有伴侣 → 认为迁移过了（或用户已自建），跳过不打扰。

### 3.3 API Key 的加密升级

迁移是**移动文件**，所以 `providers.json` 里可能还是 v1 的明文 `api_key`。
`ProviderRepository` 读的时候兼容明文；**下次保存时会自动转成密文** `api_key_enc`
（比如你在界面上编辑一次该模型服务）。

想立刻批量转成密文？编辑一次保存即可，或跑：

```python
python -c "
from soulmate.core.settings import get_settings
from soulmate.services.container import ServiceContainer
s = get_settings(); c = ServiceContainer(s, '<用户名>')
for p in c.providers.list_providers(): c.provider_repo.upsert(p)
print('已重新加密保存')
"
```

### 3.4 幂等性

所有迁移函数都是**幂等**的：跑多少次结果都一样。第二次跑会返回空结果。

```
$ soulmate migrate --user bob
迁移完成: {'flat_moved': [], 'legacy_sessions': 0}     ← 已经搬过了
```

---

## 4. 验证迁移成功

```bash
# 1. 看用户目录里有没有东西
ls -la data/users/<用户名>/

# 2. providers.json 里的 Key 应该已经是密文
cat data/users/<用户名>/providers.json | grep api_key_enc

# 3. 老位置应该已经空了（这些文件不该再存在）
ls data/providers.json data/companions.json data/sessions 2>/dev/null || echo "✅ 老位置已清空"

# 4. 应用内验证
#    登录 → 侧边栏能看到伴侣和会话历史 → 打开一个会话确认消息在
#    设置页 → 导出当前会话，确认内容完整
```

---

## 5. 手工迁移（不想用自动流程时）

```bash
# 假设用户名是 bob
mkdir -p data/users/bob
mv data/providers.json data/companions.json data/profile.json data/users/bob/
mv data/sessions data/users/bob/
mv data/memory   data/users/bob/
echo "providers.json companions.json profile.json sessions memory" > data/users/bob/.migrated
```

先在 `users.json` 里把 bob 这个账号建好（`soulmate create-user --username bob`），
否则登录时 `run_all` 会以为没搬过而重复尝试（虽然也会因目标已存在而跳过，无害）。

---

## 6. 手工回退（v2 → v1）

⚠️ 只在确实需要回到 v1 代码时做。**先停服务、先备份。**

```bash
# 0. 备份
cp -r data data-backup-before-rollback

# 1. 停服务
sudo systemctl stop soulmate      # 或 docker compose down

# 2. 把数据搬回顶层（把 bob 换成实际用户名）
mv data/users/bob/providers.json  data/
mv data/users/bob/companions.json data/
mv data/users/bob/profile.json    data/
mv data/users/bob/sessions        data/
mv data/users/bob/memory          data/
rm -f data/users/bob/.migrated
rm -rf data/users data/users.json

# 3. 代码回退
git checkout <v1 的 commit>

# 4. 起服务（用 v1 的方式）
streamlit run main.py
```

**注意**：v1 读不了密文 Key。回退后如果 `providers.json` 里只有 `api_key_enc`，
v1 会认为「没有 Key」。回退前请先把 Key 导出成明文，或回退后重新填一次。

---

## 7. 常见问题

| 问题 | 说明 |
|---|---|
| 登录后看不到老伴侣 | 确认 `data/users/<用户名>/companions.json` 有内容；确认迁移是针对**这个用户名**跑的（每个用户目录独立） |
| 迁移后 Key 显示为空 | `APP_SECRET` 变了导致解不开。见 SECURITY.md「密钥管理」；重新填一次 Key 即可 |
| 想迁到另一个用户名下 | 直接把 `data/users/<旧名>/` 整个改名为 `data/users/<新名>/`，再改 `users.json` 里的 username |
| 老 `session/` 目录还在 | 说明当时该用户已有伴侣，迁移按设计跳过了。可手工搬（见第 5 节思路）或删掉 |
| 迁移把某个文件漏了 | 迁移只在「目标不存在」时移动。手工补搬即可，不冲突 |
| `providers.json.bak-20260916` 这类文件 | v1 时期你手动留的备份，迁移**不处理**它们（只搬固定 5 项），需要就自己收好 |

---

## 8. 备份建议

迁移**之前**先备份：

```bash
cp -r data data-backup-$(date +%Y%m%d)
```

迁移是移动操作（不是复制），意味着源文件会消失 —— 有备份才有退路。