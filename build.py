# -*- coding: utf-8 -*-
"""
build.py - DiskCleanPro 一键独立 EXE 打包脚本
"""

import os
import sys
import subprocess
import shutil

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.join(root_dir, "src")
    main_file = os.path.join(src_dir, "main.py")
    dist_dir = os.path.join(root_dir, "dist")
    build_dir = os.path.join(root_dir, "build")
    release_dir = os.path.join(root_dir, "release")

    print("[1/3] 检查构建环境与入口文件...")
    if not os.path.exists(main_file):
        print(f"错误: 未找到主程序入口: {main_file}")
        sys.exit(1)

    os.makedirs(release_dir, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--uac-admin",
        "--name", "DiskCleanPro",
        "--clean",
        "--distpath", dist_dir,
        "--workpath", build_dir,
        main_file
    ]

    print("[2/3] 正在使用 PyInstaller 进行独立单文件编译打包...")
    print(f"执行命令: {' '.join(cmd)}")
    ret = subprocess.run(cmd, cwd=src_dir)
    if ret.returncode != 0:
        print("构建失败，请检查上方日志。")
        sys.exit(ret.returncode)

    built_exe = os.path.join(dist_dir, "DiskCleanPro.exe")
    target_exe = os.path.join(release_dir, "DiskCleanPro.exe")

    print("[3/3] 正在拷贝产物至 release 目录并清理临时构建缓存...")
    if os.path.exists(built_exe):
        shutil.copy2(built_exe, target_exe)
        sz_mb = os.path.getsize(target_exe) / (1024 * 1024)
        print(f"构建完成！可执行程序已输出至: {target_exe} ({sz_mb:.2f} MB)")

    # 清理 build 临时文件夹
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir, ignore_errors=True)
    if os.path.exists(dist_dir):
        shutil.rmtree(dist_dir, ignore_errors=True)

    spec_file = os.path.join(src_dir, "DiskCleanPro.spec")
    if os.path.exists(spec_file):
        os.unlink(spec_file)

if __name__ == "__main__":
    main()
