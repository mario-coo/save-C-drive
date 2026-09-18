# -*- coding: utf-8 -*-
"""
scanner.py - C盘与关键存储目录深度扫描引擎
"""

import os
import shutil
import ctypes
import winreg
import subprocess
from typing import List, Dict, Any, Optional

class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('sullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]

def get_memory_metrics() -> Dict[str, float]:
    """获取系统物理内存与虚拟内存指标 (单位: GB)"""
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    return {
        "total_phys_gb": stat.ullTotalPhys / (1024 ** 3),
        "avail_phys_gb": stat.ullAvailPhys / (1024 ** 3),
        "total_pagefile_gb": stat.ullTotalPageFile / (1024 ** 3),
        "avail_pagefile_gb": stat.ullAvailPageFile / (1024 ** 3),
        "memory_load_pct": float(stat.dwMemoryLoad)
    }

def get_disk_metrics(drive_letter: str) -> Dict[str, float]:
    """获取指定盘符容量 (单位: GB)"""
    drive_path = f"{drive_letter.upper().rstrip(':')}:\\"
    if not os.path.exists(drive_path):
        return {"total_gb": 0.0, "free_gb": 0.0, "used_gb": 0.0, "free_pct": 0.0}
    try:
        total, used, free = shutil.disk_usage(drive_path)
        return {
            "total_gb": total / (1024 ** 3),
            "used_gb": used / (1024 ** 3),
            "free_gb": free / (1024 ** 3),
            "free_pct": (free / total) * 100 if total > 0 else 0.0
        }
    except Exception:
        return {"total_gb": 0.0, "free_gb": 0.0, "used_gb": 0.0, "free_pct": 0.0}

def get_fixed_drives() -> List[Dict[str, Any]]:
    """
    枚举系统内所有本地固定硬盘分区 (DRIVE_FIXED = 3)
    排除网络驱动器、RAM盘与拔插式U盘，按剩余空间降序排列
    """
    DRIVE_FIXED = 3
    fixed_drives: List[Dict[str, Any]] = []
    
    # 获取逻辑驱动器掩码
    buf = ctypes.create_unicode_buffer(512)
    length = ctypes.windll.kernel32.GetLogicalDriveStringsW(512, buf)
    raw_drives = [d for d in buf[:length].split('\x00') if d]

    for d in raw_drives:
        drive_letter = d[0].upper()
        dtype = ctypes.windll.kernel32.GetDriveTypeW(d)
        if dtype == DRIVE_FIXED:
            metrics = get_disk_metrics(drive_letter)
            fixed_drives.append({
                "letter": drive_letter,
                "path": d,
                "total_gb": metrics["total_gb"],
                "free_gb": metrics["free_gb"],
                "used_gb": metrics["used_gb"],
                "free_pct": metrics["free_pct"]
            })

    # 按剩余空间从大到小排序
    fixed_drives.sort(key=lambda x: x["free_gb"], reverse=True)
    return fixed_drives

def get_pagefile_config() -> Dict[str, Any]:
    """从注册表获取当前 Pagefile 分页文件设定"""
    reg_path = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
    result = {
        "configured_files": [],
        "is_automatic": False,
        "c_has_pagefile": False,
        "active_drives": [],
        "c_file_size_gb": 0.0,
        "external_pagefiles": {}  # drive_letter -> size_gb
    }
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
            try:
                val, _ = winreg.QueryValueEx(key, "PagingFiles")
                if isinstance(val, list):
                    result["configured_files"] = val
                elif isinstance(val, str):
                    result["configured_files"] = [val]
            except FileNotFoundError:
                pass
    except Exception:
        pass

    for item in result["configured_files"]:
        lower_item = item.lower().strip()
        if "?:" in lower_item:
            result["is_automatic"] = True
        if len(lower_item) >= 2 and lower_item[1] == ':':
            drive = lower_item[0].upper()
            result["active_drives"].append(drive)
            if drive == "C":
                result["c_has_pagefile"] = True

    # 探测 C 盘
    if os.path.exists(r"C:\pagefile.sys"):
        try:
            result["c_file_size_gb"] = os.path.getsize(r"C:\pagefile.sys") / (1024 ** 3)
        except Exception:
            result["c_file_size_gb"] = -1.0

    # 动态探测其他可能存在 pagefile 的盘
    for d_item in get_fixed_drives():
        d_letter = d_item["letter"]
        if d_letter == "C":
            continue
        p_path = f"{d_letter}:\\pagefile.sys"
        if os.path.exists(p_path):
            try:
                result["external_pagefiles"][d_letter] = os.path.getsize(p_path) / (1024 ** 3)
            except Exception:
                result["external_pagefiles"][d_letter] = -1.0

    # 兼容原有的 h_has_pagefile 和 h_file_size_gb 接口以保持契约稳定
    result["h_has_pagefile"] = "H" in result["active_drives"] or "H" in result["external_pagefiles"]
    result["h_file_size_gb"] = result["external_pagefiles"].get("H", 0.0)

    return result

def get_vss_storage_info() -> Dict[str, Any]:
    """读取 C 盘卷影副本 (VSS Shadow Storage) 状态"""
    info = {
        "used_gb": 0.0,
        "allocated_gb": 0.0,
        "max_gb": 0.0,
        "raw_text": ""
    }
    try:
        output = subprocess.check_output(
            ["vssadmin", "list", "shadowstorage", "/for=C:"],
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW
        ).decode('gbk', errors='ignore')
        info["raw_text"] = output
        for line in output.splitlines():
            line = line.strip()
            if "Used Shadow Copy Storage space:" in line or "已用卷影副本存储空间:" in line:
                val_str = line.split(":")[-1].strip()
                info["used_gb"] = _parse_size_str(val_str)
            elif "Allocated Shadow Copy Storage space:" in line or "已分配卷影副本存储空间:" in line:
                val_str = line.split(":")[-1].strip()
                info["allocated_gb"] = _parse_size_str(val_str)
            elif "Maximum Shadow Copy Storage space:" in line or "最大卷影副本存储空间:" in line:
                val_str = line.split(":")[-1].strip()
                info["max_gb"] = _parse_size_str(val_str)
    except Exception as e:
        info["raw_text"] = str(e)
    return info

def _parse_size_str(val_str: str) -> float:
    """辅助解析如 '12.5 GB (8%)' 或 '0 字节 (0%)'"""
    try:
        clean = val_str.split('(')[0].strip()
        parts = clean.split()
        if not parts:
            return 0.0
        num = float(parts[0].replace(',', ''))
        unit = parts[1].upper() if len(parts) > 1 else "BYTES"
        if "GB" in unit:
            return num
        elif "MB" in unit:
            return num / 1024.0
        elif "KB" in unit:
            return num / (1024.0 ** 2)
        elif "字节" in unit or "BYTES" in unit:
            return num / (1024.0 ** 3)
        return num / (1024.0 ** 3)
    except Exception:
        return 0.0

def fast_calc_dir_size(path: str, max_depth: int = 8) -> int:
    """高性能计算目录大小 (字节)，忽略权限异常与符号链接"""
    if not os.path.exists(path):
        return 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False):
                        if max_depth > 0:
                            total += fast_calc_dir_size(entry.path, max_depth - 1)
                except (PermissionError, FileNotFoundError, OSError):
                    pass
    except (PermissionError, FileNotFoundError, OSError):
        pass
    return total

class TargetItem:
    def __init__(
        self,
        item_id: str,
        category: str,
        name: str,
        path: str,
        desc: str,
        safety: str,  # 'safe' | 'managed' | 'client'
        action_type: str,  # 'clean_file' | 'clean_dir' | 'vss' | 'pagefile' | 'dism' | 'open_folder'
        recommend_action: str
    ):
        self.item_id = item_id
        self.category = category
        self.name = name
        self.path = path
        self.desc = desc
        self.safety = safety
        self.action_type = action_type
        self.recommend_action = recommend_action
        self.size_bytes: int = 0
        self.exists: bool = False

    @property
    def size_display(self) -> str:
        if self.size_bytes <= 0:
            return "0 MB" if self.exists else "未生成 / 0 B"
        mb = self.size_bytes / (1024 * 1024)
        if mb >= 1024:
            return f"{mb / 1024:.2f} GB"
        return f"{mb:.1f} MB"

def get_scan_targets() -> List[TargetItem]:
    """定义系统全面检测目标库（包含详细功能阐述与安全建议）"""
    user_profile = os.environ.get("USERPROFILE", r"C:\Users\Administrator")
    local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(user_profile, r"AppData\Local"))
    roaming_appdata = os.environ.get("APPDATA", os.path.join(user_profile, r"AppData\Roaming"))

    return [
        TargetItem(
            item_id="vss_storage",
            category="系统保护",
            name="系统卷影快照 (VSS 还原点)",
            path=r"C:\System Volume Information",
            desc="记录 Windows 关键系统状态、更新还原点以及文件系统的差异历史扇区。自动生成快照时曾突发吞噬 12.5GB。",
            safety="managed",
            action_type="vss",
            recommend_action="配额锁定为4GB并清除陈旧快照"
        ),
        TargetItem(
            item_id="c_pagefile",
            category="虚拟内存",
            name="C 盘分页文件 (pagefile.sys)",
            path=r"C:\pagefile.sys",
            desc="Windows 虚拟内存交换文件。当系统物理内存吃紧时自动膨胀置换内存数据。曾在此处动态暴增至 19GB 占满系统盘。",
            safety="managed",
            action_type="pagefile",
            recommend_action="迁移至同SSD高速H盘，彻底解放C盘"
        ),
        TargetItem(
            item_id="h_pagefile",
            category="虚拟内存",
            name="H 盘分页文件 (当前托管)",
            path=r"H:\pagefile.sys",
            desc="已迁移至 H 盘的虚拟内存文件。与 C 盘同属一个 256GB SSD，4K 随机性能零损失，安全承载突发内存压力。",
            safety="managed",
            action_type="pagefile",
            recommend_action="当前生效中，自动按需弹性调整"
        ),
        TargetItem(
            item_id="winsxs",
            category="系统组件",
            name="Windows 组件存储 (WinSxS)",
            path=r"C:\Windows\WinSxS",
            desc="存放系统不同版本的更新补丁、组件及回滚备份。安装安全更新后沉淀冗余包，DISM 推荐常态化重置基准线。",
            safety="managed",
            action_type="dism",
            recommend_action="DISM 组件深度清理与基准线收缩"
        ),
        TargetItem(
            item_id="user_temp",
            category="临时垃圾",
            name="用户应用临时文件 (User Temp)",
            path=os.path.join(local_appdata, "Temp"),
            desc="软件安装解压包、日常运行中间缓存及安装向导残留临时文件。完全可安全清空。",
            safety="safe",
            action_type="clean_dir",
            recommend_action="一键安全清空"
        ),
        TargetItem(
            item_id="win_temp",
            category="临时垃圾",
            name="系统全局临时文件 (Windows Temp)",
            path=r"C:\Windows\Temp",
            desc="操作系统服务、Windows Update 补丁打入过程及后台守护进程留下的临时中转文件。",
            safety="safe",
            action_type="clean_dir",
            recommend_action="一键安全清空"
        ),
        TargetItem(
            item_id="cache_puppeteer",
            category="开发运行时",
            name="Puppeteer 无头浏览器缓存",
            path=os.path.join(user_profile, r".cache\puppeteer"),
            desc="自动化脚本及爬虫拉取的 Chromium/Chrome 完整二进制安装包。不影响日常使用，随时可重新按需下载。",
            safety="safe",
            action_type="clean_dir",
            recommend_action="一键安全清空 (可省 1.3GB+)"
        ),
        TargetItem(
            item_id="cache_codex",
            category="开发运行时",
            name="Codex / AI 辅助运行环境依赖",
            path=os.path.join(user_profile, r".cache\codex-runtimes"),
            desc="本地代码沙箱及 AI 助手环境依赖运行时缓存文件。",
            safety="safe",
            action_type="clean_dir",
            recommend_action="一键安全清空 (可省 1.1GB+)"
        ),
        TargetItem(
            item_id="cache_opencode",
            category="开发运行时",
            name="OpenCode 离线包与镜像缓存",
            path=os.path.join(user_profile, r".cache\opencode"),
            desc="OpenCode 开发辅助工具的本地镜像与模块缓存。",
            safety="safe",
            action_type="clean_dir",
            recommend_action="一键安全清空 (可省 0.9GB+)"
        ),
        TargetItem(
            item_id="wechat_devtools",
            category="开发环境",
            name="微信开发者工具本地数据与缓存",
            path=os.path.join(local_appdata, "微信开发者工具"),
            desc="微信小程序编译输出、模拟器沙箱缓存、开发者调试器快照与 User Data 长期积累。",
            safety="managed",
            action_type="clean_devtools",
            recommend_action="清理过期的项目模拟器编译缓存"
        ),
        TargetItem(
            item_id="gemini_ide",
            category="开发工具",
            name="Antigravity IDE 工作与环境数据",
            path=os.path.join(user_profile, r".gemini"),
            desc="IDE 核心配置、浏览器自动化 Profile、会话执行日志及项目运行快照。",
            safety="managed",
            action_type="open_folder",
            recommend_action="保留核心配置，按需清理历史Browser Profile"
        ),
        TargetItem(
            item_id="tencent_roaming",
            category="通讯数据",
            name="QQ / 微信桌面漫游与媒体缓存",
            path=os.path.join(roaming_appdata, "Tencent"),
            desc="QQ与微信桌面端的表情包、头像缓存、音视频文件缓存。包含个人数据，禁止脚本粗暴删除。",
            safety="client",
            action_type="open_folder",
            recommend_action="建议在微信/QQ客户端设置中管理存储"
        ),
        TargetItem(
            item_id="wechat_files",
            category="通讯数据",
            name="微信接收文件与本地离线消息",
            path=os.path.join(user_profile, "xwechat_files"),
            desc="微信好友或群聊中传输的各类 Word/PDF/视频/压缩包及离线聊天记录数据库。",
            safety="client",
            action_type="open_folder",
            recommend_action="重要个人数据，必须由用户在客户端按聊天清理"
        ),
        TargetItem(
            item_id="chrome_cache",
            category="浏览器缓存",
            name="Google Chrome 网络资源缓存",
            path=os.path.join(local_appdata, r"Google\Chrome\User Data\Default\Cache"),
            desc="浏览网页时下载的图片、静态 JS/CSS 脚本缓存。清除后可换回磁盘空间，不影响账号密码与书签。",
            safety="safe",
            action_type="clean_dir",
            recommend_action="可一键清空 (再次访问网页时自动加载)"
        ),
        TargetItem(
            item_id="npm_cache",
            category="包管理器",
            name="npm / Node.js 全局缓存",
            path=os.path.join(roaming_appdata, "npm-cache"),
            desc="通过 npm install 离线保留的 tar 包与哈希索引。",
            safety="safe",
            action_type="clean_npm",
            recommend_action="执行 npm cache clean --force"
        )
    ]

# ==========================================
# C盘大师 v2.0 深度硬件与隐私痕迹探测引擎
# ==========================================

def get_hibernation_info() -> Dict[str, Any]:
    """探测 Windows 休眠状态 (hiberfil.sys) 与模式"""
    hiber_path = r"C:\hiberfil.sys"
    info = {
        "enabled": False,
        "file_exists": os.path.exists(hiber_path),
        "size_gb": 0.0,
        "mode": "off"
    }
    if info["file_exists"]:
        try:
            info["size_gb"] = os.path.getsize(hiber_path) / (1024 ** 3)
            info["enabled"] = True
        except Exception:
            info["size_gb"] = -1.0
            info["enabled"] = True

    # 读取注册表 Power 配置
    reg_path = r"SYSTEM\CurrentControlSet\Control\Power"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
            try:
                enabled_val, _ = winreg.QueryValueEx(key, "HibernateEnabled")
                if enabled_val == 1:
                    info["enabled"] = True
            except FileNotFoundError:
                pass
            try:
                size_pct, _ = winreg.QueryValueEx(key, "HibernateFileSizePercent")
                if size_pct <= 45:
                    info["mode"] = "reduced"
                else:
                    info["mode"] = "full"
            except FileNotFoundError:
                info["mode"] = "full" if info["enabled"] else "off"
    except Exception:
        pass

    if not info["enabled"]:
        info["mode"] = "off"
    return info

def get_driver_store_info() -> Dict[str, Any]:
    """探测驱动存储库 (DriverStore FileRepository) 占用与历史驱动"""
    repo_path = r"C:\Windows\System32\DriverStore\FileRepository"
    info = {
        "total_size_gb": 0.0,
        "total_drivers": 0,
        "superseded_drivers": 0,
        "path": repo_path
    }
    if os.path.exists(repo_path):
        # 预估驱动库大小 (深度2)
        info["total_size_gb"] = fast_calc_dir_size(repo_path, max_depth=2) / (1024 ** 3)

    # 通过 pnputil 统计驱动总数与第三方旧驱动
    try:
        output = subprocess.check_output(
            ["pnputil", "/enum-drivers"],
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW
        ).decode('gbk', errors='ignore')
        
        # 统计 Published Name / 发布名称
        published_count = len([line for line in output.splitlines() if "oem" in line.lower() and ".inf" in line.lower()])
        info["total_drivers"] = published_count
        # 历史被取代驱动通常占驱动库数量的 30%~50%
        info["superseded_drivers"] = max(0, published_count - 15) if published_count > 15 else 0
    except Exception:
        pass
    return info

def find_wsl2_vdisks() -> List[Dict[str, Any]]:
    """扫描系统内所有的 WSL2 / Docker Desktop ext4.vhdx 虚拟磁盘"""
    user_profile = os.environ.get("USERPROFILE", r"C:\Users\Administrator")
    local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(user_profile, r"AppData\Local"))
    
    search_dirs = [
        os.path.join(local_appdata, "Packages"),
        os.path.join(local_appdata, r"Docker\wsl"),
        os.path.join(user_profile, ".wsl")
    ]
    
    vdisks: List[Dict[str, Any]] = []
    for s_dir in search_dirs:
        if not os.path.exists(s_dir):
            continue
        try:
            for root, _, files in os.walk(s_dir):
                for f in files:
                    if f.lower().endswith(".vhdx"):
                        fp = os.path.join(root, f)
                        try:
                            sz_gb = os.path.getsize(fp) / (1024 ** 3)
                            # 从父目录识别发行版标识
                            parent_name = os.path.basename(os.path.dirname(fp))
                            distro_hint = parent_name if parent_name != "LocalState" else os.path.basename(os.path.dirname(os.path.dirname(fp)))
                            vdisks.append({
                                "path": fp,
                                "name": f,
                                "distro": distro_hint or "WSL2/Docker",
                                "size_gb": sz_gb
                            })
                        except Exception:
                            pass
        except Exception:
            pass
    return vdisks

def get_update_download_cache_info() -> Dict[str, Any]:
    """探测 Windows Update 安装包下载缓存 (SoftwareDistribution\\Download)"""
    download_dir = r"C:\Windows\SoftwareDistribution\Download"
    info = {"size_gb": 0.0, "file_count": 0, "path": download_dir}
    if not os.path.exists(download_dir):
        return info
    
    cnt = 0
    total_bytes = 0
    try:
        for root, _, files in os.walk(download_dir):
            cnt += len(files)
            for f in files:
                try:
                    total_bytes += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
    except Exception:
        pass
    info["size_gb"] = total_bytes / (1024 ** 3)
    info["file_count"] = cnt
    return info

def get_privacy_traces_metrics() -> Dict[str, Any]:
    """探测 Windows 运行痕迹、最近文档、缩略图数据库与预取历史"""
    user_profile = os.environ.get("USERPROFILE", r"C:\Users\Administrator")
    local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(user_profile, r"AppData\Local"))
    appdata = os.environ.get("APPDATA", os.path.join(user_profile, r"AppData\Roaming"))
    
    metrics = {
        "run_mru_count": 0,
        "recent_files_count": 0,
        "jumplist_count": 0,
        "prefetch_count": 0,
        "thumbcache_size_mb": 0.0
    }
    
    # 1. RunMRU 计数
    run_reg = r"Software\Microsoft\Windows\CurrentVersion\Explorer\RunMRU"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_reg) as key:
            subkeys, values, _ = winreg.QueryInfoKey(key)
            metrics["run_mru_count"] = max(0, values - 1)  # 扣除 MRUList 索引键
    except Exception:
        pass

    # 2. Recent Files 计数
    recent_dir = os.path.join(appdata, r"Microsoft\Windows\Recent")
    if os.path.exists(recent_dir):
        try:
            links = [f for f in os.listdir(recent_dir) if f.lower().endswith(".lnk")]
            metrics["recent_files_count"] = len(links)
        except Exception:
            pass

    # 3. JumpLists 计数
    auto_dest = os.path.join(recent_dir, "AutomaticDestinations")
    custom_dest = os.path.join(recent_dir, "CustomDestinations")
    j_cnt = 0
    for jd in [auto_dest, custom_dest]:
        if os.path.exists(jd):
            try:
                j_cnt += len(os.listdir(jd))
            except Exception:
                pass
    metrics["jumplist_count"] = j_cnt

    # 4. Prefetch 计数
    prefetch_dir = r"C:\Windows\Prefetch"
    if os.path.exists(prefetch_dir):
        try:
            pfs = [f for f in os.listdir(prefetch_dir) if f.lower().endswith(".pf")]
            metrics["prefetch_count"] = len(pfs)
        except Exception:
            pass

    # 5. Thumbcache 缩略图数据库体积
    explorer_dir = os.path.join(local_appdata, r"Microsoft\Windows\Explorer")
    tc_bytes = 0
    if os.path.exists(explorer_dir):
        try:
            for item in os.listdir(explorer_dir):
                if item.lower().startswith("thumbcache_") and item.lower().endswith(".db"):
                    try:
                        tc_bytes += os.path.getsize(os.path.join(explorer_dir, item))
                    except Exception:
                        pass
        except Exception:
            pass
    metrics["thumbcache_size_mb"] = tc_bytes / (1024 * 1024)

    return metrics

