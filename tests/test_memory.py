"""
长期记忆层的单元测试（零依赖，标准库 unittest）

跑法（在项目根目录）：
    .venv\\Scripts\\python.exe -m unittest discover tests -v

装了 pytest 也能跑：
    pytest tests -v

【为什么这些测试值得留下】
项目以前每次验收都写临时脚本、跑完就删 —— 等于没有测试资产。
这两个类（parse_extraction / CompanionMemory）是**纯逻辑**，不碰网络、不碰真实 data，
是「最该被固化下来」的那部分：改动它们时能立刻知道有没有改坏。
"""
import os
import sys
import tempfile
import unittest

# 让测试能在项目根目录之外被 discover 到
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memory
from memory import CompanionMemory, parse_extraction


class TestParseExtraction(unittest.TestCase):
    """模型不会 100% 输出合法 JSON —— 这里把实测遇到的 8 种情况全固定下来。"""

    def test_正常数组(self):
        self.assertEqual(parse_extraction('["用户叫李永康", "用户养猫"]'),
                         ["用户叫李永康", "用户养猫"])

    def test_包了markdown代码块(self):
        self.assertEqual(parse_extraction('```json\n["用户叫李永康"]\n```'),
                         ["用户叫李永康"])

    def test_前后带解释文字(self):
        self.assertEqual(parse_extraction('好的，提取结果如下：["用户叫李永康"] 希望有帮助'),
                         ["用户叫李永康"])

    def test_空数组(self):
        self.assertEqual(parse_extraction("[]"), [])

    def test_纯文字无数组(self):
        self.assertEqual(parse_extraction("没有值得记住的信息"), [])

    def test_空字符串(self):
        self.assertEqual(parse_extraction(""), [])

    def test_非数组json(self):
        self.assertEqual(parse_extraction('{"name": "李永康"}'), [])

    def test_数组里混空串要过滤(self):
        self.assertEqual(parse_extraction('["用户叫李永康", "", "  ", "养猫"]'),
                         ["用户叫李永康", "养猫"])

    def test_None也不能炸(self):
        self.assertEqual(parse_extraction(None), [])


class TestCompanionMemory(unittest.TestCase):
    """存储层行为。用临时目录，绝不碰真实 data/memory/。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="soulmate_mem_test_")
        self._orig_dir = memory.MEMORY_DIR
        memory.MEMORY_DIR = self._tmp          # 把存储位置重定向到临时目录
        self.mem = CompanionMemory("test-companion")

    def tearDown(self):
        memory.MEMORY_DIR = self._orig_dir
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_文件不存在时读出空列表(self):
        self.assertEqual(self.mem.facts, [])

    def test_add返回真实新增条数(self):
        n = self.mem.add(["a", "b", "a"])      # 第三条重复
        self.assertEqual(n, 2)
        self.assertEqual([f["text"] for f in self.mem.facts], ["a", "b"])

    def test_重复add不会堆积(self):
        self.mem.add(["用户叫李永康"])
        self.mem.add(["用户叫李永康"])
        self.assertEqual(len(self.mem.facts), 1)

    def test_每条带id与时间(self):
        self.mem.add(["用户叫李永康"])
        f = self.mem.facts[0]
        self.assertTrue(f["id"])
        self.assertTrue(f["created_at"])
        self.assertIn("session_id", f)

    def test_空文本不写入(self):
        n = self.mem.add(["", "   ", None])
        self.assertEqual(n, 0)
        self.assertEqual(self.mem.facts, [])

    def test_落盘与重新读取一致(self):
        self.mem.add(["用户叫李永康"])
        other = CompanionMemory("test-companion")   # 新建对象，只能从磁盘读
        self.assertEqual([f["text"] for f in other.facts], ["用户叫李永康"])

    def test_remove(self):
        self.mem.add(["a", "b"])
        self.mem.remove(self.mem.facts[0]["id"])
        self.assertEqual([f["text"] for f in self.mem.facts], ["b"])

    def test_clear连文件一起删(self):
        self.mem.add(["a"])
        self.mem.clear()
        self.assertEqual(self.mem.facts, [])
        self.assertFalse(os.path.exists(self.mem.file_path))

    def test_没有记忆时prompt块为空串(self):
        self.assertEqual(self.mem.to_prompt_block(), "")

    def test_prompt块含条目文本(self):
        self.mem.add(["用户叫李永康"])
        block = self.mem.to_prompt_block()
        self.assertIn("你记得关于 TA 的事", block)
        self.assertIn("用户叫李永康", block)

    def test_prompt块按limit截断(self):
        self.mem.add([f"事实{i}" for i in range(40)])
        block = self.mem.to_prompt_block(limit=5)
        self.assertEqual(block.count("- "), 5)
        self.assertIn("事实39", block)      # 取的是最近 5 条
        self.assertNotIn("事实0\n", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
