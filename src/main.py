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
    QLineEdit, QAbstractItemView, QMenu, QTabWidget, QCheckBox
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
        fixed_drives = scanner.get_fixed_drives()
        non_c_drives = [d for d in fixed_drives if d["letter"] != "C"]
        preferred_drive = non_c_drives[0]["letter"] if non_c_drives else "C"

        mem_stat = scanner.get_memory_metrics()
        page_stat = scanner.get_pagefile_config()
        vss_stat = scanner.get_vss_storage_info()
        hiber_stat = scanner.get_hibernation_info()
        driver_stat = scanner.get_driver_store_info()
        wsl_disks = scanner.find_wsl2_vdisks()
        update_stat = scanner.get_update_download_cache_info()
        privacy_stat = scanner.get_privacy_traces_metrics()

        sys_metrics = {
            "c_disk": c_stat,
            "fixed_drives": fixed_drives,
            "preferred_drive": preferred_drive,
            "memory": mem_stat,
            "pagefile": page_stat,
            "vss": vss_stat,
            "hibernation": hiber_stat,
            "driver_store": driver_stat,
            "wsl_disks": wsl_disks,
            "update_cache": update_stat,
            "privacy": privacy_stat
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

        elif self.action_name == "manage_hibernation":
            mode = self.kwargs.get("mode", "reduced")
            success = actions.manage_hibernation(mode, log_cb)
            self.action_done.emit(f"休眠模式调整为 {mode} 执行完毕", success)

        elif self.action_name == "clean_driver_store":
            cnt = actions.clean_driver_store(log_cb)
            self.action_done.emit(f"DriverStore 旧驱动精简完毕，共清理 {cnt} 个包", True)

        elif self.action_name == "compact_wsl2":
            vhdx = self.kwargs.get("vhdx_path", "")
            success = actions.compact_wsl2_vdisk(vhdx, log_cb)
            self.action_done.emit("WSL2 虚拟磁盘压缩完毕", success)

        elif self.action_name == "clean_update_cache":
            freed = actions.clean_update_cache(log_cb)
            self.action_done.emit(f"Windows Update 缓存清理完毕，释放 {freed/(1024*1024):.2f} MB", True)

        elif self.action_name == "batch_hardcore":
            tasks = self.kwargs.get("tasks", {})
            log_cb("【批处理】开始执行选中的系统硬核瘦身任务...")
            if tasks.get("clean_safe"):
                actions.clean_safe_paths(log_cb)
            if tasks.get("hibernation"):
                mode = tasks.get("hibernation_mode", "reduced")
                actions.manage_hibernation(mode, log_cb)
            if tasks.get("driver_store"):
                actions.clean_driver_store(log_cb)
            if tasks.get("update_cache"):
                actions.clean_update_cache(log_cb)
            if tasks.get("wsl2_paths"):
                for p in tasks["wsl2_paths"]:
                    actions.compact_wsl2_vdisk(p, log_cb)
            if tasks.get("vss"):
                actions.optimize_vss("4GB", log_cb)
            self.action_done.emit("选中的硬核瘦身任务已全量执行完成！", True)

        elif self.action_name == "batch_privacy":
            items = self.kwargs.get("items", {})
            log_cb("【批处理】开始执行选中的反取证与隐私去痕任务...")
            if items.get("run_mru"):
                actions.clean_run_mru(log_cb)
            if items.get("recent"):
                actions.clean_recent_and_jumplists(log_cb)
            if items.get("prefetch"):
                actions.clean_prefetch(log_cb)
            if items.get("thumbcache"):
                actions.clean_thumbcache(log_cb)
            if items.get("dns"):
                actions.flush_dns_cache(log_cb)
            self.action_done.emit("选中的隐私与运行去痕任务已全量执行完成！", True)


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
            QLineEdit {
                background-color: #1a1c23;
                border: 1px solid #333a4d;
                border-radius: 6px;
                padding: 5px 10px;
                color: #f1f5f9;
            }
            QComboBox {
                background-color: #1a1c23;
                border: 1px solid #333a4d;
                border-radius: 6px;
                padding: 5px 28px 5px 10px;
                color: #f1f5f9;
                min-height: 20px;
            }
            QComboBox:hover {
                border-color: #4b5563;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border-left-width: 0px;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1c23;
                border: 1px solid #3b4252;
                border-radius: 6px;
                padding: 4px;
                selection-background-color: #2563eb;
                selection-color: #ffffff;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                min-height: 28px;
                padding: 4px 8px;
                border-radius: 4px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #2d3748;
            }
            QTextEdit {
                background-color: #0d0e12;
                border: 1px solid #232734;
                border-radius: 6px;
                color: #a5f3fc;
                font-family: "Consolas", "Courier New", monospace;
                font-size: 12px;
            }
            QTabWidget::pane {
                border: 1px solid #2d323f;
                border-radius: 8px;
                background-color: #14161d;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #1a1c24;
                color: #94a3b8;
                padding: 8px 18px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                border: 1px solid #2d323f;
                border-bottom: none;
                margin-right: 4px;
                font-weight: 600;
                font-size: 13px;
            }
            QTabBar::tab:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
            QTabBar::tab:hover:!selected {
                background-color: #262c3a;
                color: #f1f5f9;
            }
            QCheckBox {
                color: #e2e8f0;
                spacing: 8px;
                font-size: 13px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #475569;
                border-radius: 4px;
                background-color: #1e222d;
            }
            QCheckBox::indicator:hover {
                border-color: #38bdf8;
            }
            QCheckBox::indicator:checked {
                background-color: #2563eb;
                border-color: #60a5fa;
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

        # 卡片 2: 辅助数据盘状态 (自适应探测)
        self.card_h = QFrame()
        self.card_h.setProperty("class", "card")
        h_box = QVBoxLayout(self.card_h)
        self.h_title = QLabel("辅助数据盘空间 (推荐分区)")
        self.h_title.setProperty("class", "card-title")
        self.h_value = QLabel("可用: -- GB")
        self.h_value.setProperty("class", "card-value")
        self.h_prog = QProgressBar()
        self.h_prog.setRange(0, 100)
        self.h_desc = QLabel("托管 Pagefile 零性能损耗")
        self.h_desc.setProperty("class", "card-desc")
        h_box.addWidget(self.h_title)
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

        # 动态驱动器选择与虚拟内存迁移
        self.combo_pagefile_drive = QComboBox()
        self.combo_pagefile_drive.setToolTip("选择承载虚拟内存的目标本地固定硬盘分区")
        self.combo_pagefile_drive.setMinimumWidth(190)
        self.combo_pagefile_drive.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.combo_pagefile_drive.view().setMinimumWidth(220)
        actions_bar.addWidget(self.combo_pagefile_drive)

        self.btn_pagefile_h = QPushButton("🚀 迁移虚拟内存至选定盘")
        self.btn_pagefile_h.setProperty("class", "btn-purple")
        self.btn_pagefile_h.clicked.connect(self._on_migrate_pagefile_clicked)
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

        # 3. 核心区域升级为 QTabWidget 三标签页控制台
        self.tabs = QTabWidget()

        # ---------- TAB 1: 存储深度排查 ----------
        tab1_widget = QWidget()
        tab1_layout = QVBoxLayout(tab1_widget)
        tab1_layout.setContentsMargins(6, 10, 6, 6)
        tab1_layout.setSpacing(8)
        tab1_layout.addLayout(actions_bar)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "分类", "模块与名称", "占用体积", "安全级别", "推荐操作", "功能说明 (为什么会产生占用)", "路径"
        ])
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(34)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setCascadingSectionResizes(True)
        header.setHighlightSections(True)

        self.table.setColumnWidth(0, 95)    # 分类
        self.table.setColumnWidth(1, 210)   # 模块与名称
        self.table.setColumnWidth(2, 105)   # 占用体积
        self.table.setColumnWidth(3, 130)   # 安全级别
        self.table.setColumnWidth(4, 210)   # 推荐操作
        self.table.setColumnWidth(5, 420)   # 功能说明
        self.table.setColumnWidth(6, 320)   # 路径

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        tab1_layout.addWidget(self.table)
        self.tabs.addTab(tab1_widget, "📊 存储深度排查")

        # ---------- TAB 2: 系统硬核瘦身 ----------
        tab2_widget = QWidget()
        tab2_layout = QVBoxLayout(tab2_widget)
        tab2_layout.setContentsMargins(6, 10, 6, 6)
        tab2_layout.setSpacing(8)

        hardcore_bar = QHBoxLayout()
        hardcore_bar.setSpacing(8)
        self.btn_hardcore_rec = QPushButton("✨ 智能推荐预设")
        self.btn_hardcore_rec.setProperty("class", "btn-secondary btn-sm")
        self.btn_hardcore_rec.clicked.connect(self._select_hardcore_recommended)
        hardcore_bar.addWidget(self.btn_hardcore_rec)

        self.btn_hardcore_all = QPushButton("☑️ 全选")
        self.btn_hardcore_all.setProperty("class", "btn-secondary btn-sm")
        self.btn_hardcore_all.clicked.connect(lambda: self._set_all_checkboxes(self.table_hardcore, True))
        hardcore_bar.addWidget(self.btn_hardcore_all)

        self.btn_hardcore_none = QPushButton("⬜ 取消全选")
        self.btn_hardcore_none.setProperty("class", "btn-secondary btn-sm")
        self.btn_hardcore_none.clicked.connect(lambda: self._set_all_checkboxes(self.table_hardcore, False))
        hardcore_bar.addWidget(self.btn_hardcore_none)

        hardcore_bar.addStretch()

        self.btn_hardcore_exec = QPushButton("⚡ 立即执行选中的硬核瘦身")
        self.btn_hardcore_exec.setProperty("class", "btn-green")
        self.btn_hardcore_exec.clicked.connect(self._exec_batch_hardcore)
        hardcore_bar.addWidget(self.btn_hardcore_exec)
        tab2_layout.addLayout(hardcore_bar)

        self.table_hardcore = QTableWidget()
        self.table_hardcore.setColumnCount(6)
        self.table_hardcore.setHorizontalHeaderLabels([
            "选择", "功能模块", "当前占用 / 状态", "优化策略配置", "安全级别", "底层技术原理说明"
        ])
        self.table_hardcore.setAlternatingRowColors(True)
        self.table_hardcore.verticalHeader().setDefaultSectionSize(36)
        h_header = self.table_hardcore.horizontalHeader()
        h_header.setSectionResizeMode(QHeaderView.Interactive)
        h_header.setStretchLastSection(True)
        self.table_hardcore.setColumnWidth(0, 60)
        self.table_hardcore.setColumnWidth(1, 230)
        self.table_hardcore.setColumnWidth(2, 190)
        self.table_hardcore.setColumnWidth(3, 280)
        self.table_hardcore.setColumnWidth(4, 130)
        self.table_hardcore.setColumnWidth(5, 420)
        tab2_layout.addWidget(self.table_hardcore)
        self.tabs.addTab(tab2_widget, "⚡ 系统硬核瘦身")

        # ---------- TAB 3: 隐私与运行去痕 ----------
        tab3_widget = QWidget()
        tab3_layout = QVBoxLayout(tab3_widget)
        tab3_layout.setContentsMargins(6, 10, 6, 6)
        tab3_layout.setSpacing(8)

        privacy_bar = QHBoxLayout()
        privacy_bar.setSpacing(8)
        self.btn_privacy_rec = QPushButton("✨ 推荐去痕预设")
        self.btn_privacy_rec.setProperty("class", "btn-secondary btn-sm")
        self.btn_privacy_rec.clicked.connect(self._select_privacy_recommended)
        privacy_bar.addWidget(self.btn_privacy_rec)

        self.btn_privacy_all = QPushButton("☑️ 全选")
        self.btn_privacy_all.setProperty("class", "btn-secondary btn-sm")
        self.btn_privacy_all.clicked.connect(lambda: self._set_all_checkboxes(self.table_privacy, True))
        privacy_bar.addWidget(self.btn_privacy_all)

        self.btn_privacy_none = QPushButton("⬜ 取消全选")
        self.btn_privacy_none.setProperty("class", "btn-secondary btn-sm")
        self.btn_privacy_none.clicked.connect(lambda: self._set_all_checkboxes(self.table_privacy, False))
        privacy_bar.addWidget(self.btn_privacy_none)

        privacy_bar.addStretch()

        self.btn_privacy_exec = QPushButton("🛡️ 立即执行反取证去痕")
        self.btn_privacy_exec.setProperty("class", "btn-amber")
        self.btn_privacy_exec.clicked.connect(self._exec_batch_privacy)
        privacy_bar.addWidget(self.btn_privacy_exec)
        tab3_layout.addLayout(privacy_bar)

        self.table_privacy = QTableWidget()
        self.table_privacy.setColumnCount(6)
        self.table_privacy.setHorizontalHeaderLabels([
            "选择", "去痕项目", "痕迹数量 / 预估占用", "推荐优化动作", "敏感级别", "反取证与隐私防护价值"
        ])
        self.table_privacy.setAlternatingRowColors(True)
        self.table_privacy.verticalHeader().setDefaultSectionSize(36)
        p_header = self.table_privacy.horizontalHeader()
        p_header.setSectionResizeMode(QHeaderView.Interactive)
        p_header.setStretchLastSection(True)
        self.table_privacy.setColumnWidth(0, 60)
        self.table_privacy.setColumnWidth(1, 230)
        self.table_privacy.setColumnWidth(2, 190)
        self.table_privacy.setColumnWidth(3, 210)
        self.table_privacy.setColumnWidth(4, 130)
        self.table_privacy.setColumnWidth(5, 420)
        tab3_layout.addWidget(self.table_privacy)
        self.tabs.addTab(tab3_widget, "🕵️ 隐私与运行去痕")

        # 4. 分割区：TabWidget + 底部日志输出
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.tabs)

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

        # 自适应选择卡片 2 展示的辅助盘
        pg = metrics["pagefile"]
        fixed_drives = metrics.get("fixed_drives", [])
        
        # 寻找当前托管盘或推荐盘
        active_pg_drives = pg.get("active_drives", [])
        active_secondary = next((d for d in active_pg_drives if d != "C"), None)
        display_drive = active_secondary or metrics.get("preferred_drive", "H")
        
        # 刷新驱动器下拉选择器 (保持用户当前所选或优先默认项)
        prev_choice = self.combo_pagefile_drive.currentData()
        self.combo_pagefile_drive.blockSignals(True)
        self.combo_pagefile_drive.clear()
        for fd in fixed_drives:
            letter = fd["letter"]
            item_text = f"{letter}: 盘 (可用 {fd['free_gb']:.1f} GB)"
            self.combo_pagefile_drive.addItem(item_text, letter)
        self.combo_pagefile_drive.view().setMinimumWidth(220)
        
        # 选中推荐项或原有项
        select_letter = prev_choice or display_drive
        for i in range(self.combo_pagefile_drive.count()):
            if self.combo_pagefile_drive.itemData(i) == select_letter:
                self.combo_pagefile_drive.setCurrentIndex(i)
                break
        self.combo_pagefile_drive.blockSignals(False)

        # 刷新卡片 2 的指标
        target_disk = next((d for d in fixed_drives if d["letter"] == display_drive), None)
        if target_disk:
            self.h_title.setText(f"{display_drive}: 盘空间 ({'当前托管' if display_drive == active_secondary else '推荐辅助分区'})")
            self.h_value.setText(f"可用: {target_disk['free_gb']:.1f} GB")
            used_pct_target = int((target_disk['used_gb'] / target_disk['total_gb']) * 100) if target_disk['total_gb'] > 0 else 0
            self.h_prog.setValue(used_pct_target)
            self.h_desc.setText(f"总计: {target_disk['total_gb']:.1f} GB | 已用: {target_disk['used_gb']:.1f} GB ({used_pct_target}%)")
        else:
            self.h_title.setText("辅助数据分区")
            self.h_value.setText("无附加固定分区")
            self.h_prog.setValue(0)
            self.h_desc.setText("建议单盘自动管理")

        mem = metrics["memory"]
        self.mem_value.setText(f"RAM可用: {mem['avail_phys_gb']:.1f} GB")
        self.mem_prog.setValue(int(mem["memory_load_pct"]))
        pg_loc = f"{active_secondary}:盘托管中" if active_secondary else ("C:盘中" if pg["c_has_pagefile"] else "全局自动")
        self.mem_desc.setText(f"物理总量: {mem['total_phys_gb']:.1f} GB | 页面文件: {pg_loc}")

        vss = metrics["vss"]
        self.vss_value.setText(f"配额: {vss['max_gb']:.1f} GB")
        vss_pct = int((vss['used_gb'] / vss['max_gb']) * 100) if vss['max_gb'] > 0 else 0
        self.vss_prog.setValue(vss_pct)
        self.vss_desc.setText(f"已用快照: {vss['used_gb']:.2f} GB | 已分配: {vss['allocated_gb']:.2f} GB")

        # 刷新 Tab 2: 系统硬核瘦身列表
        self._refresh_hardcore_table(metrics)

        # 刷新 Tab 3: 隐私与运行去痕列表
        self._refresh_privacy_table(metrics)

    def _add_hardcore_row(self, key: str, name: str, status: str, default_check: bool, combo_options: List[str], safety: str, desc: str, extra_data: Any = None):
        row = self.table_hardcore.rowCount()
        self.table_hardcore.insertRow(row)

        chk = QCheckBox()
        chk.setChecked(default_check)
        chk.setProperty("item_key", key)
        chk.setProperty("extra_data", extra_data)
        chk_widget = QWidget()
        chk_layout = QHBoxLayout(chk_widget)
        chk_layout.addWidget(chk)
        chk_layout.setAlignment(Qt.AlignCenter)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        self.table_hardcore.setCellWidget(row, 0, chk_widget)

        name_item = QTableWidgetItem(name)
        font = name_item.font()
        font.setBold(True)
        name_item.setFont(font)
        self.table_hardcore.setItem(row, 1, name_item)

        status_item = QTableWidgetItem(status)
        status_item.setForeground(QBrush(QColor("#38bdf8")))
        self.table_hardcore.setItem(row, 2, status_item)

        if len(combo_options) > 1:
            combo = QComboBox()
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            combo.view().setMinimumWidth(320)
            for opt in combo_options:
                combo.addItem(opt)
            self.table_hardcore.setCellWidget(row, 3, combo)
        else:
            act_item = QTableWidgetItem(combo_options[0] if combo_options else "")
            self.table_hardcore.setItem(row, 3, act_item)

        safe_item = QTableWidgetItem(safety)
        safe_item.setForeground(QBrush(QColor("#34d399" if "安全" in safety or "官方" in safety or "无损" in safety else "#fbbf24")))
        self.table_hardcore.setItem(row, 4, safe_item)

        desc_item = QTableWidgetItem(desc)
        desc_item.setToolTip(desc)
        self.table_hardcore.setItem(row, 5, desc_item)

    def _refresh_hardcore_table(self, metrics: dict):
        self.table_hardcore.setRowCount(0)
        
        # 1. 休眠文件
        hiber = metrics.get("hibernation", {})
        h_sz = f"{hiber.get('size_gb', 0):.2f} GB" if hiber.get("file_exists") else "未生成 / 0 B"
        h_mode = hiber.get("mode", "off")
        mode_desc = "完整休眠模式 (75% RAM)" if h_mode == "full" else ("精简休眠模式 (减半50%)" if h_mode == "reduced" else "已彻底关闭")
        self._add_hardcore_row(
            key="hibernation",
            name="Windows 休眠文件 (hiberfil.sys)",
            status=f"当前占用: {h_sz} | {mode_desc}",
            default_check=True if h_mode == "full" else False,
            combo_options=["精简休眠模式 (Reduced, 减半并保留快速启动)", "彻底关闭休眠 (Off, 释放100%空间)"],
            safety="🟢 安全推荐",
            desc="优化内核休眠映射镜像，精简模式既可省数十GB又保留开机秒开"
        )

        # 2. DriverStore 驱动存储库
        driver = metrics.get("driver_store", {})
        d_sz = f"{driver.get('total_size_gb', 0):.2f} GB"
        d_cnt = driver.get("total_drivers", 0)
        d_old = driver.get("superseded_drivers", 0)
        self._add_hardcore_row(
            key="driver_store",
            name="DriverStore 脱机驱动备份存储库",
            status=f"约 {d_sz} | 共 {d_cnt} 个第三方驱动包 (含约 {d_old} 个历史备份)",
            default_check=True if d_old > 0 else False,
            combo_options=["清理非活动的脱机版本驱动 (安全卸载)"],
            safety="🟢 官方机制",
            desc="调用 pnputil 安全裁减已被新驱动取代的历史备份包，绝不触碰正在工作的活动驱动"
        )

        # 3. WSL2 / Docker 虚拟硬盘
        wsl_disks = metrics.get("wsl_disks", [])
        if wsl_disks:
            for idx, wd in enumerate(wsl_disks):
                self._add_hardcore_row(
                    key=f"wsl2_{idx}",
                    name=f"WSL2/Docker 虚拟磁盘 ({wd['distro']})",
                    status=f"物理占用: {wd['size_gb']:.2f} GB ({wd['name']})",
                    default_check=True,
                    combo_options=["diskpart compact 物理回缩空闲扇区"],
                    safety="🟢 无损压缩",
                    desc=f"释放 Linux 内已删除但 Windows 未收回的扇区: {wd['path']}",
                    extra_data=wd['path']
                )
        else:
            self._add_hardcore_row(
                key="wsl2_none",
                name="WSL2 / Docker 虚拟磁盘 (ext4.vhdx)",
                status="未检测到活跃的 WSL2/Docker 磁盘",
                default_check=False,
                combo_options=["无需优化"],
                safety="🟢 无需动作",
                desc="当安装使用 WSL2 或 Docker Desktop 且出现膨胀时可在此一键紧缩"
            )

        # 4. Windows Update 下载缓存
        update_info = metrics.get("update_cache", {})
        u_sz = f"{update_info.get('size_gb', 0)*1024:.1f} MB"
        u_cnt = update_info.get("file_count", 0)
        self._add_hardcore_row(
            key="update_cache",
            name="Windows Update 交付优化与下载缓存",
            status=f"占用 {u_sz} | 包含 {u_cnt} 个临时更新包",
            default_check=True if update_info.get('size_gb', 0) > 0.05 else False,
            combo_options=["安全挂起服务并清空下载缓存"],
            safety="🟢 安全可清",
            desc="临时暂停 wuauserv 服务并清空 SoftwareDistribution\\Download 历史包后自动恢复"
        )

        # 5. VSS 卷影快照
        vss = metrics.get("vss", {})
        self._add_hardcore_row(
            key="vss",
            name="系统还原点卷影存储上限 (VSS)",
            status=f"当前配额: {vss.get('max_gb', 0):.1f} GB | 已用快照: {vss.get('used_gb', 0):.2f} GB",
            default_check=True if vss.get('max_gb', 0) > 4.5 else False,
            combo_options=["锁定配额为 4GB (自动裁剪陈旧快照)"],
            safety="🟡 系统托管",
            desc="防止 Windows Update 和系统保护在后台频繁创建差异快照吃满 15GB"
        )

    def _add_privacy_row(self, key: str, name: str, status: str, action: str, safety: str, desc: str):
        row = self.table_privacy.rowCount()
        self.table_privacy.insertRow(row)

        chk = QCheckBox()
        chk.setChecked(True)
        chk.setProperty("item_key", key)
        chk_widget = QWidget()
        chk_layout = QHBoxLayout(chk_widget)
        chk_layout.addWidget(chk)
        chk_layout.setAlignment(Qt.AlignCenter)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        self.table_privacy.setCellWidget(row, 0, chk_widget)

        name_item = QTableWidgetItem(name)
        font = name_item.font()
        font.setBold(True)
        name_item.setFont(font)
        self.table_privacy.setItem(row, 1, name_item)

        status_item = QTableWidgetItem(status)
        status_item.setForeground(QBrush(QColor("#fcd34d")))
        self.table_privacy.setItem(row, 2, status_item)

        act_item = QTableWidgetItem(action)
        self.table_privacy.setItem(row, 3, act_item)

        safe_item = QTableWidgetItem(safety)
        safe_item.setForeground(QBrush(QColor("#34d399" if "零风险" in safety or "安全" in safety else "#fbbf24")))
        self.table_privacy.setItem(row, 4, safe_item)

        desc_item = QTableWidgetItem(desc)
        desc_item.setToolTip(desc)
        self.table_privacy.setItem(row, 5, desc_item)

    def _refresh_privacy_table(self, metrics: dict):
        self.table_privacy.setRowCount(0)
        priv = metrics.get("privacy", {})

        self._add_privacy_row(
            key="run_mru",
            name="Win+R 运行窗口历史记录 (RunMRU)",
            status=f"{priv.get('run_mru_count', 0)} 条输入记录",
            action="清空 RunMRU 注册表键值",
            safety="🟢 零风险",
            desc="彻底擦除在 Win+R 运行窗口敲入的历史可执行程序名与路径"
        )

        rec_cnt = priv.get("recent_files_count", 0)
        jump_cnt = priv.get("jumplist_count", 0)
        self._add_privacy_row(
            key="recent",
            name="最近访问文档与任务栏跳转列表 (Recent / JumpLists)",
            status=f"{rec_cnt} 个快捷方式 | {jump_cnt} 个任务栏条目",
            action="销毁 Recent 与 AutomaticDestinations 并刷新 Shell",
            safety="🟢 零风险",
            desc="消除文件资源管理器“快速访问”中留存的私密文档记录和任务栏右键历史"
        )

        pf_cnt = priv.get("prefetch_count", 0)
        self._add_privacy_row(
            key="prefetch",
            name="应用程序执行预取痕迹 (Prefetch)",
            status=f"{pf_cnt} 个运行预取文件 (*.pf)",
            action="清空 C:\\Windows\\Prefetch 启动记录",
            safety="🟢 安全清除",
            desc="阻断反取证分析重构本机器各软件的启动历史与执行时间线"
        )

        tc_mb = priv.get("thumbcache_size_mb", 0)
        self._add_privacy_row(
            key="thumbcache",
            name="资源管理器缩略图数据库 (Thumbcache)",
            status=f"已缓存 {tc_mb:.1f} MB 数据库",
            action="平滑重启 Explorer 并粉碎 thumbcache_*.db",
            safety="🟡 需重载资源管理器",
            desc="彻底销毁已删除图片或隐私照片在系统底层留存的微缩图快照库"
        )

        self._add_privacy_row(
            key="dns",
            name="Windows 本地 DNS 解析缓存",
            status="当前网络会话活跃解析缓存",
            action="执行 ipconfig /flushdns",
            safety="🟢 零风险",
            desc="刷新并抹除本机已解析访问过的所有公网与内网域名记录"
        )

    def _set_all_checkboxes(self, table: QTableWidget, checked: bool):
        for row in range(table.rowCount()):
            w = table.cellWidget(row, 0)
            if w:
                chk = w.findChild(QCheckBox)
                if chk:
                    chk.setChecked(checked)

    def _select_hardcore_recommended(self):
        for row in range(self.table_hardcore.rowCount()):
            w = self.table_hardcore.cellWidget(row, 0)
            if w:
                chk = w.findChild(QCheckBox)
                if chk:
                    k = chk.property("item_key")
                    chk.setChecked(k in ["hibernation", "driver_store", "update_cache", "vss"])

    def _select_privacy_recommended(self):
        self._set_all_checkboxes(self.table_privacy, True)

    def _exec_batch_hardcore(self):
        selected_tasks = {}
        wsl2_paths = []
        
        for row in range(self.table_hardcore.rowCount()):
            w = self.table_hardcore.cellWidget(row, 0)
            if not w:
                continue
            chk = w.findChild(QCheckBox)
            if not chk or not chk.isChecked():
                continue
            k = chk.property("item_key")
            if k == "hibernation":
                selected_tasks["hibernation"] = True
                combo = self.table_hardcore.cellWidget(row, 3)
                if combo and isinstance(combo, QComboBox):
                    selected_tasks["hibernation_mode"] = "off" if "彻底关闭" in combo.currentText() else "reduced"
                else:
                    selected_tasks["hibernation_mode"] = "reduced"
            elif k == "driver_store":
                selected_tasks["driver_store"] = True
            elif k.startswith("wsl2_") and k != "wsl2_none":
                p = chk.property("extra_data")
                if p:
                    wsl2_paths.append(p)
            elif k == "update_cache":
                selected_tasks["update_cache"] = True
            elif k == "vss":
                selected_tasks["vss"] = True

        if wsl2_paths:
            selected_tasks["wsl2_paths"] = wsl2_paths

        if not selected_tasks:
            QMessageBox.warning(self, "未勾选任务", "请至少勾选一个系统硬核瘦身项目！")
            return

        reply = QMessageBox.question(
            self,
            "硬核瘦身确认",
            f"确定执行选中的 {len(selected_tasks)} 项系统硬核瘦身任务吗？\n\n将按高可靠标准批量调用底层系统工具处理。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._run_action("batch_hardcore", tasks=selected_tasks)

    def _exec_batch_privacy(self):
        items = {}
        for row in range(self.table_privacy.rowCount()):
            w = self.table_privacy.cellWidget(row, 0)
            if not w:
                continue
            chk = w.findChild(QCheckBox)
            if not chk or not chk.isChecked():
                continue
            k = chk.property("item_key")
            items[k] = True

        if not items:
            QMessageBox.warning(self, "未勾选项目", "请至少勾选一个反取证去痕项目！")
            return

        has_thumb = items.get("thumbcache", False)
        msg = f"确定执行选中的 {len(items)} 项隐私去痕任务吗？"
        if has_thumb:
            msg += "\n\n⚠️ 注: 包含【缩略图数据库粉碎】，将平滑重启 Windows 资源管理器进程约 1 秒，属于正常现象。"

        reply = QMessageBox.question(
            self,
            "隐私去痕确认",
            msg,
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._run_action("batch_privacy", items=items)

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

    def _on_migrate_pagefile_clicked(self):
        target_drive = self.combo_pagefile_drive.currentData()
        if not target_drive:
            QMessageBox.warning(self, "未选择分区", "请先在下拉列表中选择一个本地固定硬盘分区！")
            return
        self._exec_migrate_pagefile(target_drive)

    def _exec_migrate_pagefile(self, target_drive: str):
        reply = QMessageBox.question(
            self,
            "虚拟内存位置迁移",
            f"确定将系统虚拟内存 (Pagefile) 转移至 {target_drive}: 盘吗？\n\n"
            f"• 系统将自动在该盘建立系统托管分页文件 (0 0)\n"
            f"• 设置后旧的 C:\\pagefile.sys 将被标记为下次重启自动物理删除\n"
            f"• 请在设置完成后重启一次电脑以彻底释放 C 盘空间。",
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
