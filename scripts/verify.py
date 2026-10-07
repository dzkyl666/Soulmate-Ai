"""验收脚本 v2：把「可上线」的硬指标逐条验一遍，并且**标明每条的可信度**。

跑法：
    python scripts/verify.py

──────────────────────────────────────────────────────────────
【v2 相对 v1 的关键修正 —— 先读这段再读代码】

v1 有 50 项、跑出来 50/50 全绿，但它有**结构性盲区**：

  1. 50 项里 39 项是「文件存在 / 字符串存在」类弱检查。
     `"class RateLimiter" in text` 这种断言，在「模块、配置、测试、文档四件套齐全，
     但生产代码零调用」时照样通过 —— 限流空转就是这么被放过的。
  2. 分层铁律只覆盖了 6 层里的 **2 层边界**（core 与 storage），
     压根没检查 `ui` 不许直连 `storage`。所以那两处越层在结构上
     **不可能**被 v1 抓到 —— 50/50 全绿与越层是并存关系，不是偶然漏掉。

结论：v1 的两个盲区，恰好就是两个真实缺陷的藏身处。
所以 v2 不是「多加几条」，而是**改检查框架的覆盖面**：

  【强】命令实跑      —— ruff / mypy / pytest / git（外部工具的真结论）
  【强】AST 结构分析  —— 六层依赖矩阵、能力调用点存在性（用 AST，不用正则）
  【中】行为断言映射  —— 每项能力必须对应一个**真实存在且被收集**的测试
  【弱】存在性        —— 文件/字符串（保留，但明确标注，不再与强项混为一谈）

汇总会按这三档分别计数 —— 避免再出现「50/50 全绿」这种掩盖盲区的表述。
──────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 让脚本能 import 项目包（从任意工作目录跑都行）
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PY = sys.executable

# 可信度分档
STRONG, MEDIUM, WEAK = "强", "中", "弱"
results: list[tuple[bool, str, str, str]] = []   # (ok, name, detail, tier)


def check(ok: bool, name: str, detail: str = "", tier: str = WEAK) -> None:
    results.append((bool(ok), name, detail, tier))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}][{tier}] {name}" + (f"  -- {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    # 命令全部是脚本内写死的，无外部输入
    p = subprocess.run(  # noqa: S603
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════
# AST 工具：所有结构分析都走 AST，不用正则
# ══════════════════════════════════════════════════════════════
def _project_py_files() -> list[Path]:
    return sorted(p for p in (ROOT / "soulmate").rglob("*.py") if p.is_file())


def _layer_of_module(module: str) -> str:
    """`soulmate.services.container` → `services`。"""
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def _layer_of_file(path: Path) -> str:
    rel = path.relative_to(ROOT / "soulmate")
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _resolve_import_targets(module: str, aliases: list[str]) -> set[str]:
    """把 `from X import a, b` 解析成**真实目标模块**的集合。

    为什么要解析而不是直接取 X：
      `from soulmate.auth import oidc` 的 module 是 `soulmate.auth`（包），
      真实目标是子模块 `soulmate.auth.oidc`。只取 X 会丢失粒度，
      导致「精确到模块的白名单」无法表达。
    """
    base = ROOT / Path(*module.split("."))
    if (base.parent / f"{base.name}.py").is_file():
        # 指向一个 .py 模块 → 别名都是符号，不是子模块
        return {module}
    if (base / "__init__.py").is_file():
        out: set[str] = set()
        for a in aliases:
            if (base / a).is_dir() or (base / f"{a}.py").is_file():
                out.add(f"{module}.{a}")
            else:
                out.add(module)
        return out
    return {module}


def _imports(path: Path) -> list[tuple[int, str]]:
    """提取文件里的项目内 import → [(行号, 目标模块)]。

    用 AST 而非 grep 的理由：grep 会命中注释与 docstring。
    （真实教训：`core/presets.py` 的 docstring 里写着「已移除的内网 IP」，
      用 grep 扫内网 IP 会把它误判成违规。）
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("soulmate."):
                    found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if not mod.startswith("soulmate."):
                continue
            names = [a.name for a in node.names]
            for target in _resolve_import_targets(mod, names):
                found.append((node.lineno, target))
    return found


def _called_names(path: Path) -> set[str]:
    """收集文件里「被调用」的函数/类名（含 `x.f()` 的 f）。

    这是「能力调用点」检查的核心：它证明的是**被调用**，
    而不是被 import 或被写在注释里 —— 后者正是 v1 的漏洞所在。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _test_node_ids() -> set[str]:
    """用 pytest --collect-only 拿到全部可收集的测试 node id。

    这比 AST 扫测试文件更强：它证明测试**能被执行器收集到**
    （语法错、fixture 缺失导致无法收集的情况都会被抓住）。
    """
    code, out = run([PY, "-m", "pytest", "tests", "--collect-only", "-q", "--no-header"])
    ids: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if "::" in line and not line.startswith("="):
            ids.add(line)
    if code != 0 and not ids:
        print(f"  (warn) pytest --collect-only 异常：{out.strip()[-200:]}")
    return ids


# ══════════════════════════════════════════════════════════════
# §0 版本绑定（升级 3）
# ══════════════════════════════════════════════════════════════
def report_version_binding() -> tuple[str, bool]:
    """打印并返回 (版本指纹, 工作区是否干净)。

    v1 的「50/50」无法定位到任何一个提交 —— 这个缺陷是真实的。
    所以每次验收都必须把结论**钉在某个 commit 上**。
    """
    def git(*args: str) -> str:
        code, out = run(["git", *args])
        return out.strip() if code == 0 else ""

    head = git("rev-parse", "HEAD")
    short = git("rev-parse", "--short", "HEAD")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    dirty_raw = git("status", "--porcelain")
    dirty_files = [ln for ln in dirty_raw.splitlines() if ln.strip()]
    clean = not dirty_files

    print("=" * 62)
    print(f"验收版本：{short}  (分支 {branch})")
    print(f"完整 SHA：{head}")
    if clean:
        print("工作区：干净 ✓  —— 本次结论可归因到上面这个提交")
    else:
        print(f"工作区：**不干净** ✗  —— 有 {len(dirty_files)} 项未提交改动，结论无法绑定到提交：")
        for ln in dirty_files[:10]:
            print(f"    {ln}")
    print("=" * 62)
    return (short if clean else f"{short}+dirty({len(dirty_files)})"), clean


# ══════════════════════════════════════════════════════════════
# §1 命令实跑（强）
# ══════════════════════════════════════════════════════════════
def check_commands() -> None:
    section("1. 命令实跑（强）")

    code, out = run([PY, "-m", "ruff", "check", "soulmate", "tests", "main.py", "scripts"])
    check(code == 0, "ruff check 通过", out.strip().splitlines()[-1] if out.strip() else "", STRONG)

    code, out = run([PY, "-m", "mypy", "soulmate", "main.py"])
    check(code == 0, "mypy 通过", out.strip().splitlines()[-1] if out.strip() else "", STRONG)

    code, out = run([PY, "-m", "pytest", "tests", "-q"])
    m = re.search(r"(\d+) passed", out)
    n = int(m.group(1)) if m else 0
    check(code == 0 and n > 0, f"pytest 全绿（{n} 个用例）",
          out.strip().splitlines()[-1] if out.strip() else "", STRONG)
    # 测试规模本身也是判据：v1 只有 1 个测试文件
    check(n >= 200, f"测试规模 ≥200（当前 {n}）", "", STRONG)


# ══════════════════════════════════════════════════════════════
# §2 六层依赖矩阵（升级 1）
# ══════════════════════════════════════════════════════════════
# 为什么是**显式矩阵**而不是「目标层层号 ≤ 源层层号」：
# 后者用标量序号表达依赖，而 ui(4) → storage(1) 满足 1 ≤ 4，会被放行 ——
# 恰恰放过了本次要抓的越层。真实策略不是全序，必须显式声明。
ALLOWED_LAYERS: dict[str, set[str]] = {
    "core": {"core"},
    "storage": {"core", "storage"},
    "llm": {"core", "llm"},
    "auth": {"core", "storage", "auth"},
    "services": {"core", "storage", "llm", "auth", "services"},
    "ui": {"core", "services", "ui"},
    "cli": {"core", "services", "cli"},
}

# ui 层额外允许的跨层目标 —— **精确到模块**，且每条必须写清理由。
# 这是「有理由的例外」白名单，不是「为了通过而开后门」。
UI_CROSS_LAYER_ALLOWLIST: dict[str, str] = {
    "soulmate.llm.types": "流式事件协议：UI 必须按事件类型（chunk/fallback/error/end）分流渲染",
    "soulmate.auth.oidc": "Streamlit 登录适配器：st.login() 本质是 UI 行为，反转会让 services 依赖 streamlit",
}


def check_layer_matrix() -> None:
    section("2. 六层依赖矩阵（强 · AST 全量两两组合）")
    violations: list[str] = []
    allowlisted: list[str] = []
    edges = 0

    for path in _project_py_files():
        src = _layer_of_file(path)
        if src not in ALLOWED_LAYERS:
            continue
        for lineno, target in _imports(path):
            tgt = _layer_of_module(target)
            if not tgt or tgt == src:
                continue
            edges += 1
            if tgt in ALLOWED_LAYERS[src]:
                continue
            # ui 的跨层例外：目标必须精确命中白名单
            if src == "ui" and target in UI_CROSS_LAYER_ALLOWLIST:
                allowlisted.append(f"{path.relative_to(ROOT)}:{lineno} → {target}")
                continue
            violations.append(f"{path.relative_to(ROOT)}:{lineno}  {src} → {tgt}  ({target})")

    check(edges > 0, f"扫到 {edges} 条层间依赖边", "", STRONG)
    check(not violations, "依赖方向无违规（含 ui→storage 这类越层）",
          "; ".join(violations[:5]) if violations else "", STRONG)
    check(bool(allowlisted), f"白名单例外被真实使用（{len(allowlisted)} 处）", "", MEDIUM)
    for item in allowlisted:
        print(f"      · 允许的例外: {item}")

    # 显式反向断言：历史上那两处 ui→storage 违规必须不复存在
    ui_storage = [v for v in violations if "ui → storage" in v]
    check(not ui_storage, "历史上那两处 ui→storage 越层已消除", "", STRONG)

    impure_core = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "soulmate" / "core").glob("*.py")
        if any(_layer_of_module(mod) not in ("core", "") for _line, mod in _imports(p))
    ]
    check(not impure_core, "core 层不依赖项目内任何其它层", str(impure_core), STRONG)


# ══════════════════════════════════════════════════════════════
# §3 能力调用点存在性（升级 2）
# ══════════════════════════════════════════════════════════════
@dataclass
class Capability:
    name: str
    symbol: str
    required_callers: set[str] = field(default_factory=set)
    why: str = ""


CAPABILITIES: list[Capability] = [
    Capability(
        name="限流记账",
        symbol="get_limiter",
        required_callers={
            "soulmate/llm/service.py",              # chat() / stream() 两个入口
            "soulmate/services/memory_service.py",  # 抽取档
            "soulmate/auth/user_store.py",          # 登录档
        },
        why="v1 的致命盲区：模块+配置+测试+文档齐全，但生产代码零调用（空转）",
    ),
    Capability(
        name="日志/异常脱敏",
        symbol="redact",
        required_callers={"soulmate/core/logging.py"},
        why="脱敏必须挂在「日志出口」上才算生效，只定义不调用等于没有",
    ),
    Capability(
        name="读盘缓存",
        symbol="MTimeCache",
        required_callers={"soulmate/storage/repositories/sessions.py"},
        why="会话列表每次 rerun 全量读盘是 v1 的性能问题，必须确认缓存真被用上",
    ),
    Capability(
        name="系统诊断",
        symbol="collect",
        required_callers={"soulmate/ui/diagnostics_page.py"},
        why="可观测性模块产出为空 = 等于没有（验收标准反模式第 4 条）",
    ),
    Capability(
        name="数据备份",
        symbol="backup_user_data",
        required_callers={"soulmate/ui/settings_page.py"},
        why="备份能力必须在界面上有真实入口",
    ),
    Capability(
        name="认证门面",
        symbol="AuthService",
        required_callers={
            "soulmate/ui/app.py",
            "soulmate/ui/auth_page.py",
            "soulmate/services/container.py",
        },
        why="ui 不许直连 storage，必须经由 AuthService 门面（本轮修复的越层）",
    ),
]


def check_capability_wiring() -> None:
    section("3. 能力调用点存在性（强 · AST 调用图）")
    callers: dict[str, set[str]] = {}
    for path in _project_py_files():
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for name in _called_names(path):
            callers.setdefault(name, set()).add(rel)

    for cap in CAPABILITIES:
        actual = callers.get(cap.symbol, set())
        missing = {c for c in cap.required_callers if c not in actual}
        detail = f"缺失: {sorted(missing)}" if missing else f"调用点 {len(actual)} 处"
        check(not missing, f"「{cap.name}」在要求的调用点被真实调用（{cap.symbol}）", detail, STRONG)

    # ★ 阴性对照：证明这个检查**本身**能识别出「未被业务层调用」的符号。
    # 拿一个确定只在自身模块内使用的符号做对照；若它也被判为"有业务调用点"，
    # 说明检查失灵 —— 那上面那些 PASS 就都不可信了。
    negative = Capability(
        name="（阴性对照）", symbol="ProviderRegistry", required_callers={"soulmate/ui/chat.py"},
    )
    actual = callers.get(negative.symbol, set())
    check(
        bool(negative.required_callers - actual),
        "阴性对照：检查能识别出「未被业务层调用」的符号",
        "若这条失败，说明调用点检查失灵，上面的 PASS 不可信",
        STRONG,
    )


# ══════════════════════════════════════════════════════════════
# §4 能力-证据对照表（升级 4）
# ══════════════════════════════════════════════════════════════
# 规则：每一条「宣称已实现的能力」都必须挂一个**最小行为断言**（真实测试）。
# 缺了就红 —— 这是防「文档承诺 ≠ 实际行为」的机制。
CAPABILITY_EVIDENCE: list[tuple[str, str]] = [
    ("API Key 加密落盘", "tests/test_storage.py::TestProviderRepository::test_api_key_never_stored_in_plaintext"),
    ("旧明文 Key 平滑升级", "tests/test_storage.py::TestProviderRepository::test_legacy_plaintext_is_encrypted_on_next_save"),
    ("口令 bcrypt 哈希", "tests/test_core.py::TestPasswords::test_hash_and_verify"),
    ("会话令牌防篡改", "tests/test_core.py::TestTokens::test_tampered_payload_rejected"),
    ("路径穿越防护", "tests/test_core.py::TestValidateId::test_rejects_dangerous_ids"),
    ("SSRF 内网拦截", "tests/test_core.py::TestValidateBaseUrl::test_blocks_loopback_by_default"),
    ("日志脱敏屏蔽 Key", "tests/test_core.py::TestRedact::test_masks_openai_style_key"),
    ("限流：登录档接线", "tests/test_ratelimit_wiring.py::TestLoginTierIsWired::test_nth_plus_one_attempt_raises"),
    ("限流：失败尝试也计数", "tests/test_ratelimit_wiring.py::TestLoginTierIsWired::test_failed_attempts_are_also_counted"),
    ("限流：chat 档接线", "tests/test_ratelimit_wiring.py::TestChatTierIsWired::test_chat_entry_charges_quota"),
    ("限流：stream 档接线", "tests/test_ratelimit_wiring.py::TestChatTierIsWired::test_stream_entry_charges_quota"),
    ("限流：chat/stream 共享配额", "tests/test_ratelimit_wiring.py::TestChatTierIsWired::test_chat_and_stream_share_one_quota"),
    ("限流：extract 档接线", "tests/test_ratelimit_wiring.py::TestExtractTierIsWired::test_extract_entry_charges_quota"),
    ("限流：按用户隔离", "tests/test_ratelimit_wiring.py::TestQuotaIsolation::test_chat_quota_isolated_between_users"),
    ("限流：容器注入 user_id", "tests/test_ratelimit_wiring.py::TestContainerInjectsUserId::test_container_meters_its_own_llm_per_user"),
    ("模型降级链", "tests/test_llm.py::TestStreamFallback::test_partial_then_fallback_emits_fallback_event"),
    ("多用户物理隔离", "tests/test_services.py::TestContainer::test_two_users_are_physically_isolated"),
    ("迁移幂等", "tests/test_migration.py::TestFlatMigration::test_is_idempotent"),
    ("真实数据不被测试改动", "tests/test_data_safety.py::TestRealDataIsProtected::test_real_project_data_untouched"),
    ("AuthService 门面可用", "tests/test_auth.py::TestAuthServiceFacade::test_bootstrap_then_authenticate_roundtrip"),
    ("UI 首屏能启动", "tests/test_apptest.py::TestAuthenticatedApp::test_boots_to_onboarding_without_providers"),
    ("生产配置 fail-fast", "tests/test_apptest.py::TestProductionGuard::test_production_misconfig_shows_error_page"),
]


def check_capability_evidence(collected: set[str]) -> None:
    section("4. 能力-证据对照表（中 · 每项能力必须有真实测试）")
    check(len(collected) > 0, f"pytest 收集到 {len(collected)} 个测试 node id", "", MEDIUM)

    # 参数化测试收集到的 node id 带 `[参数]` 后缀（如 test_rejects_dangerous_ids[../etc/passwd]），
    # 所以用「前缀匹配」而不是精确相等 —— 精确匹配会对参数化用例误报缺失。
    def _present(node: str) -> bool:
        return any(c == node or c.startswith(f"{node}[") for c in collected)

    missing = [f"{cap} → {node}" for cap, node in CAPABILITY_EVIDENCE if not _present(node)]
    check(not missing, f"{len(CAPABILITY_EVIDENCE)} 项能力均有对应测试且可被收集",
          "; ".join(missing[:6]) if missing else "", MEDIUM)
    if missing:
        print("      缺失明细：")
        for m in missing:
            print(f"        · {m}")


# ══════════════════════════════════════════════════════════════
# §5 安全静态指标
# ══════════════════════════════════════════════════════════════
def check_security_surface() -> None:
    section("5. 安全静态指标（弱档仅证明「存在」，行为证据见 §4）")

    check("api_key_enc" in read("soulmate/storage/repositories/providers.py"), "API Key 加密落盘（api_key_enc）", "", WEAK)
    check("Fernet" in read("soulmate/core/security.py"), "使用 Fernet 对称加密", "", WEAK)
    check("bcrypt" in read("soulmate/core/security.py"), "口令用 bcrypt 哈希", "", WEAK)
    check("allow_private_base_url" in read("soulmate/core/validation.py"), "有 SSRF 内网地址防护", "", WEAK)
    check("validate_for_startup" in read("soulmate/core/settings.py"), "生产配置 fail-fast 自检", "", WEAK)
    check("def redact" in read("soulmate/core/exceptions.py"), "脱敏函数存在", "", WEAK)
    check("redact(" in read("soulmate/core/logging.py"), "日志出口调用脱敏", "", MEDIUM)

    # 静默吞异常：AST 找 `except: pass`（比 grep 准，不会命中注释）
    silent: list[str] = []
    for path in _project_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                silent.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    check(not silent, "没有静默吞异常的 except: pass", str(silent[:4]), STRONG)

    # 硬编码内网 IP：只看预设里**真会被用作 base_url** 的值（不看注释）
    from soulmate.core.presets import PROVIDER_PRESETS

    offenders = [
        f"{k}: {v.get('base_url')}"
        for k, v in PROVIDER_PRESETS.items()
        if re.search(r"\b(?:10|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", str(v.get("base_url") or ""))
    ]
    check(not offenders, "厂商预设无硬编码内网/私有 IP", str(offenders), STRONG)


# ══════════════════════════════════════════════════════════════
# §6 交付物与仓库卫生（弱）
# ══════════════════════════════════════════════════════════════
def check_deliverables() -> None:
    section("6. 交付物与仓库卫生（弱 · 存在性）")
    for rel in [
        "README.md", "pyproject.toml", "requirements.txt", "requirements-dev.txt",
        ".env.example", ".gitignore", ".dockerignore", "Dockerfile", "docker-compose.yml",
        ".github/workflows/ci.yml", ".pre-commit-config.yaml",
        "docs/ARCHITECTURE.md", "docs/SECURITY.md", "docs/DEPLOYMENT.md", "docs/MIGRATION.md",
        "legacy/README.md",
    ]:
        check((ROOT / rel).is_file(), f"存在 {rel}", "", WEAK)

    for layer in ("core", "storage", "llm", "services", "auth", "ui"):
        check((ROOT / "soulmate" / layer / "__init__.py").is_file(), f"分层包存在 soulmate/{layer}/", "", WEAK)

    check((ROOT / "main.py").is_file(), "入口 main.py 存在", "", WEAK)
    check((ROOT / "legacy" / "main.py").is_file(), "v1 代码已归档到 legacy/", "", WEAK)
    check(not (ROOT / "companion_manager.py").exists(), "根目录已无 v1 平铺模块", "", WEAK)

    _, out = run(["git", "ls-files"])
    tracked = out.splitlines()
    leaked = [
        f for f in tracked
        if f.startswith(("data/", "session/")) or f in (".env", ".streamlit/secrets.toml")
    ]
    check(not leaked, "git 未跟踪 data/、session/、.env、secrets.toml", str(leaked[:3]), MEDIUM)

    gi = read(".gitignore")
    for pat in ["data/", "session/", ".env", "secrets.toml"]:
        check(pat in gi, f".gitignore 含 {pat}", "", WEAK)


# ══════════════════════════════════════════════════════════════
# §7 CI 覆盖（弱）
# ══════════════════════════════════════════════════════════════
def check_ci() -> None:
    section("7. CI 覆盖（弱 · 配置存在性）")
    ci = read(".github/workflows/ci.yml")
    for item, key in [
        ("跑 ruff", "ruff check"), ("跑 mypy", "mypy "), ("跑 pytest", "pytest tests"),
        ("跑镜像构建", "docker/build-push-action"), ("把 scripts 也纳入 lint", "scripts"),
    ]:
        check(key in ci, f"CI {item}", "", WEAK)


# ══════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════
def main() -> int:
    version, clean = report_version_binding()

    check_commands()
    check_layer_matrix()
    check_capability_wiring()
    collected = _test_node_ids()
    check_capability_evidence(collected)
    check_security_surface()
    check_deliverables()
    check_ci()

    failed = [r for r in results if not r[0]]
    by_tier = {t: [r for r in results if r[3] == t] for t in (STRONG, MEDIUM, WEAK)}

    print("\n" + "=" * 62)
    print(f"验收版本：{version}" + ("（工作区干净）" if clean else "（含未提交改动 —— 结论不可归因到提交）"))
    print("-" * 62)
    for t in (STRONG, MEDIUM, WEAK):
        items = by_tier[t]
        ok = sum(1 for r in items if r[0])
        print(f"  [{t}] 通过 {ok}/{len(items)}")
    print("-" * 62)
    print(f"总计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("\n失败项：")
        for _ok, name, detail, tier in failed:
            print(f"  ✗ [{tier}] {name}  {detail}")
        print("\n注意：强档失败意味着**结构性/行为性**问题，不是文档措辞问题。")
        return 1
    print("\n全部通过 ✅")
    print("提示：弱档多为「存在性」检查，证明不了行为；结论以强/中档为准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
