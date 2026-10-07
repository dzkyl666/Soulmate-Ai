"""
主题配色模块
============
4 套配色方案 + 运行时应用函数（侧边栏可切换）。

【为什么它适合单独成一个模块】
本文件只依赖 streamlit，不依赖项目里其它任何文件。
换句话说，把它整个复制到别的 Streamlit 项目里，照样能用——
这就是"独立模块"的判断标准：不看主程序也能单独存在。

【机制备注】
st._config.set_option("theme.<键>", 值) 是 Streamlit 私有 API。
页面的颜色永远以「服务器当前 config」为准（唯一事实来源）：
    - 刚启动 / 重启应用：颜色来自 .streamlit/config.toml（= 月白）
    - 切换主题后：颜色 = 最近一次 apply_theme 写入的那套（F5 刷新不丢，重启才回默认）
    - 前端要「下一次 rerun」才吃色，所以 set_option 后调用方要补一次 st.rerun()
下拉框的选中项不自己记状态，而是用 current_theme_name() 反查服务器颜色得出，
这样"页面颜色"和"下拉框显示的名字"永远一致。
"""
import streamlit as st

THEMES = {
    "月白": {
        "base": "light",
        "primaryColor": "#5F6B76",
        "backgroundColor": "#FFFFFF",
        "secondaryBackgroundColor": "#F5F6F8",
        "textColor": "#2C2F33",
        "borderColor": "#E3E6EA",
        "linkColor": "#4A5560",
    },
    "玄夜": {
        "base": "dark",
        "primaryColor": "#5B9FD6",
        "backgroundColor": "#1A1D21",
        "secondaryBackgroundColor": "#23272C",
        "textColor": "#E3E6E8",
        "borderColor": "#33383D",
        "linkColor": "#7FBBE8",
    },
    "樱粉": {
        "base": "light",
        "primaryColor": "#D95F81",
        "backgroundColor": "#FFFBF8",
        "secondaryBackgroundColor": "#FDF0F1",
        "textColor": "#3E353A",
        "borderColor": "#F0DADD",
        "linkColor": "#C25577",
    },
    "天青": {
        "base": "light",
        "primaryColor": "#2E7DD1",
        "backgroundColor": "#FFFFFF",
        "secondaryBackgroundColor": "#EEF4FB",
        "textColor": "#1E2A35",
        "borderColor": "#DCE7F5",
        "linkColor": "#1B62C0",
    },
}

# 默认主题名（首次进入页面、且没有任何保存偏好时的那套）
DEFAULT_THEME = "月白"


def apply_theme(name: str) -> None:
    """把指定主题写入运行时配置（每次切换会覆盖所有键，避免上个主题的颜色残留）"""
    try:
        for key, value in THEMES[name].items():
            st._config.set_option(f"theme.{key}", value)
    except Exception:
        # 私有 API 防御：万一以后版本移除，至少不崩
        pass


def current_theme_name(default: str = DEFAULT_THEME) -> str:
    """反查「服务器当前颜色」属于哪一套主题，返回主题名。

    用途：每次新建会话（首次打开 / F5 刷新）时，让下拉框的选中项
    跟着服务器实际颜色走，而不是自己另外记一份状态——两份状态必然会对不上。

    三种场景的结果：
        刚启动 / 重启应用 → config 来自 config.toml → 返回「月白」（默认）
        F5 刷新页面       → config 还是上次切换后的那套 → 返回那套
        都匹配不上（比如用户手动改了 config.toml）→ 返回 default
    """
    try:
        for name, colors in THEMES.items():
            # 某一套主题的所有颜色键都和服务器现值相同，就认定是它
            if all(st._config.get_option(f"theme.{k}") == v for k, v in colors.items()):
                return name
    except Exception:
        pass
    return default
