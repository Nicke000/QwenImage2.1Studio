#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_models.py — 从 HF 镜像下载 Qwen-Image-2.1 模型到数据盘，并软链接进 ComfyUI

设计要点：
  * 模型存放在数据盘 /root/autodl-tmp/models（不进系统盘、不进镜像）
  * 下载后软链接到 /root/ComfyUI/models/<子目录>/<文件名>，工作流无需改动
  * 默认 fast 集（约 16GB，够文生图/图生图/放大）；--hq 追加高质量；--all 全量
  * 国内走 hf-mirror.com，支持断点续传
"""
import os, sys, argparse, shutil, subprocess

REPO = "Comfy-Org/Qwen-Image-2.1"
DEST = os.environ.get("MODELS_DIR", "/root/autodl-tmp/models")
COMFY_MODELS = os.environ.get("COMFY_MODELS", "/root/ComfyUI/models")

FAST = [
    "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
    "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
    "vae/qwen_image_2.1_vae_bf16.safetensors",
]
HQ = [
    "diffusion_models/qwen_image_2.1_bf16.safetensors",
    "text_encoders/qwen3vl_8b_bf16.safetensors",
]
PE = [
    "text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors",
    "text_encoders/qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors",
]


def link(file_rel):
    """把数据盘的模型软链接到 ComfyUI/models 对应子目录"""
    src = os.path.join(DEST, file_rel)
    dst = os.path.join(COMFY_MODELS, file_rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.islink(dst) and os.path.realpath(dst) == os.path.realpath(src):
        return "已有链接"
    if os.path.exists(dst) and not os.path.islink(dst):
        return "目标已是实体文件，跳过"
    if os.path.islink(dst) or os.path.exists(dst):
        os.remove(dst)
    os.symlink(src, dst)
    return "已链接"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hq", action="store_true", help="追加高质量 BF16 模型（约 +30GB）")
    ap.add_argument("--pe", action="store_true", help="追加 AI 优化用的 PE 编码器（约 +18GB）")
    ap.add_argument("--all", action="store_true", help="下载全部模型（约 63GB）")
    ap.add_argument("--link-only", action="store_true", help="只重建软链接，不下载")
    args = ap.parse_args()

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    files = list(FAST)
    if args.all or args.hq:
        files += HQ
    if args.all or args.pe:
        files += PE

    os.makedirs(DEST, exist_ok=True)
    print(f"目标目录: {DEST}")
    print(f"HF 端点 : {os.environ['HF_ENDPOINT']}")
    print(f"待处理  : {len(files)} 个文件")

    if not args.link_only:
        from huggingface_hub import hf_hub_download
        for i, rel in enumerate(files, 1):
            print(f"\n[{i}/{len(files)}] {rel}", flush=True)
            try:
                p = hf_hub_download(repo_id=REPO, filename=rel, local_dir=DEST)
                print(f"   下载完成: {p}", flush=True)
            except Exception as e:
                print(f"   下载失败: {e}", flush=True)
                continue
            print(f"   {link(rel)}", flush=True)
    else:
        for rel in files:
            print(f"  {link(rel)}  {rel}")

    print("\n=== 当前软链接状态 ===")
    for rel in files:
        dst = os.path.join(COMFY_MODELS, rel)
        ok = os.path.exists(os.path.realpath(dst)) if os.path.islink(dst) else os.path.exists(dst)
        sz = ""
        try:
            sz = f"{os.path.getsize(os.path.realpath(dst))/2**30:.2f}GB"
        except Exception:
            sz = "缺失"
        print(f"  [{'OK' if ok else '!!'}] {rel}  {sz}")
    print("FETCH_DONE")


if __name__ == "__main__":
    main()
