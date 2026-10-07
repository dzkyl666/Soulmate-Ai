"""命令行入口：`soulmate <command>`。

生产部署时，服务器上未必方便用浏览器开号（或已关闭自助注册），
管理员靠这里操作：
    soulmate doctor            自检环境与配置
    soulmate create-user       开号（不要求已关闭的注册开关，这是管理员动作）
    soulmate list-users        列出账号
    soulmate migrate --user x  把老版本数据搬进某个用户
    soulmate version           版本
"""

from __future__ import annotations

import argparse
import getpass
import sys

import soulmate
from soulmate.auth.user_store import UserStore
from soulmate.core.logging import setup_logging
from soulmate.core.settings import get_settings
from soulmate.services.container import ServiceContainer
from soulmate.services.migration import run_all


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"soulmate {soulmate.__version__}")
    return 0


def _cmd_doctor(_args: argparse.Namespace) -> int:
    settings = get_settings()
    problems = settings.validate_for_startup()
    print(f"env         : {settings.env}")
    print(f"data_dir    : {settings.data_root()}")
    print(f"auth_mode   : {settings.auth_mode}")
    print(f"allow_signup: {settings.allow_signup}")
    print(f"app_secret  : {'yes' if settings.app_secret else '<empty>'} (len={len(settings.app_secret)})")
    if problems:
        print("\n[警告] 生产配置问题：")
        for p in problems:
            print("  -", p)
        return 1
    print("\n配置无致命问题。")
    return 0


def _cmd_create_user(args: argparse.Namespace) -> int:
    settings = get_settings()
    store = UserStore(ServiceContainer(settings, "soulmate-cli").user_repo, settings)
    username = args.username or input("用户名: ").strip()
    if args.password:
        password = args.password
    else:
        password = getpass.getpass("密码（至少8位，含大小写和数字）: ")
    try:
        user = store.admin_create_user(username, password, role=args.role)
    except Exception as exc:
        print(f"[错误] {exc}")
        return 1
    print(f"已创建用户 {user.username}（角色 {user.role}）")

    # 顺手迁移：第一个用户通常带着老数据
    container = ServiceContainer(settings, user.username)
    summary = run_all(settings, user.username, container)
    if summary["flat_moved"] or summary["legacy_sessions"]:
        print(f"已迁移老数据: {summary}")
    return 0


def _cmd_list_users(_args: argparse.Namespace) -> int:
    settings = get_settings()
    repo = ServiceContainer(settings, "soulmate-cli").user_repo
    for u in repo.list():
        print(f"{u.username:24s} role={u.role:6s} disabled={u.disabled}")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    settings = get_settings()
    container = ServiceContainer(settings, args.user)
    summary = run_all(settings, args.user, container)
    print(f"迁移完成: {summary}")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(prog="soulmate", description="Soulmate AI 管理工具")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("version", help="显示版本")
    sub.add_parser("doctor", help="环境自检")
    sub.add_parser("list-users", help="列出账号")

    p = sub.add_parser("create-user", help="创建用户（管理员开号）")
    p.add_argument("--username", default="")
    p.add_argument("--password", default="")
    p.add_argument("--role", choices=["admin", "user"], default="user")

    p = sub.add_parser("migrate", help="迁移老版本数据到指定用户")
    p.add_argument("--user", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "version":
        return _cmd_version(args)
    if args.cmd == "doctor":
        return _cmd_doctor(args)
    if args.cmd == "create-user":
        return _cmd_create_user(args)
    if args.cmd == "list-users":
        return _cmd_list_users(args)
    if args.cmd == "migrate":
        return _cmd_migrate(args)
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
