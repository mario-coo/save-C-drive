# -*- coding: utf-8 -*-
"""
actions.py - C盘与存储清理、虚拟内存管理、VSS优化执行模块
"""

import os
import shutil
import subprocess
import time
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
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    log_cb(line.rstrip())
        finally:
            if proc.stdout:
                proc.stdout.close()
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


# ==========================================
# C盘大师 v2.0 硬件瘦身与反取证去痕动作执行
# ==========================================

def manage_hibernation(mode: str, log_cb: Callable[[str], None]) -> bool:
    """
    休眠文件管理:
    - 'reduced': 精简休眠，体积减半并保留快速启动 (powercfg /h /type reduced)
    - 'off': 彻底关闭休眠并删除 hiberfil.sys (powercfg /h off)
    - 'full': 恢复全量休眠 (powercfg /h /type full)
    """
    mode = mode.lower()
    log_cb(f"正在配置系统休眠模式为: {mode}...")
    if mode == "off":
        code = run_command_stream(["powercfg", "/hibernate", "off"], log_cb)
        if code == 0:
            log_cb("休眠功能已彻底关闭，C:\\hiberfil.sys 已被操作系统销毁释放！")
            return True
        return False
    elif mode == "reduced":
        run_command_stream(["powercfg", "/hibernate", "on"], log_cb)
        code = run_command_stream(["powercfg", "/h", "/type", "reduced"], log_cb)
        if code == 0:
            log_cb("已成功开启精简休眠模式！hiberfil.sys 体积削减约 50%，同时完美保留 Windows 快速启动。")
            return True
        return False
    elif mode == "full":
        run_command_stream(["powercfg", "/hibernate", "on"], log_cb)
        code = run_command_stream(["powercfg", "/h", "/type", "full"], log_cb)
        if code == 0:
            log_cb("已恢复全量完整休眠模式。")
            return True
        return False
    return False

def clean_driver_store(log_cb: Callable[[str], None]) -> int:
    """清理已被新版本取代且脱机的第三方历史旧驱动 (pnputil)"""
    log_cb("正在扫描并精简 DriverStore 历史旧驱动...")
    freed_count = 0
    try:
        output = subprocess.check_output(
            ["pnputil", "/enum-drivers"],
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW
        ).decode('gbk', errors='ignore')
        
        # 提取所有的 oemXX.inf
        oem_infs = []
        for line in output.splitlines():
            line = line.strip()
            if "oem" in line.lower() and ".inf" in line.lower():
                parts = line.split(":")
                inf_name = parts[-1].strip()
                if inf_name.lower().endswith(".inf") and inf_name.lower().startswith("oem"):
                    oem_infs.append(inf_name)
        
        log_cb(f"发现 {len(oem_infs)} 个第三方驱动安装包，开始排查可安全清理的历史冗余...")
        for inf in oem_infs:
            # 仅对旧驱动尝试删除；在用中的活动驱动 Windows 会自动拒绝删除 (/uninstall 确保安全)
            res = subprocess.run(
                ["pnputil", "/delete-driver", inf],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='gbk',
                errors='ignore',
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if "已成功删除驱动程序程序包" in res.stdout or "Driver package deleted successfully" in res.stdout:
                freed_count += 1
                log_cb(f"成功清理旧驱动: {inf}")
        
        log_cb(f"DriverStore 旧驱动精简完成！共清理了 {freed_count} 个脱机历史驱动包。")
    except Exception as e:
        log_cb(f"驱动库清理异常: {e}")
    return freed_count

def compact_wsl2_vdisk(vhdx_path: str, log_cb: Callable[[str], None]) -> bool:
    """通过 diskpart compact vdisk 物理回缩 WSL2/Docker 虚拟磁盘文件"""
    if not os.path.exists(vhdx_path):
        log_cb(f"错误: 未找到指定的虚拟磁盘文件: {vhdx_path}")
        return False
    
    log_cb(f"正在准备压缩 WSL2 虚拟硬盘: {vhdx_path}")
    log_cb("1. 正在停止 WSL2 子系统以释放文件锁...")
    run_command_stream(["wsl", "--shutdown"], log_cb)
    
    # 构造临时 diskpart 脚本
    import tempfile
    script_content = f'select vdisk file="{vhdx_path}"\nattach vdisk readonly\ncompact vdisk\ndetach vdisk\n'
    
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write(script_content)
        script_file = tf.name
    
    log_cb("2. 正在调用 diskpart 物理回缩空闲扇区（根据磁盘大小约需 30 秒至 2 分钟）...")
    try:
        code = run_command_stream(["diskpart", "/s", script_file], log_cb)
        try:
            os.unlink(script_file)
        except Exception:
            pass
        
        if code == 0:
            log_cb(f"WSL2 磁盘压缩成功！磁盘空间已物理归还宿主系统。")
            return True
        else:
            log_cb(f"diskpart 执行返回码: {code}")
            return False
    except Exception as e:
        log_cb(f"WSL2 磁盘压缩遇到异常: {e}")
        return False

def clean_update_cache(log_cb: Callable[[str], None]) -> int:
    """清理 Windows Update 安装包下载缓存 (挂起 wuauserv 服务)"""
    download_dir = r"C:\Windows\SoftwareDistribution\Download"
    if not os.path.exists(download_dir):
        log_cb("未发现 SoftwareDistribution\\Download 目录。")
        return 0
    
    log_cb("正在临时挂起 Windows Update 服务...")
    subprocess.run(["net", "stop", "wuauserv"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    
    freed = 0
    log_cb("正在清空更新安装包下载临时缓存...")
    try:
        for root, dirs, files in os.walk(download_dir, topdown=False):
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
    except Exception as e:
        log_cb(f"清理文件时跳过锁定项: {e}")
    finally:
        log_cb("正在恢复 Windows Update 服务...")
        subprocess.run(["net", "start", "wuauserv"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        log_cb(f"Windows Update 下载缓存清理完毕，共释放: {freed / (1024*1024):.2f} MB")
    return freed

def clean_run_mru(log_cb: Callable[[str], None]) -> bool:
    """擦除 Win+R 运行历史记录 (RunMRU)"""
    log_cb("正在清空 Win+R 运行历史记录 (RunMRU)...")
    reg_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\RunMRU"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_ALL_ACCESS) as key:
            # 获取所有值名称
            val_names = []
            try:
                i = 0
                while True:
                    name, _, _ = winreg.EnumValue(key, i)
                    val_names.append(name)
                    i += 1
            except OSError:
                pass
            
            for vn in val_names:
                try:
                    winreg.DeleteValue(key, vn)
                except Exception:
                    pass
        log_cb("Win+R 运行记录已全部安全擦除！")
        return True
    except Exception as e:
        log_cb(f"擦除 RunMRU 异常: {e}")
        return False

def clean_recent_and_jumplists(log_cb: Callable[[str], None]) -> int:
    """清理最近打开文件快捷方式 (Recent) 与任务栏跳转列表 (JumpLists)"""
    import ctypes
    log_cb("正在抹除最近文档访问足迹与任务栏跳转列表...")
    appdata = os.environ.get("APPDATA", r"C:\Users\Administrator\AppData\Roaming")
    recent_dir = os.path.join(appdata, r"Microsoft\Windows\Recent")
    
    deleted_count = 0
    if os.path.exists(recent_dir):
        # 1. 递归删除 Recent 及 AutomaticDestinations、CustomDestinations
        for root, dirs, files in os.walk(recent_dir, topdown=False):
            for f in files:
                try:
                    os.unlink(os.path.join(root, f))
                    deleted_count += 1
                except Exception:
                    pass
        
        # 2. 调用 Windows Shell API 刷新通知清空
        try:
            ctypes.windll.shell32.SHAddToRecentDocs(0, None)
        except Exception:
            pass
            
    log_cb(f"最近访问足迹已彻底清空！共消除 {deleted_count} 条痕迹项。")
    return deleted_count

def clean_prefetch(log_cb: Callable[[str], None]) -> int:
    """清空 Windows 应用程序执行预取痕迹 (C:\\Windows\\Prefetch)"""
    log_cb("正在清空 Windows 应用程序执行预取记录 (Prefetch)...")
    prefetch_dir = r"C:\Windows\Prefetch"
    if not os.path.exists(prefetch_dir):
        log_cb("未找到 Prefetch 目录或系统已禁用。")
        return 0
    
    cnt = 0
    try:
        for f in os.listdir(prefetch_dir):
            if f.lower().endswith(".pf"):
                fp = os.path.join(prefetch_dir, f)
                try:
                    os.unlink(fp)
                    cnt += 1
                except Exception:
                    pass
        log_cb(f"Prefetch 预取痕迹清理完成！共销毁 {cnt} 个程序启动痕迹文件。")
    except Exception as e:
        log_cb(f"Prefetch 访问受限: {e}")
    return cnt

def clean_thumbcache(log_cb: Callable[[str], None]) -> int:
    """平滑重启 Explorer 并粉碎缩略图数据库缓存 (thumbcache_*.db)"""
    log_cb("【敏感操作】正在终止 explorer.exe 进程以解锁缩略图数据库...")
    freed = 0
    try:
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(0.8)
        
        local_appdata = os.environ.get("LOCALAPPDATA", r"C:\Users\Administrator\AppData\Local")
        explorer_dir = os.path.join(local_appdata, r"Microsoft\Windows\Explorer")
        
        if os.path.exists(explorer_dir):
            for item in os.listdir(explorer_dir):
                if (item.lower().startswith("thumbcache_") or item.lower().startswith("iconcache_")) and item.lower().endswith(".db"):
                    fp = os.path.join(explorer_dir, item)
                    try:
                        sz = os.path.getsize(fp)
                        os.unlink(fp)
                        freed += sz
                    except Exception:
                        pass
        log_cb(f"缩略图与图标数据库粉碎完毕，共释放: {freed / (1024*1024):.2f} MB")
    except Exception as e:
        log_cb(f"缩略图数据库删除异常: {e}")
    finally:
        log_cb("正在自动拉起并恢复 Windows 资源管理器 (explorer.exe)...")
        subprocess.Popen("explorer.exe")
    return freed

def flush_dns_cache(log_cb: Callable[[str], None]) -> bool:
    """刷新本地 DNS 解析缓存"""
    log_cb("正在刷新 Windows DNS 本地解析缓存...")
    code = run_command_stream(["ipconfig", "/flushdns"], log_cb)
    if code == 0:
        log_cb("DNS 本地解析缓存已刷新！")
        return True
    return False

