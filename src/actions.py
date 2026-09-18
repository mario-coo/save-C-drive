# -*- coding: utf-8 -*-
"""
actions.py - C盘与存储清理、虚拟内存管理、VSS优化执行模块
"""

import os
import shutil
import subprocess
import winreg
from typing import Callable, Tuple, Dict, Any, List

def run_command_stream(cmd: List[str], log_cb: Callable[[str], None]) -> int:
    """流式执行系统命令并回调输出"""
    log_cb(f"执行系统指令: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='gbk',
            errors='ignore',
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        for line in proc.stdout:
            log_cb(line.rstrip())
        proc.wait()
        return proc.returncode
    except Exception as e:
        log_cb(f"执行失败: {e}")
        return -1

def clean_safe_paths(log_cb: Callable[[str], None]) -> int:
    """
    清理安全临时文件与开发环境废弃缓存
    返回释放的大致字节数
    """
    user_profile = os.environ.get("USERPROFILE", r"C:\Users\Administrator")
    local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(user_profile, r"AppData\Local"))

    safe_dirs = [
        os.path.join(local_appdata, "Temp"),
        r"C:\Windows\Temp",
        os.path.join(user_profile, r".cache\puppeteer"),
        os.path.join(user_profile, r".cache\codex-runtimes"),
        os.path.join(user_profile, r".cache\opencode"),
        os.path.join(local_appdata, r"Google\Chrome\User Data\Default\Cache")
    ]

    total_freed = 0
    for target in safe_dirs:
        if not os.path.exists(target):
            continue
        log_cb(f"正在扫描并清理: {target}")
        freed = 0
        if os.path.isdir(target):
            # 对于 Temp 目录清理其内部子文件和文件夹，不删除 Temp 本身
            if "temp" in target.lower():
                try:
                    for item in os.scandir(target):
                        try:
                            if item.is_file(follow_symlinks=False):
                                sz = item.stat(follow_symlinks=False).st_size
                                os.unlink(item.path)
                                freed += sz
                            elif item.is_dir(follow_symlinks=False):
                                sz = _calc_and_rm_dir(item.path)
                                freed += sz
                        except (PermissionError, FileNotFoundError, OSError):
                            pass
                except Exception as e:
                    log_cb(f"扫描目录跳过锁住项: {e}")
            else:
                freed = _calc_and_rm_dir(target)
        elif os.path.isfile(target):
            try:
                sz = os.path.getsize(target)
                os.unlink(target)
                freed += sz
            except Exception:
                pass

        total_freed += freed
        log_cb(f"已释放: {target} -> {freed / (1024*1024):.2f} MB")

    # 执行 npm 缓存清理
    try:
        log_cb("尝试清理 npm 本地缓存...")
        subprocess.run(
            ["npm.cmd", "cache", "clean", "--force"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=15
        )
        log_cb("npm 缓存清理完成")
    except Exception:
        pass

    return total_freed

def _calc_and_rm_dir(path: str) -> int:
    """递归计算大小并删除目录"""
    freed = 0
    try:
        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    freed += os.path.getsize(fp)
                    os.unlink(fp)
                except Exception:
                    pass
            for d in dirs:
                dp = os.path.join(root, d)
                try:
                    os.rmdir(dp)
                except Exception:
                    pass
        try:
            os.rmdir(path)
        except Exception:
            pass
    except Exception:
        pass
    return freed

def optimize_vss(max_size: str, log_cb: Callable[[str], None]) -> bool:
    """
    调整 C 盘卷影副本配额上限为 max_size (例如 '4GB')，
    Windows 会自动裁剪超出配额的陈旧快照
    """
    log_cb(f"正在配置系统卷影存储 (VSS) 上限为 {max_size}...")
    cmd = ["vssadmin", "resize", "shadowstorage", "/for=C:", "/on=C:", f"/maxsize={max_size}"]
    code = run_command_stream(cmd, log_cb)
    if code == 0:
        log_cb("卷影存储配额优化成功！旧快照已被 Windows 自动裁剪。")
        return True
    else:
        log_cb(f"卷影存储调整失败，返回码: {code}")
        return False

def configure_pagefile_drive(target_drive: str, log_cb: Callable[[str], None]) -> bool:
    r"""
    配置虚拟内存托管盘符（例如 'H' 或 'D'）
    执行严苛的防御性介质校验，并在目标盘建立系统管理大小 (0 0)
    若旧 C:\pagefile.sys 存在内核锁，自动注册 Windows 引导层延迟销毁 (MoveFileExW)
    """
    import ctypes
    DRIVE_FIXED = 3
    MOVEFILE_DELAY_UNTIL_REBOOT = 0x4

    target_letter = target_drive.upper().rstrip(':')
    target_root = f"{target_letter}:\\"

    # Guard 1: 盘符有效性与存在性拦截
    if not os.path.exists(target_root):
        log_cb(f"【安全拦截】目标盘符 {target_root} 不存在，已终止操作！")
        return False

    # Guard 2: 存储介质类型校验 (杜绝可移动介质或网络卷)
    drive_type = ctypes.windll.kernel32.GetDriveTypeW(target_root)
    if drive_type != DRIVE_FIXED:
        log_cb(f"【安全拦截】驱动器 {target_root} 不是本地固定硬盘（类型代码: {drive_type}）。为防止介质脱机导致系统蓝屏崩溃，禁止迁移虚拟内存！")
        return False

    # Guard 3: 目标磁盘可用空间校验 (至少需要 4GB 基础吞吐冗余)
    try:
        _, _, free_bytes = shutil.disk_usage(target_root)
        free_gb = free_bytes / (1024 ** 3)
        if free_gb < 4.0:
            log_cb(f"【容量预警】目标盘 {target_root} 剩余可用空间仅有 {free_gb:.1f} GB，不足 4GB，建议先清理目标盘。")
    except Exception:
        pass

    log_cb(f"【安全检查通过】目标盘 {target_letter}: 为合格本地固定存储，开始迁移...")

    # 1. 禁用全局自动管理 (通过 PowerShell WMI)
    ps_wmi = '$cs = Get-CimInstance Win32_ComputerSystem; Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile = $false}'
    ret = run_command_stream(["powershell", "-NoProfile", "-Command", ps_wmi], log_cb)
    if ret != 0:
        log_cb("警告: 自动管理设置更新遇到异常，尝试直接写入注册表...")

    # 2. 写入注册表 Session Manager
    reg_path = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
    new_value = [f"{target_letter}:\\pagefile.sys 0 0"]
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "PagingFiles", 0, winreg.REG_MULTI_SZ, new_value)
        log_cb(f"注册表 PagingFiles 已安全更新为: {new_value}")
    except Exception as e:
        log_cb(f"注册表写入失败: {e}")
        return False

    # 3. 针对旧的 C:\pagefile.sys 的处理
    if target_letter != "C" and os.path.exists(r"C:\pagefile.sys"):
        try:
            os.unlink(r"C:\pagefile.sys")
            log_cb("检测到旧的 C:\\pagefile.sys 已未被内核占用，并已直接清理删除！")
        except PermissionError:
            # 当前会话内核独占锁，注册 Windows 引导层延迟销毁指令
            success = ctypes.windll.kernel32.MoveFileExW(
                r"C:\pagefile.sys",
                None,
                MOVEFILE_DELAY_UNTIL_REBOOT
            )
            if success:
                log_cb("【高阶机制】旧的 C:\\pagefile.sys 处于系统内核独占锁中。已向 Windows 注册系统重启延迟删除标记 (MoveFileEx)，下次开机引导阶段将由内核自动彻底物理销毁，自动腾出 C 盘空间！")
            else:
                log_cb("提示: 旧的 C:\\pagefile.sys 处于当前会话锁定中，可在重启电脑后重新打开本工具扫描并清除。")

    log_cb(f"虚拟内存迁移配置成功！已指向 {target_letter}: 盘，将在下次电脑重启后全量生效。")
    return True

def run_dism_cleanup(log_cb: Callable[[str], None]) -> bool:
    """调用 DISM 工具深度清理已取代的系统更新组件"""
    log_cb("正在启动 DISM 系统组件存储清理（耗时约 1~3 分钟，请稍候）...")
    cmd = ["Dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"]
    code = run_command_stream(cmd, log_cb)
    if code == 0:
        log_cb("DISM 组件存储清理执行成功！")
        return True
    else:
        log_cb(f"DISM 清理返回状态: {code}")
        return False

def open_in_explorer(path: str) -> None:
    """在 Windows 资源管理器中打开指定目录或高亮文件"""
    if not os.path.exists(path):
        return
    try:
        if os.path.isdir(path):
            os.startfile(path)
        else:
            subprocess.Popen(f'explorer /select,"{path}"')
    except Exception:
        pass
