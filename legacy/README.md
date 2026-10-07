# legacy/ —— v1 平铺架构归档

这里是 **v1 版本**（单用户、平铺模块）的完整源码归档，**仅作学习对照与 git 历史留底**，
不再是运行入口。新版本入口是根目录的 `main.py` + `soulmate/` 包。

## 内容

| 文件 | v1 中的职责 | 在 v2 中的对应 |
|---|---|---|
| `main.py` | 入口 + 主区聊天渲染 | `main.py`（薄入口）→ `soulmate/ui/app.py` / `chat.py` |
| `sidebar.py` | 侧边栏 | `soulmate/ui/sidebar.py` |
| `dialogs.py` | 弹窗 | `soulmate/ui/dialogs.py` |
| `companion_manager.py` | 业务核心 | `soulmate/services/` + `soulmate/storage/repositories/` |
| `llm.py` | 模型调用 | `soulmate/llm/` |
| `memory.py` | 长期记忆 | `soulmate/services/memory_service.py` |
| `config.py` | 常量/预设 | `soulmate/core/settings.py` + `soulmate/core/presets.py` |
| `theme.py` | 主题 | `soulmate/ui/theme.py` |
| `storage.py` | 文件读写 | `soulmate/storage/atomic.py` 等 |

> 注：`legacy/config.py` 里包含当时未提交的 `gemini-proxy` 反代预设（内网 IP）。
> 出于安全考虑，v2 的预设表已移除该条目；如确需接入，请在界面用「自定义」手填，
> 并显式开启 `SOULMATE_ALLOW_PRIVATE_BASE_URL`。

## 为什么保留

- 方便 `git log --follow legacy/` 对照学习旧实现；
- 避免重构中途误删用户历史工作；
- v1 的 `data/` 平铺数据会在首次登录时自动迁移（见 `soulmate/services/migration.py`）。

## 怎么跑 v1（不推荐）

```bash
# 需要把 legacy 目录下的模块复制回根目录才能运行，
# 且数据布局是旧版 —— 别在产品数据上这么干。
python .venv/Scripts/python.exe -c "import sys; sys.path.insert(0, 'legacy')"
```