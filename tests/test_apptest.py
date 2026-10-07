"""UI 冒烟测试（Streamlit AppTest）：证明「改完能跑起来」而不是只过了单测。

AppTest 会在真实 Streamlit 运行时里执行 `main.py`，能抓到
「import 写错 / widget 参数不兼容 / 首屏抛异常」这类只有跑起来才暴露的问题。

注意：这里跑的是进程级配置（conftest 里已把 data 指到临时目录），
所以不会碰用户真实数据。
"""

from __future__ import annotations

from pathlib import Path

import pytest

streamlit = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from soulmate.auth.user_store import UserStore  # noqa: E402
from soulmate.core.settings import get_settings  # noqa: E402
from soulmate.storage.repositories import UserRepository  # noqa: E402

GOOD_PW = "Passw0rd123"

# AppTest.from_file 的相对路径是相对**调用它的文件**解析的（即 tests/），
# 所以这里必须传绝对路径，否则会去找不存在的 tests/main.py。
APP_PATH = Path(__file__).resolve().parents[1] / "main.py"


def _fresh_app() -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    return at


def _logged_in_app(username: str, **session_state) -> AppTest:
    """构造一个「已登录」的 App。"""
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.session_state["user_id"] = username
    for k, v in session_state.items():
        at.session_state[k] = v
    at.run()
    return at


@pytest.fixture
def isolated_data_dir(tmp_root):
    """确保全局 settings 指向临时数据目录（而不是项目里真实的 data/）。"""
    settings = get_settings()
    assert str(tmp_root) in str(settings.data_root()), "测试数据目录必须隔离在临时目录内"
    return settings.data_root()


class TestLoginScreen:
    def test_first_run_shows_admin_bootstrap(self, isolated_data_dir):
        """没有任何账号时，首屏应该是「创建管理员账号」，且不报错。"""
        for f in isolated_data_dir.glob("users.json"):
            f.unlink()
        at = _fresh_app()
        assert not at.exception, [str(e) for e in at.exception]
        assert at.title, "应该渲染标题"
        text_blob = " ".join(t.value for t in at.markdown).lower()
        assert "soulmate" in text_blob or at.title[0].value

    def test_login_form_shows_when_users_exist(self, isolated_data_dir):
        settings = get_settings()
        store = UserStore(UserRepository(settings.data_root()), settings)
        store.admin_create_user("existing", GOOD_PW)

        at = _fresh_app()
        assert not at.exception, [str(e) for e in at.exception]
        # 有账号 → 出现登录相关输入框
        labels = [t.label for t in at.text_input]
        assert any("用户名" in x for x in labels), labels


class TestLoginRateLimitOnPage:
    """★ P1 回归：连点登录触发限流时，页面必须**显示提示**而不是整页红框。

    【这条为什么必须是 AppTest，而不是单元测试】
    `RateLimitError` 是 `SoulmateError` 的直接子类，**不是** `AuthError` 的子类。
    登录页原先写的是 `except AuthError` → 限流异常不被接住 → 冒到 Streamlit → 整页红框。

    单元测试测不到这个：`UserStore.authenticate()` 抛 RateLimitError 是**正确行为**，
    单测里那是通过。真正的缺陷在「UI 边界捕得太窄」这一层，
    只有把页面真跑起来、真点按钮才能暴露。

    这类回归以前要靠验收方手动连点 11 次才发现，现在由本用例挡住。
    """

    def test_repeated_login_clicks_show_hint_not_crash(self, isolated_data_dir):
        settings = get_settings()
        store = UserStore(UserRepository(settings.data_root()), settings)
        store.admin_create_user("victim", GOOD_PW)

        # 点 (限额+1) 次；默认限额 10 → 11 次（第 11 次必被限流）
        limit = settings.rate_limit_login_per_minute
        assert limit == 10, f"本用例按默认限额 10 设计，当前是 {limit}"

        at = _fresh_app()
        assert not at.exception, [str(e) for e in at.exception]

        # 必须用**错误密码**：正确密码第一次就会登录成功、离开登录页，
        # 后面就没有"连点"可言了。错误密码也恰好模拟爆破。
        for i in range(limit + 1):
            at.text_input(key="login_user").set_value("victim")
            at.text_input(key="login_pass").set_value("definitely-wrong-password")
            at.button(key="login_submit").click()
            at.run()

            # 每一次点击后页面都不允许抛异常（这就是 P1 的断言点）
            assert not at.exception, (
                f"第 {i + 1} 次点登录后页面抛异常 → 说明 UI 边界没接住："
                f"{[str(e) for e in at.exception]}"
            )

        # 第 limit+1 次必须看到限流文案（来自 RateLimitError.user_message()）
        shown = " ".join(e.value for e in at.error)
        assert "操作太频繁" in shown, f"未出现限流提示，实际错误提示为：{shown!r}"

    def test_first_attempts_show_wrong_password_not_rate_limit(self, isolated_data_dir):
        """反向断言：限额之内应该是"密码不正确"，别把限流提示提前打出来。"""
        settings = get_settings()
        store = UserStore(UserRepository(settings.data_root()), settings)
        store.admin_create_user("victim2", GOOD_PW)

        at = _fresh_app()
        at.text_input(key="login_user").set_value("victim2")
        at.text_input(key="login_pass").set_value("wrong")
        at.button(key="login_submit").click()
        at.run()

        assert not at.exception, [str(e) for e in at.exception]
        shown = " ".join(e.value for e in at.error)
        assert "用户名或密码不正确" in shown, shown
        assert "操作太频繁" not in shown, "第一次点击不该触发限流"


class TestAuthenticatedApp:
    def test_boots_to_onboarding_without_providers(self, isolated_data_dir):
        """已登录 + 没有任何模型服务 → 主区显示第一步引导，不报错。"""
        settings = get_settings()
        store = UserStore(UserRepository(settings.data_root()), settings)
        store.admin_create_user("smoke", GOOD_PW)

        at = _logged_in_app("smoke")

        assert not at.exception, [str(e) for e in at.exception]
        body = " ".join(m.value for m in at.markdown)
        assert "欢迎" in body or "第一步" in body or "伴侣" in body

    def test_sidebar_has_core_sections(self, isolated_data_dir):
        settings = get_settings()
        store = UserStore(UserRepository(settings.data_root()), settings)
        store.admin_create_user("smoke2", GOOD_PW)

        at = _logged_in_app("smoke2")

        assert not at.exception, [str(e) for e in at.exception]
        labels = [e.label for e in at.sidebar.expander]
        assert any("我的伴侣" in x for x in labels), labels
        assert any("模型服务" in x for x in labels), labels

    def test_pages_switch_without_error(self, isolated_data_dir):
        """设置页与诊断页都要能渲染（这两页最容易因为字段名写错而崩）。"""
        settings = get_settings()
        store = UserStore(UserRepository(settings.data_root()), settings)
        store.admin_create_user("smoke3", GOOD_PW)

        for page_label in ["⚙️ 设置", "🩺 诊断"]:
            at = _logged_in_app("smoke3", _page_radio=page_label)
            assert not at.exception, f"{page_label}: " + str([str(e) for e in at.exception])


class TestProductionGuard:
    def test_production_misconfig_shows_error_page(self, tmp_root, monkeypatch):
        """生产环境密钥不合格时必须红屏拦住，而不是带病启动。"""
        from soulmate.core import settings as settings_mod

        strict = settings_mod.Settings(
            env="production",
            app_secret="short",  # 太短 → 触发校验
            data_dir=tmp_root / "prod-data",
            allow_signup=True,
            debug=False,
        )
        monkeypatch.setattr(settings_mod, "get_settings", lambda: strict)

        # app.run 内部通过模块属性取 settings，这里同样打补丁到 ui.app 的引用
        from soulmate.ui import app as app_mod

        monkeypatch.setattr(app_mod, "get_settings", lambda: strict)

        at = AppTest.from_file(str(APP_PATH), default_timeout=30)
        at.run()
        assert not at.exception, [str(e) for e in at.exception]
        errors = " ".join(e.value for e in at.error)
        assert "生产配置" in errors or "拒绝启动" in errors, errors
