"""入口：`streamlit run main.py`

只做一件事：配置页面 + 把控制权交给 soulmate.ui.app.run()。
业务逻辑、认证、迁移、主题全在包内部，这里没有任何实现细节。
"""

from __future__ import annotations

import streamlit as st

from soulmate.ui.app import run

st.set_page_config(
    page_title="Soulmate AI",
    page_icon="🧸",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={},
)

run()