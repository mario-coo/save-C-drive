# C盘空间深度检测与优化大师 v2.0 实施规划方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 C 盘大师全面升级为 v2.0 三 Tab 现代化控制台，新增“系统硬核瘦身 (休眠双模态/驱动库裁减/WSL2压实/更新下载缓存)”与“Windows 隐私与运行去痕 (RunMRU/Recent/JumpList/Prefetch/Thumbcache/DNS)”两大全新工程模块。

**Architecture:** 采用高内聚低耦合分层架构。底层扩展 `scanner.py` 实现硬件与注册表级特征探测；业务层扩展 `actions.py` 实现基于 Windows 原生接口（PowerShell、pnputil、diskpart、Win32 Shell API）的安全动作；展现层将 `main.py` 重构为基于 `QTabWidget` 的现代化深色控制台，保持顶部健康指标卡片全局共享，并引入细粒度复选清单（CheckBox List）与智能预设。

**Tech Stack:** Python 3.10+, PySide6, Windows API (ctypes / winreg), pnputil, powercfg, diskpart.

---

## User Review Required

> [!IMPORTANT]
> 1. **缩略图数据库 (Thumbcache) 清理**：由于缩略图数据库常驻被 `explorer.exe` 锁定，清理时会优雅重启 Explorer 进程，桌面与任务栏会有约 1 秒钟的自动重载。
> 2. **休眠模式选择**：默认推荐“精简休眠模式（Reduced）”，可砍掉 50% 体积同时保留快速启动（Fast Startup）；若用户明确选择“彻底关闭”，将 100% 清除 `hiberfil.sys`。
> 3. **驱动存储库清理**：调用 `pnputil /delete-driver` 仅清除已被新版本替代且脱机的历史备份，绝不触碰活动驱动。

---

## Proposed Changes

### Core Engine & Architecture

#### [MODIFY] [scanner.py](file:///h:/work/cc/save-C-drive/src/scanner.py)
- 新增 `get_hibernation_info()`：探测休眠开启状态、模式（Full / Reduced）及 `C:\hiberfil.sys` 真实体积；
- 新增 `get_driver_store_info()`：调用 `pnputil /enum-drivers` 统计驱动包总数、被取代旧驱动数量及 `FileRepository` 占用估算；
- 新增 `find_wsl2_vdisks()`：自动扫描 `%LOCALAPPDATA%\Packages` 与 Docker 目录下的所有 `ext4.vhdx` 文件及其物理大小；
- 新增 `get_update_download_cache_info()`：统计 `SoftwareDistribution\Download` 目录大小；
- 新增 `get_privacy_traces_metrics()`：扫描 RunMRU 条数、Recent 快捷方式数、JumpList 数据库、Prefetch 文件数、Thumbcache 文件大小。

#### [MODIFY] [actions.py](file:///h:/work/cc/save-C-drive/src/actions.py)
- 新增 `manage_hibernation(mode: str, log_cb)`：执行 `powercfg /h /type reduced` 或 `powercfg /h off`；
- 新增 `clean_driver_store(log_cb)`：筛选非活动旧驱动并调用 `pnputil /delete-driver` 安全卸载；
- 新增 `compact_wsl2_vdisk(vhdx_path: str, log_cb)`：执行 `wsl --shutdown` 并以只读挂载执行 diskpart compact；
- 新增 `clean_update_cache(log_cb)`：挂起 `wuauserv` 服务，清空下载临时包并自动恢复服务；
- 新增 `clean_privacy_traces(options: dict, log_cb)`：支持细粒度擦除 RunMRU、Recent、JumpList、Prefetch、平滑重启 Explorer 销毁 Thumbcache、刷新 DNS 缓存。

#### [MODIFY] [main.py](file:///h:/work/cc/save-C-drive/src/main.py)
- 重构主界面：顶部保持全局仪表盘卡片（C盘、辅助盘、RAM/Pagefile、VSS），下方引入 `QTabWidget`；
- **Tab 1: 存储深度排查**：保留现有大文件表、动态虚拟内存迁移、VSS 4GB、DISM；
- **Tab 2: 系统硬核瘦身**：
  - 卡片式表格复选清单：休眠文件治理、旧驱动精简、WSL2虚拟磁盘压实、更新下载缓存；
  - 按钮组：“智能推荐选中”、“全选/反选”、“⚡ 立即执行选中的硬核瘦身”；
- **Tab 3: 隐私与运行去痕**：
  - 细粒度复选表：运行历史、最近文件、任务栏跳转列表、预取文件、缩略图数据库、DNS 缓存；
  - 带有安全评级标签（🟢 安全、🟡 涉及服务/进程重启）；
  - 按钮组：“推荐去痕项”、“🕵️ 立即执行反取证清理”。

---

## Verification Plan

### Automated Tests
1. 创建 `tests/test_v2_features.py`：
   - 验证 `scanner.get_hibernation_info` 结构及健壮性；
   - 验证 `scanner.find_wsl2_vdisks` 探测逻辑；
   - 验证 `scanner.get_privacy_traces_metrics` 统计各痕迹指标；
   - 运行命令：`python -m unittest tests/test_v2_features.py`

### Build & Release Verification
1. 运行 `python build.py` 重新生成单文件可执行程序 `release/DiskCleanPro.exe`；
2. 验证 EXE 打包成功，大小约为 45MB，退出码 0。
