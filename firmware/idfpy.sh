#!/usr/bin/env bash
# Git Bash(MSYS)からESP-IDFのidf.pyを呼ぶためのラッパー。
#
# 背景: このマシンのGit Bash環境では
#   1. export.sh がシステムpython3経由でidf_tools.pyを呼ぶ際に
#      _ssl の DLL ロードがセキュリティポリシーでブロックされる
#   2. idf.py自体もMSYSTEM環境変数を検知すると起動を中断する
#      （MSYS2ランタイムが子プロセスにMSYSTEMを再注入するためenv -uでは回避不可）
#   3. cmake/ninja/xtensaツールチェーンがPATHに無い
# という3つの問題がある。ESP-IDF同梱のvenv pythonでidfpy_launcher.pyを実行し、
# 実行中のPythonプロセス内でos.environからMSYSTEMを取り除いてからidf.pyを
# runpyで実行し、`idf_tools.py export --format key-value`の出力（下記PATHの値）を
# 事前にcygpathでPOSIX形式へ変換してbashのPATHへ入れることで3つとも回避する。
#
# ツールバージョンを更新した場合は、下記コマンドで最新のPATH一覧を再取得して
# IDF_TOOLS_WIN_PATH を更新すること:
#   "/c/Espressif/python_env/idf5.5_py3.11_env/Scripts/python.exe" -c "
#   import os, sys, runpy
#   os.environ.pop('MSYSTEM', None)
#   tools_dir = r'C:\Espressif\frameworks\esp-idf-v5.5.4\tools'
#   sys.path.insert(0, tools_dir)
#   sys.argv = ['idf_tools.py', 'export', '--format', 'key-value']
#   runpy.run_path(tools_dir + r'\idf_tools.py', run_name='__main__')
#   "
#
# 使い方: ./idfpy.sh create-project --path . csi_ap
#         ./idfpy.sh set-target esp32s3
#         ./idfpy.sh build
#         ./idfpy.sh -p COM3 flash monitor

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IDF_VENV_PYTHON="/c/Espressif/python_env/idf5.5_py3.11_env/Scripts/python.exe"

IDF_TOOLS_WIN_PATH='C:\Espressif\tools\xtensa-esp-elf-gdb\16.3_20250913\xtensa-esp-elf-gdb\bin;C:\Espressif\tools\riscv32-esp-elf-gdb\16.3_20250913\riscv32-esp-elf-gdb\bin;C:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin;C:\Espressif\tools\esp-clang\esp-19.1.2_20250312\esp-clang\bin;C:\Espressif\tools\riscv32-esp-elf\esp-14.2.0_20260121\riscv32-esp-elf\bin;C:\Espressif\tools\esp32ulp-elf\2.38_20240113\esp32ulp-elf\bin;C:\Espressif\tools\cmake\3.30.2\bin;C:\Espressif\tools\openocd-esp32\v0.12.0-esp32-20251215\openocd-esp32\bin;C:\Espressif\tools\ninja\1.12.1\;C:\Espressif\tools\idf-exe\1.0.3\;C:\Espressif\tools\ccache\4.12.1\ccache-4.12.1-windows-x86_64;C:\Espressif\tools\dfu-util\0.11\dfu-util-0.11-win64;C:\Espressif\python_env\idf5.5_py3.11_env\Scripts;C:\Espressif\frameworks\esp-idf-v5.5.4\tools'

export PATH="$(cygpath -up "$IDF_TOOLS_WIN_PATH"):$PATH"
export IDF_PYTHON_ENV_PATH='C:\Espressif\python_env\idf5.5_py3.11_env'
export OPENOCD_SCRIPTS='C:\Espressif\tools\openocd-esp32\v0.12.0-esp32-20251215\openocd-esp32\share\openocd\scripts'
export IDF_CCACHE_ENABLE=1
export ESP_ROM_ELF_DIR='C:\Espressif\tools\esp-rom-elfs\20241011\'

"$IDF_VENV_PYTHON" "$SCRIPT_DIR/idfpy_launcher.py" "$@"
