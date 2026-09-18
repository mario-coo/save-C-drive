# -*- coding: utf-8 -*-
"""
test_v2_features.py - C盘大师 v2.0 核心探测与动作防护测试套件
"""

import os
import sys
import unittest

# 将 src 目录加入路径
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

import scanner
import actions


class TestScannerV2(unittest.TestCase):
    """测试 scanner.py 新增的探测能力"""

    def test_get_hibernation_info(self):
        info = scanner.get_hibernation_info()
        self.assertIsInstance(info, dict)
        self.assertIn("enabled", info)
        self.assertIn("file_exists", info)
        self.assertIn("size_gb", info)
        self.assertIn("mode", info)
        self.assertIsInstance(info["size_gb"], float)

    def test_get_driver_store_info(self):
        info = scanner.get_driver_store_info()
        self.assertIsInstance(info, dict)
        self.assertIn("total_size_gb", info)
        self.assertIn("total_drivers", info)
        self.assertIn("superseded_drivers", info)
        self.assertIsInstance(info["total_size_gb"], float)

    def test_find_wsl2_vdisks(self):
        disks = scanner.find_wsl2_vdisks()
        self.assertIsInstance(disks, list)
        for d in disks:
            self.assertIn("path", d)
            self.assertIn("size_gb", d)
            self.assertIn("distro", d)

    def test_get_update_download_cache_info(self):
        info = scanner.get_update_download_cache_info()
        self.assertIsInstance(info, dict)
        self.assertIn("size_gb", info)
        self.assertIn("file_count", info)

    def test_get_privacy_traces_metrics(self):
        metrics = scanner.get_privacy_traces_metrics()
        self.assertIsInstance(metrics, dict)
        expected_keys = [
            "run_mru_count",
            "recent_files_count",
            "jumplist_count",
            "prefetch_count",
            "thumbcache_size_mb",
        ]
        for k in expected_keys:
            self.assertIn(k, metrics)


class TestActionsV2(unittest.TestCase):
    """测试 actions.py 新增动作接口的签名与回调鲁棒性"""

    def test_flush_dns_cache(self):
        logs = []
        success = actions.flush_dns_cache(lambda msg: logs.append(msg))
        self.assertTrue(success)
        self.assertTrue(any("DNS" in m for m in logs))

    def test_clean_run_mru(self):
        logs = []
        # 执行清理并在回调中捕获
        res = actions.clean_run_mru(lambda msg: logs.append(msg))
        self.assertIsInstance(res, bool)

    def test_clean_recent_and_jumplists(self):
        logs = []
        res = actions.clean_recent_and_jumplists(lambda msg: logs.append(msg))
        self.assertIsInstance(res, int)  # 返回释放的条目数


if __name__ == "__main__":
    unittest.main()
