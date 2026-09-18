# -*- coding: utf-8 -*-
"""
main.py - C盘深度分析与空间优化大师 (DiskCleanPro)
现代化 PySide6 桌面端应用主程序
"""

import sys
import os
import time
from typing import List, Dict, Any, Optional

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QIcon, QFont, QColor, QBrush, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QTextEdit, QSplitter, QFrame, QMessageBox, QComboBox,
    QLineEdit, QAbstractItemView, QMenu
)

import scanner
import actions


class NumericTableWidgetItem(QTableWidgetItem):
    """支持按真实字节数进行数值排序的 TableWidgetItem"""
    def __init__(self, display_text: str, sort_value: int):
        super().__init__(display_text)
        self.sort_value = sort_value

    def __lt__(self, other):
        if isinstance(other, NumericTableWidgetItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


class ScanWorker(QThread):
    """后台扫描工作线程"""
    item_scanned = Signal(object)      # 发射单个 TargetItem
    scan_finished = Signal(dict)       # 发射全局磁盘与内存状态
    log_message = Signal(str)

    def run(self):
        self.log_message.emit("【开始全盘扫描】正在探测磁盘、内存与虚拟内存指标...")
        t0 = time.time()

        # 1. 采集全局系统状态
        c_stat = scanner.get_disk_metrics("C")
        h_stat = scanner.get_disk_metrics("H")
        mem_stat = scanner.get_memory_metrics()
        page_stat = scanner.get_pagefile_config()
        vss_stat = scanner.get_vss_storage_info()

        sys_metrics = {
            "c_disk": c_stat,
            "h_disk": h_stat,
            "memory": mem_stat,
            "pagefile": page_stat,
            "vss": vss_stat
        }

        # 2. 扫描指定目标库
        targets = scanner.get_scan_targets()
        for idx, item in enumerate(targets):
            self.log_message.emit(f"正在扫描 [{idx+1}/{len(targets)}]: {item.name}...")
            # 特殊目标处理
            if item.item_id == "vss_storage":
                item.size_bytes = int(vss_stat["used_gb"] * (1024 ** 3))
                item.exists = True
            elif item.item_id == "c_pagefile":
                if os.path.exists(item.path):
                    item.exists = True
                    try:
                        item.size_bytes = os.path.getsize(item.path)
                    except Exception:
                        item.size_bytes = int(page_stat.get("c_file_size_gb", 0) * (1024**3))
                else:
                    item.exists = False
                    item.size_bytes = 0
            elif item.item_id == "h_pagefile":
                if os.path.exists(item.path):
                    item.exists = True
                    try:
                        item.size_bytes = os.path.getsize(item.path)
                    except Exception:
                        item.size_bytes = int(page_stat.get("h_file_size_gb", 0) * (1024**3))
                else:
                    item.exists = False
                    item.size_bytes = 0
            elif item.item_id == "winsxs":
                # WinSxS 计算部分目录快速预估或实际存在
                item.exists = os.path.exists(item.path)
                item.size_bytes = scanner.fast_calc_dir_size(item.path, max_depth=2)
            else:
                item.exists = os.path.exists(item.path)
                if item.exists:
                    item.size_bytes = scanner.fast_calc_dir_size(item.path, max_depth=6)
                else:
                    item.size_bytes = 0

            self.item_scanned.emit(item)

        cost = time.time() - t0
        self.log_message.emit(f"【扫描完毕】总耗时 {cost:.2f} 秒。已罗列所有高占用与核心模块。")
        self.scan_finished.emit(sys_metrics)


class ActionWorker(QThread):
    """后台动作执行线程"""
    action_log = Signal(str)
    action_done = Signal(str, bool)

    def __init__(self, action_name: str, **kwargs):
        super().__init__()
        self.action_name = action_name
        self.kwargs = kwargs

    def run(self):
        log_cb = lambda msg: self.action_log.emit(msg)

        if self.action_name == "clean_safe":
            freed = actions.clean_safe_paths(log_cb)
            freed_mb = freed / (1024 * 1024)
            self.action_done.emit(f"安全清理执行完毕，共释放空间: {freed_mb:.2f} MB", True)

        elif self.action_name == "optimize_vss":
            success = actions.optimize_vss(self.kwargs.get("max_size", "4GB"), log_cb)
            self.action_done.emit("卷影存储配额调整完毕", success)

        elif self.action_name == "migrate_pagefile":
            drive = self.kwargs.get("drive", "H")
            success = actions.configure_pagefile_drive(drive, log_cb)
            self.action_done.emit(f"虚拟内存已切换配置至 {drive} 盘", success)

        elif self.action_name == "dism_cleanup":
            success = actions.run_dism_cleanup(log_cb)
            self.action_done.emit("DISM 组件清理执行完毕", success)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("C盘空间深度检测与优化大师 - DiskCleanPro")
        self.resize(1120, 780)
        self.setMinimumSize(960, 640)

        self.scanned_items: List[scanner.TargetItem] = []
        self.active_worker: Optional[QThread] = None

        self._apply_theme()
        self._init_ui()
        self._start_scan()

    def _apply_theme(self):
        """专业现代深色科技主题 QSS"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #121318;
            }
            QWidget {
                color: #e2e8f0;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 13px;
            }
            QFrame.card {
                background-color: #1a1c23;
                border: 1px solid #2d323f;
                border-radius: 8px;
                padding: 10px;
            }
            QLabel.card-title {
                color: #94a3b8;
                font-size: 12px;
                font-weight: bold;
            }
            QLabel.card-value {
                color: #38bdf8;
                font-size: 20px;
                font-weight: 800;
            }
            QLabel.card-desc {
                color: #64748b;
                font-size: 11px;
            }
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton:pressed {
                background-color: #1e40af;
            }
            QPushButton.btn-green {
                background-color: #10b981;
            }
            QPushButton.btn-green:hover {
                background-color: #059669;
            }
            QPushButton.btn-amber {
                background-color: #d97706;
            }
            QPushButton.btn-amber:hover {
                background-color: #b45309;
            }
            QPushButton.btn-purple {
                background-color: #8b5cf6;
            }
            QPushButton.btn-purple:hover {
                background-color: #7c3aed;
            }
            QPushButton.btn-secondary {
                background-color: #2d323f;
                color: #cbd5e1;
            }
            QPushButton.btn-secondary:hover {
                background-color: #3b4252;
            }
            QPushButton.btn-sm {
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 500;
                border-radius: 4px;
            }
            QTableWidget {
                background-color: #16181f;
                alternate-background-color: #1b1e27;
                border: 1px solid #2d323f;
                border-radius: 8px;
                gridline-color: #262a37;
                selection-background-color: #2e384d;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #1f232d;
                color: #cbd5e1;
                font-weight: 600;
                padding: 6px 8px;
                border: 1px solid #2d323f;
            }
            QHeaderView::section:hover {
                background-color: #282e3b;
            }
            QProgressBar {
                background-color: #2d323f;
                border-radius: 4px;
                text-align: center;
                height: 8px;
                color: transparent;
            }
            QProgressBar::chunk {
                background-color: #38bdf8;
                border-radius: 4px;
            }
            QLineEdit, QComboBox {
                background-color: #1a1c23;
                border: 1px solid #333a4d;
                border-radius: 6px;
                padding: 5px 10px;
                color: #f1f5f9;
            }
            QTextEdit {
                background-color: #0d0e12;
                border: 1px solid #232734;
                border-radius: 6px;
                color: #a5f3fc;
                font-family: "Consolas", "Courier New", monospace;
                font-size: 12px;
            }
        """)

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. 顶部状态指标卡片栏
        dashboard_layout = QHBoxLayout()
        dashboard_layout.setSpacing(12)

        # 卡片 1: C 盘状态
        self.card_c = QFrame()
        self.card_c.setProperty("class", "card")
        c_box = QVBoxLayout(self.card_c)
        c_title = QLabel("C: 盘空间 (系统分区)")
        c_title.setProperty("class", "card-title")
        self.c_value = QLabel("可用: -- GB")
        self.c_value.setProperty("class", "card-value")
        self.c_prog = QProgressBar()
        self.c_prog.setRange(0, 100)
        self.c_desc = QLabel("总计: -- GB | 已用: -- GB")
        self.c_desc.setProperty("class", "card-desc")
        c_box.addWidget(c_title)
        c_box.addWidget(self.c_value)
        c_box.addWidget(self.c_prog)
        c_box.addWidget(self.c_desc)
        dashboard_layout.addWidget(self.card_c)

        # 卡片 2: H 盘状态
        self.card_h = QFrame()
        self.card_h.setProperty("class", "card")
        h_box = QVBoxLayout(self.card_h)
        h_title = QLabel("H: 盘空间 (同 256GB SSD 分区)")
        h_title.setProperty("class", "card-title")
        self.h_value = QLabel("可用: -- GB")
        self.h_value.setProperty("class", "card-value")
        self.h_prog = QProgressBar()
        self.h_prog.setRange(0, 100)
        self.h_desc = QLabel("托管 Pagefile 零性能损耗")
        self.h_desc.setProperty("class", "card-desc")
        h_box.addWidget(h_title)
        h_box.addWidget(self.h_value)
        h_box.addWidget(self.h_prog)
        h_box.addWidget(self.h_desc)
        dashboard_layout.addWidget(self.card_h)

        # 卡片 3: 虚拟内存与负载
        self.card_mem = QFrame()
        self.card_mem.setProperty("class", "card")
        mem_box = QVBoxLayout(self.card_mem)
        mem_title = QLabel("内存与虚拟内存 (Commit)")
        mem_title.setProperty("class", "card-title")
        self.mem_value = QLabel("RAM 可用: -- GB")
        self.mem_value.setProperty("class", "card-value")
        self.mem_value.setStyleSheet("color: #a7f3d0;")
        self.mem_prog = QProgressBar()
        self.mem_prog.setRange(0, 100)
        self.mem_desc = QLabel("当前 Pagefile 状态: 检测中...")
        self.mem_desc.setProperty("class", "card-desc")
        mem_box.addWidget(mem_title)
        mem_box.addWidget(self.mem_value)
        mem_box.addWidget(self.mem_prog)
        mem_box.addWidget(self.mem_desc)
        dashboard_layout.addWidget(self.card_mem)

        # 卡片 4: 卷影副本 (VSS)
        self.card_vss = QFrame()
        self.card_vss.setProperty("class", "card")
        vss_box = QVBoxLayout(self.card_vss)
        vss_title = QLabel("卷影副本 (VSS 快照保护)")
        vss_title.setProperty("class", "card-title")
        self.vss_value = QLabel("配额: -- GB")
        self.vss_value.setProperty("class", "card-value")
        self.vss_value.setStyleSheet("color: #fcd34d;")
        self.vss_prog = QProgressBar()
        self.vss_prog.setRange(0, 100)
        self.vss_desc = QLabel("已用快照: -- GB")
        self.vss_desc.setProperty("class", "card-desc")
        vss_box.addWidget(vss_title)
        vss_box.addWidget(self.vss_value)
        vss_box.addWidget(self.vss_prog)
        vss_box.addWidget(self.vss_desc)
        dashboard_layout.addWidget(self.card_vss)

        main_layout.addLayout(dashboard_layout)

        # 2. 功能操作按钮栏
        actions_bar = QHBoxLayout()
        actions_bar.setSpacing(8)

        self.btn_scan = QPushButton("🔍 重新全盘扫描")
        self.btn_scan.clicked.connect(self._start_scan)
        actions_bar.addWidget(self.btn_scan)

        self.btn_clean_safe = QPushButton("⚡ 一键安全清理 (Temp与开发缓存)")
        self.btn_clean_safe.setProperty("class", "btn-green")
        self.btn_clean_safe.clicked.connect(self._exec_clean_safe)
        actions_bar.addWidget(self.btn_clean_safe)

        self.btn_vss_opt = QPushButton("🛡️ 限制卷影配额为4GB")
        self.btn_vss_opt.setProperty("class", "btn-amber")
        self.btn_vss_opt.clicked.connect(self._exec_vss_opt)
        actions_bar.addWidget(self.btn_vss_opt)

        self.btn_pagefile_h = QPushButton("🚀 虚拟内存迁移至 H 盘")
        self.btn_pagefile_h.setProperty("class", "btn-purple")
        self.btn_pagefile_h.clicked.connect(lambda: self._exec_migrate_pagefile("H"))
        actions_bar.addWidget(self.btn_pagefile_h)

        self.btn_dism = QPushButton("🧹 DISM 组件深度清理")
        self.btn_dism.setProperty("class", "btn-secondary")
        self.btn_dism.clicked.connect(self._exec_dism)
        actions_bar.addWidget(self.btn_dism)

        actions_bar.addStretch()

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("过滤目标或功能...")
        self.filter_input.setFixedWidth(160)
        self.filter_input.textChanged.connect(self._apply_filter)
        actions_bar.addWidget(self.filter_input)

        main_layout.addLayout(actions_bar)

        # 3. 分割区：核心表格 + 底部日志输出
        splitter = QSplitter(Qt.Vertical)

        # 表格控件
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "分类", "模块与名称", "占用体积", "安全级别", "推荐操作", "功能说明 (为什么会产生占用)", "路径"
        ])
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(34)

        # 允许所有表头列自由拖拽拉伸或收缩，彻底解决文字截断问题
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setCascadingSectionResizes(True)
        header.setHighlightSections(True)

        # 设置适宜的初始列宽分布
        self.table.setColumnWidth(0, 95)    # 分类
        self.table.setColumnWidth(1, 210)   # 模块与名称
        self.table.setColumnWidth(2, 105)   # 占用体积
        self.table.setColumnWidth(3, 130)   # 安全级别
        self.table.setColumnWidth(4, 210)   # 推荐操作
        self.table.setColumnWidth(5, 420)   # 功能说明 (长文本)
        self.table.setColumnWidth(6, 320)   # 路径

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        splitter.addWidget(self.table)

        # 底部控制台与日志框
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(4)
        log_header = QHBoxLayout()
        lbl_log = QLabel("实时系统输出与动作执行日志:")
        lbl_log.setStyleSheet("color: #94a3b8; font-weight: bold;")
        log_header.addWidget(lbl_log)
        log_header.addStretch()

        # 优化清空日志按钮尺寸与样式，杜绝文字截断
        btn_clear_log = QPushButton("🗑️ 清空日志")
        btn_clear_log.setProperty("class", "btn-secondary btn-sm")
        btn_clear_log.setMinimumWidth(88)
        btn_clear_log.setFixedHeight(28)
        btn_clear_log.clicked.connect(lambda: self.log_box.clear())
        log_header.addWidget(btn_clear_log)
        bottom_layout.addLayout(log_header)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        bottom_layout.addWidget(self.log_box)
        splitter.addWidget(bottom_widget)

        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        main_layout.addWidget(splitter)

    def _append_log(self, text: str):
        now = time.strftime("%H:%M:%S")
        self.log_box.append(f"[{now}] {text}")

    def _start_scan(self):
        """触发后台全盘检测"""
        if self.active_worker and self.active_worker.isRunning():
            return
        self.table.setRowCount(0)
        self.scanned_items.clear()
        self.btn_scan.setEnabled(False)

        self.scan_worker = ScanWorker()
        self.scan_worker.item_scanned.connect(self._on_item_scanned)
        self.scan_worker.scan_finished.connect(self._on_scan_finished)
        self.scan_worker.log_message.connect(self._append_log)
        self.active_worker = self.scan_worker
        self.scan_worker.start()

    def _on_item_scanned(self, item: scanner.TargetItem):
        self.scanned_items.append(item)
        row = self.table.rowCount()
        self.table.insertRow(row)

        # 0: 分类
        cat_item = QTableWidgetItem(item.category)
        self.table.setItem(row, 0, cat_item)

        # 1: 模块名称
        name_item = QTableWidgetItem(item.name)
        font = name_item.font()
        font.setBold(True)
        name_item.setFont(font)
        self.table.setItem(row, 1, name_item)

        # 2: 体积大小 (数值排序)
        size_item = NumericTableWidgetItem(item.size_display, item.size_bytes)
        if item.size_bytes > 5 * 1024 * 1024 * 1024:  # > 5GB
            size_item.setForeground(QBrush(QColor("#f87171")))  # 红色告警
        elif item.size_bytes > 1 * 1024 * 1024 * 1024:  # > 1GB
            size_item.setForeground(QBrush(QColor("#fbbf24")))  # 黄色提醒
        else:
            size_item.setForeground(QBrush(QColor("#38bdf8")))
        self.table.setItem(row, 2, size_item)

        # 3: 安全级别 Badge
        safety_text = "🟢 安全可清"
        color = "#34d399"
        if item.safety == "managed":
            safety_text = "🟡 系统托管"
            color = "#fbbf24"
        elif item.safety == "client":
            safety_text = "🔵 客户端内管理"
            color = "#60a5fa"
        badge_item = QTableWidgetItem(safety_text)
        badge_item.setForeground(QBrush(QColor(color)))
        self.table.setItem(row, 3, badge_item)

        # 4: 推荐操作
        action_item = QTableWidgetItem(item.recommend_action)
        self.table.setItem(row, 4, action_item)

        # 5: 功能说明 (为什么产生占用)
        desc_item = QTableWidgetItem(item.desc)
        desc_item.setToolTip(item.desc)
        self.table.setItem(row, 5, desc_item)

        # 6: 路径
        path_item = QTableWidgetItem(item.path)
        path_item.setToolTip(item.path)
        path_item.setForeground(QBrush(QColor("#94a3b8")))
        self.table.setItem(row, 6, path_item)

    def _on_scan_finished(self, metrics: dict):
        self.btn_scan.setEnabled(True)
        self.active_worker = None

        # 更新仪表盘卡片
        c = metrics["c_disk"]
        self.c_value.setText(f"可用: {c['free_gb']:.1f} GB")
        used_pct_c = int((c['used_gb'] / c['total_gb']) * 100) if c['total_gb'] > 0 else 0
        self.c_prog.setValue(used_pct_c)
        self.c_desc.setText(f"总计: {c['total_gb']:.1f} GB | 已用: {c['used_gb']:.1f} GB ({used_pct_c}%)")

        h = metrics["h_disk"]
        self.h_value.setText(f"可用: {h['free_gb']:.1f} GB")
        used_pct_h = int((h['used_gb'] / h['total_gb']) * 100) if h['total_gb'] > 0 else 0
        self.h_prog.setValue(used_pct_h)
        self.h_desc.setText(f"总计: {h['total_gb']:.1f} GB | 已用: {h['used_gb']:.1f} GB ({used_pct_h}%)")

        mem = metrics["memory"]
        self.mem_value.setText(f"RAM可用: {mem['avail_phys_gb']:.1f} GB")
        self.mem_prog.setValue(int(mem["memory_load_pct"]))
        pg = metrics["pagefile"]
        pg_loc = "H:盘托管中" if pg["h_has_pagefile"] else ("C:盘中" if pg["c_has_pagefile"] else "未检测")
        self.mem_desc.setText(f"物理总量: {mem['total_phys_gb']:.1f} GB | 页面文件: {pg_loc}")

        vss = metrics["vss"]
        self.vss_value.setText(f"配额: {vss['max_gb']:.1f} GB")
        vss_pct = int((vss['used_gb'] / vss['max_gb']) * 100) if vss['max_gb'] > 0 else 0
        self.vss_prog.setValue(vss_pct)
        self.vss_desc.setText(f"已用快照: {vss['used_gb']:.2f} GB | 已分配: {vss['allocated_gb']:.2f} GB")

    def _apply_filter(self, text: str):
        search = text.strip().lower()
        for row in range(self.table.rowCount()):
            match = False
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item and search in item.text().lower():
                    match = True
                    break
            self.table.setRowHidden(row, not match)

    def _show_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        path_item = self.table.item(row, 6)
        if not path_item:
            return
        target_path = path_item.text()

        menu = QMenu(self)
        act_open = QAction("📂 在文件资源管理器中定位", self)
        act_open.triggered.connect(lambda: actions.open_in_explorer(target_path))
        menu.addAction(act_open)

        act_copy = QAction("📋 复制路径", self)
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(target_path))
        menu.addAction(act_copy)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    # --- 动作触发 ---
    def _exec_clean_safe(self):
        reply = QMessageBox.question(
            self,
            "安全清理确认",
            "确定执行一键安全清理吗？\n\n将清理用户Temp、系统Temp、Puppeteer/Codex废弃运行时与Chrome缓存。\n该操作对用户文档完全安全零风险。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._run_action("clean_safe")

    def _exec_vss_opt(self):
        reply = QMessageBox.question(
            self,
            "卷影存储配额优化",
            "确定将 C 盘卷影副本配额上限限制为 4GB 吗？\n\nWindows 将自动清理超出配额的过期全盘差异快照（防止突然吃满 15GB）。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._run_action("optimize_vss", max_size="4GB")

    def _exec_migrate_pagefile(self, target_drive: str):
        reply = QMessageBox.question(
            self,
            "虚拟内存位置迁移",
            f"确定将系统虚拟内存 (Pagefile) 转移至 {target_drive}: 盘吗？\n\n"
            f"注: C 盘与 H 盘同属一个 256GB SSD，性能完全一致。\n设置后需要重启一次电脑以彻底释放 C 盘旧文件占用的空间。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._run_action("migrate_pagefile", drive=target_drive)

    def _exec_dism(self):
        reply = QMessageBox.question(
            self,
            "DISM 系统更新组件清理",
            "确定执行 DISM 组件存储深度清理吗？\n\n将清理被新安全补丁替代的历史更新包。过程需要约 1~3 分钟。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._run_action("dism_cleanup")

    def _run_action(self, action_name: str, **kwargs):
        if self.active_worker and self.active_worker.isRunning():
            QMessageBox.warning(self, "忙碌中", "当前有正在运行的任务，请稍候...")
            return

        self._append_log(f"【触发操作】: {action_name}...")
        self.worker = ActionWorker(action_name, **kwargs)
        self.worker.action_log.connect(self._append_log)
        self.worker.action_done.connect(self._on_action_done)
        self.active_worker = self.worker
        self.worker.start()

    def _on_action_done(self, summary: str, success: bool):
        self.active_worker = None
        self._append_log(f"【操作结果】: {summary} (状态: {'成功' if success else '失败'})")
        QMessageBox.information(self, "执行完成", summary)
        # 动作完成后自动触发一次重新扫描刷新数据
        self._start_scan()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
