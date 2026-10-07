"""
底层存储工具
============
只做一件事：把 JSON 安全地读写到磁盘。

【为什么单独一层】
不掺任何业务逻辑、也不 import streamlit —— 这样它最"死"，也最好测。
上层 companion_manager.py 决定"存什么、放在哪"，本文件只管"怎么落到磁盘上"。
"""
import os
import json


def ensure_dir(path: str) -> None:
    """目录不存在就创建（已存在也不会报错）"""
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def read_json(path: str, default=None):
    """读 JSON；文件不存在 / 内容损坏都返回 default，绝不抛异常

    这是刻意的设计：配置文件被手动改坏时，程序不应该整个崩掉，
    而是当作"没配过"继续跑。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path: str, data) -> None:
    """写 JSON：自动建父目录；ensure_ascii=False 让中文保持可读（方便你直接打开看）

    先写临时文件、再 os.replace 原子替换 —— 避免写到一半崩溃把原文件毁掉。
    """
    ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def delete_file(path: str) -> bool:
    """删文件；文件不存在或删除失败都返回 False（调用方无需 try）"""
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except Exception:
        pass
    return False


def list_json_names(dirpath: str) -> list:
    """列出目录下所有 .json 文件的名字（去掉扩展名）；目录不存在返回空列表"""
    if not os.path.isdir(dirpath):
        return []
    return [n[:-5] for n in os.listdir(dirpath) if n.endswith(".json")]


def delete_dir(dirpath: str) -> None:
    """递归删除整个目录（删伴侣时连带删它名下的会话）"""
    import shutil
    if dirpath and os.path.isdir(dirpath):
        shutil.rmtree(dirpath, ignore_errors=True)
