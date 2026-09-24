#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen Image Studio — 环境自检脚本（仅用标准库，无需安装依赖即可运行）

用途：
  1) AutoDL 镜像审核时，快速验证镜像环境配置是否合格；
  2) 用户克隆实例后自检。

检查项：
  - Python 版本
  - 运行依赖是否可导入（fastapi / uvicorn / aiohttp / PIL / pydantic）
  - ComfyUI 目录与主程序
  - 模型软链接是否可解析（指向 AutoDL 公共模型库）
  - 工作流文件是否齐全
  - 前端文件与后端入口是否存在
  - 服务端口 6008 / 6006 监听状态

用法：
    python3 tools/check_env.py

退出码：0 = 全部必须项通过；1 = 存在必须项失败。
"""
import os
import socket
import sys

COMFY = os.environ.get("COMFY_DIR", "/root/ComfyUI")
MODELS = os.path.join(COMFY, "models")
WF_DIR = os.path.join(COMFY, "user", "default", "workflows")

PASS, FAIL, WARN = [], [], []


def ok(msg):
    PASS.append(msg)
    print(f"  [ OK ] {msg}")


def bad(msg):
    FAIL.append(msg)
    print(f"  [FAIL] {msg}")


def warn(msg):
    WARN.append(msg)
    print(f"  [WARN] {msg}")


def port_open(port):
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def main():
    # 允许用 --repo <path> 指定仓库根目录
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "--repo" in sys.argv:
        try:
            repo = os.path.abspath(sys.argv[sys.argv.index("--repo") + 1])
        except IndexError:
            pass

    print("=" * 62)
    print(" Qwen Image Studio — 环境自检")
    print("=" * 62)

    print(f"\n[1/7] Python 环境")
    v = sys.version_info
    print(f"  解释器: {sys.executable}")
    print(f"  版本  : {v.major}.{v.minor}.{v.micro}")
    if v >= (3, 10):
        ok(f"Python {v.major}.{v.minor} 满足要求 (>=3.10)")
    else:
        bad(f"Python {v.major}.{v.minor} 版本过低，需要 >=3.10")

    print(f"\n[2/7] 运行依赖")
    for mod, name in (("fastapi", "FastAPI"), ("uvicorn", "uvicorn"),
                      ("aiohttp", "aiohttp"), ("PIL", "Pillow"),
                      ("pydantic", "pydantic")):
        try:
            __import__(mod)
            ok(f"{name} 已安装")
        except Exception as e:
            bad(f"{name} 缺失或导入失败: {e}")

    print(f"\n[3/7] ComfyUI")
    if os.path.isdir(COMFY):
        ok(f"ComfyUI 目录存在: {COMFY}")
    else:
        bad(f"ComfyUI 目录不存在: {COMFY}")
    mainpy = os.path.join(COMFY, "main.py")
    if os.path.isfile(mainpy):
        ok("ComfyUI 主程序 main.py 存在")
    else:
        bad(f"缺少 {mainpy}")

    print(f"\n[4/7] 模型软链接（指向公共模型库）")
    need = [
        ("diffusion_models/qwen_image_2.1_bf16.safetensors", False),
        ("diffusion_models/qwen_image_2.1_int8_convrot.safetensors", False),
        ("text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors", False),
        ("text_encoders/qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors", False),
        ("text_encoders/qwen3vl_8b_int8_convrot.safetensors", False),
        ("vae/qwen_image_2.1_vae_bf16.safetensors", False),
    ]
    for rel, _ in need:
        p = os.path.join(MODELS, rel)
        if os.path.islink(p):
            if os.path.exists(os.path.realpath(p)):
                ok(f"软链接有效: {rel}")
            else:
                bad(f"软链接失效（公共库未挂载?）: {rel} -> {os.readlink(p)}")
        elif os.path.isfile(p):
            warn(f"实体文件（会占用磁盘）: {rel}")
        else:
            bad(f"缺失: {rel}")

    print(f"\n[5/7] 工作流")
    if os.path.isdir(WF_DIR):
        files = sorted(f for f in os.listdir(WF_DIR) if f.endswith(".json"))
        if files:
            ok(f"工作流目录存在，共 {len(files)} 个: {'、'.join(files)}")
        else:
            bad(f"工作流目录为空: {WF_DIR}")
    else:
        bad(f"工作流目录不存在: {WF_DIR}")

    print(f"\n[6/7] 应用文件")
    for rel in ("server.py", "web/index.html"):
        p = os.path.join(repo, rel)
        if os.path.isfile(p):
            ok(f"{rel} 存在")
        else:
            bad(f"{rel} 不存在（仓库不完整?）")

    print(f"\n[7/7] 服务端口")
    for port, name in ((6008, "Qwen Image Studio"), (6006, "ComfyUI")):
        if port_open(port):
            ok(f"{name} 端口 {port} 正在监听")
        else:
            warn(f"{name} 端口 {port} 未监听（尚未启动，执行 bash scripts/start-all.sh）")

    print("\n" + "=" * 62)
    print(f" 结果: 通过 {len(PASS)} 项 / 失败 {len(FAIL)} 项 / 提示 {len(WARN)} 项")
    if FAIL:
        print(" 状态: 环境不完整，请按上面 [FAIL] 项修复")
        for f in FAIL:
            print(f"   - {f}")
        print("=" * 62)
        return 1
    print(" 状态: 环境合格 ✅")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
