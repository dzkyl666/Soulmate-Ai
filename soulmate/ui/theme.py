"""主题配色：4 套方案 + 运行时应用 + 持久化。

【机制（与旧版一致，仍是 Streamlit 私有 API）】
- 页面的颜色永远以「服务器当前 config」为准（唯一事实来源）；
- 切换主题 = `st._config.set_option("theme.<键>", 值)`；
- 下拉框选中项不自己记状态，用 `current_theme_name()` 反查服务器颜色得出，
  页面颜色和下拉框显示永远一致。

【本版修复的旧问题】
旧版主题只在「用户手动切换」时生效，重启后回默认。
现在主题偏好存在 profile 里，app 启动时 `apply_saved_theme()` 会**先应用再 rerun**，
持久化的主题重启不失；首屏第 2 帧起颜色就正确。
"""

from __future__ import annotations

import streamlit as st

from soulmate.core.logging import get_logger

log = get_logger("soulmate.ui.theme")

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

DEFAULT_THEME = "月白"


def apply_theme(name: str) -> None:
    """把指定主题写入运行时配置（覆盖所有键，避免上个主题颜色残留）。"""
    if name not in THEMES:
        return
    try:
        for key, value in THEMES[name].items():
            st._config.set_option(f"theme.{key}", value)
    except Exception:
        # 私有 API 防御：未来版本若移除，至少不崩（但要留下痕迹便于排查）
        log.warning("应用主题失败（Streamlit 私有 API 可能已变更）", exc_info=True)


def current_theme_name(default: str = DEFAULT_THEME) -> str:
    """反查服务器当前颜色属于哪套主题。"""
    try:
        for name, colors in THEMES.items():
            if all(st._config.get_option(f"theme.{k}") == v for k, v in colors.items()):
                return name
    except Exception:
        log.debug("反查主题失败，回落到默认", exc_info=True)
    return default


def apply_saved_theme(saved: str | None) -> bool:
    """启动时调用：持久化的主题和当前不一致就先应用，返回是否需要 rerun。"""
    saved = saved or DEFAULT_THEME
    if saved not in THEMES:
        saved = DEFAULT_THEME
    if current_theme_name(DEFAULT_THEME) != saved:
        apply_theme(saved)
        return True
    return False
