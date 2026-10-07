"""一次性验收脚本：把「可上线」的硬指标逐条验一遍。

放在 scripts/ 下是为了可复现 —— 你（或 CI、或未来的我）随时能重跑，
而不是只信一句「我改完了」。

跑法：
    python scripts/verify.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 让脚本能 import 项目包（从任意工作目录跑都行）
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PY = sys.executable
results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  -- {detail}" if detail else ""))


def run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
    # 命令全部是脚本内写死的（python -m ruff/mypy/pytest、git ls-files），无外部输入
    p = subprocess.run(  # noqa: S603
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── 1. 分层完整性 ────────────────────────────────────────────
print("\n=== 1. 分层与包结构 ===")
for layer in ["core", "storage", "llm", "services", "auth", "ui"]:
    check((ROOT / "soulmate" / layer / "__init__.py").is_file(), f"存在 soulmate/{layer}/")

check((ROOT / "main.py").is_file(), "入口 main.py 存在")
check((ROOT / "legacy" / "main.py").is_file(), "v1 代码已归档到 legacy/")
check(not (ROOT / "companion_manager.py").exists(), "根目录已无 v1 平铺模块")

# ── 2. 分层铁律：依赖只能向下 ────────────────────────────────
print("\n=== 2. 分层铁律（依赖只能向下）===")
import_lists = {
    "core（不许依赖项目内其它层）": list((ROOT / "soulmate" / "core").glob("*.py")),
    "storage（不许依赖 services/ui）": list((ROOT / "soulmate" / "storage").rglob("*.py")),
}
for label, files in import_lists.items():
    violations = []
    for f in files:
        if f.name == "__init__.py":
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*from (soulmate\.\w+)", text, re.M):
            mod = m.group(1)
            if label.startswith("core") and not mod.startswith("soulmate.core"):
                violations.append(f"{f.name} -> {mod}")
            if label.startswith("storage") and mod.split(".")[1] in ("services", "ui"):
                violations.append(f"{f.name} -> {mod}")
    check(not violations, label, "; ".join(violations[:3]))

# 依赖边界：openai 只允许出现在 llm/ 层（含 retry.py 的错误分类）
openai_files = [
    str(p.relative_to(ROOT)).replace("\\", "/")
    for p in (ROOT / "soulmate").rglob("*.py")
    if re.search(r"^\s*(import openai|from openai)", p.read_text(encoding="utf-8"), re.M)
]
bad_openai = [f for f in openai_files if not f.startswith("soulmate/llm/")]
check(not bad_openai, "import openai 只在 llm/ 层", f"越界: {bad_openai}")

# import streamlit 只允许出现在 ui/（auth/oidc.py 是唯一例外：它适配 st.login）
ST_ALLOWED = ("soulmate/ui/", "soulmate/auth/oidc.py")
st_files = [
    str(p.relative_to(ROOT)).replace("\\", "/")
    for p in (ROOT / "soulmate").rglob("*.py")
    if re.search(r"^\s*(import streamlit|from streamlit)", p.read_text(encoding="utf-8"), re.M)
]
bad_st = [f for f in st_files if not f.startswith(ST_ALLOWED)]
check(not bad_st, "import streamlit 只在 ui/（oidc.py 例外）", f"越界: {bad_st}")

# ── 3. 质量门禁 ──────────────────────────────────────────────
print("\n=== 3. 质量门禁 ===")
code, out = run([PY, "-m", "ruff", "check", "soulmate", "tests", "main.py"])
check(code == 0, "ruff check 通过", out.strip().splitlines()[-1] if out.strip() else "")

code, out = run([PY, "-m", "mypy", "soulmate", "main.py"])
check(code == 0, "mypy 通过", out.strip().splitlines()[-1] if out.strip() else "")

code, out = run([PY, "-m", "pytest", "tests", "-q"])
m = re.search(r"(\d+) passed", out)
check(code == 0 and bool(m), f"pytest 全绿（{m.group(1) if m else '?'} 个用例）",
      out.strip().splitlines()[-1] if out.strip() else "")

# ── 4. 交付物完整性 ──────────────────────────────────────────
print("\n=== 4. 交付物 ===")
for rel in [
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    ".github/workflows/ci.yml",
    ".pre-commit-config.yaml",
    "docs/ARCHITECTURE.md",
    "docs/SECURITY.md",
    "docs/DEPLOYMENT.md",
    "docs/MIGRATION.md",
    "legacy/README.md",
]:
    check((ROOT / rel).is_file(), f"存在 {rel}")

# ── 5. 安全硬指标 ────────────────────────────────────────────
print("\n=== 5. 安全硬指标 ===")
check("api_key_enc" in read("soulmate/storage/repositories/providers.py"), "API Key 加密落盘（api_key_enc）")
check("Fernet" in read("soulmate/core/security.py"), "使用 Fernet 对称加密")
check("bcrypt" in read("soulmate/core/security.py"), "口令用 bcrypt 哈希")
check("allow_private_base_url" in read("soulmate/core/validation.py"), "有 SSRF 内网地址防护")
check("validate_for_startup" in read("soulmate/core/settings.py"), "生产配置 fail-fast 自检")
check("def redact" in read("soulmate/core/exceptions.py"), "日志/异常脱敏函数存在")
check("class RateLimiter" in read("soulmate/core/ratelimit.py"), "有限流实现")

# 残留的静默 except: pass（旧版有 9 处）
silent = []
for p in (ROOT / "soulmate").rglob("*.py"):
    text = p.read_text(encoding="utf-8")
    for m in re.finditer(r"except[^\n]*:\s*\n\s*pass\s*\n", text):
        line = text[: m.start()].count("\n") + 1
        silent.append(f"{p.relative_to(ROOT)}:{line}")
check(not silent, "没有静默吞异常的 except: pass", str(silent[:4]))

# 硬编码内网 IP：只看**预设里真正会被用作 base_url 的值**，不看解释性注释
from soulmate.core.presets import PROVIDER_PRESETS  # noqa: E402

offenders = []
for key, preset in PROVIDER_PRESETS.items():
    url = str(preset.get("base_url") or "")
    if re.search(r"\b(?:10|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", url):
        offenders.append(f"{key}: {url}")
check(not offenders, "厂商预设里没有硬编码内网/私有 IP", str(offenders))

# ── 6. 数据目录不被跟踪 ──────────────────────────────────────
print("\n=== 6. 数据与密钥不进版本库 ===")
code, out = run(["git", "ls-files"])
tracked = out.splitlines()
leaked = [f for f in tracked if f.startswith(("data/", "session/")) or f in (".env", ".streamlit/secrets.toml")]
check(not leaked, "git 未跟踪 data/、session/、.env、secrets.toml", str(leaked[:3]))

gi = read(".gitignore")
for pat in ["data/", "session/", ".env", "secrets.toml"]:
    check(pat in gi, f".gitignore 含 {pat}")

# ── 7. CI 覆盖 ───────────────────────────────────────────────
print("\n=== 7. CI 覆盖 ===")
ci = read(".github/workflows/ci.yml")
for item, key in [("跑 ruff", "ruff check"), ("跑 mypy", "mypy "), ("跑 pytest", "pytest tests"), ("跑镜像构建", "docker/build-push-action")]:
    check(key in ci, f"CI {item}")

# ── 汇总 ─────────────────────────────────────────────────────
failed = [r for r in results if not r[0]]
print("\n" + "=" * 60)
print(f"总计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
if failed:
    print("\n失败项：")
    for _ok, name, detail in failed:
        print(f"  ✗ {name}  {detail}")
    sys.exit(1)
print("全部通过 ✅")
