#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen Image Studio — FastAPI 后端 v2
对接 ComfyUI 0.37 (Qwen-Image-2.1)，实现 README 中的 API 契约：
  POST /api/generate   提交生成任务  {prompt, negative_prompt, model, ratio, resLv, steps, seed, count, refs[], wf, optimize}
  GET  /api/task/{id}  轮询进度      {status, pct, node, etaSec, nodes[] 节点级进度}
  GET  /api/image/{id} 下载 PNG      多张按序号 (?n=0..count-1)
  POST /api/restart    重启生成服务  (管理员口令)
鉴权：Authorization: Bearer <访问口令>；restart 需管理员口令。

工作流绑定（wf 参数，与用户实测一致的 4 条链路）：
  t2i     = 文生图                 （无参考图；TextEncode + EmptyLatent）
  t2i_opt = 文生图带优化            （无参考图 + optimize；TextGenerateLTX2Prompt 优化提示词）
  i2i     = 图片编辑（细）          （有参考图；1~10 张自适应 images.image_1..N，输入几张就启用几张）
  rmbg    = 移除背景                （有参考图 + transparent；多张图自动依次提交每张抠图）
"""
import asyncio, base64, io, json, os, re, shutil, subprocess, time, uuid
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ================= 配置 =================
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:6006")
COMFY_INPUT = Path(os.environ.get("COMFY_INPUT", "/root/ComfyUI/input"))
COMFY_OUTPUT = Path(os.environ.get("COMFY_OUTPUT", "/root/ComfyUI/output"))
TOKEN = os.environ.get("API_TOKEN", "").strip()        # 空 = 开箱即用（不鉴权）；设置后需 Bearer 口令
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", TOKEN).strip()  # 管理员口令（restart）；空 = 与访问口令一致
MAX_QUEUE = int(os.environ.get("MAX_QUEUE", "8"))             # 排队上限

# 官方原生分辨率（ModelScope Qwen-Image-2.1 卡片）
RATIOS = {
    "1:1": (2048, 2048), "4:3": (2400, 1792), "3:4": (1792, 2400),
    "3:2": (2528, 1696), "2:3": (1696, 2528), "16:9": (2752, 1536), "9:16": (1536, 2752),
}
RES_LEVELS = {0: "官方原生", 480: "480P", 720: "720P", 1080: "1080P", 1440: "1K", 2560: "2K", 4096: "4K*"}
MODELS = {
    "fast": {"file": "qwen_image_2.1_int8_convrot.safetensors", "tag": "INT8", "speed": 1.0},
    "hq": {"file": "qwen_image_2.1_bf16.safetensors", "tag": "BF16", "speed": 1.7},
}
# t2i / i2i 文本编码器
TE_T2I = "qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors"
TE_I2I = "qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors"
TE_VL = "qwen3vl_8b_int8_convrot.safetensors"    # 图片编辑（细）用视觉语言编码器
VAE = "qwen_image_2.1_vae_bf16.safetensors"

# 绑定工作流节点清单（id -> 中文阶段名），用于节点级进度
WF_STEPS = {
    "t2i":     [("1","文本编码器"), ("2","扩散模型"), ("3","VAE"), ("5","提示词编码"), ("9","空潜像"), ("6","采样生成"), ("7","解码"), ("8","保存图片")],
    "t2i_opt": [("1","文本编码器"), ("2","扩散模型"), ("3","VAE"), ("10","提示词优化"), ("5","提示词编码"), ("9","空潜像"), ("6","采样生成"), ("7","解码"), ("8","保存图片")],
    "i2i":     [("1","文本编码器"), ("2","扩散模型"), ("3","VAE"), ("5","提示/参考编码"), ("6","采样生成"), ("7","解码"), ("8","保存图片")],
    "rmbg":    [("1","文本编码器"), ("2","扩散模型"), ("3","VAE"), ("5","提示/参考编码"), ("6","采样生成"), ("7","解码"), ("8","保存图片")],
    "upscale": [("2","放大模型加载"), ("3","高清放大"), ("4","保存放大图")],
}

# ================= GPU 检测 / 自适应 =================
MODEL_VRAM_GB = {"fast": 17.0, "hq": 24.5}
RES_LONG = {0: 2752, 480: 480, 720: 720, 1080: 1080, 1440: 1440, 2560: 2560, 4096: 2752}
_GPU_CACHE = {"ts": 0.0, "info": None}

async def gpu_info():
    now = time.time()
    if _GPU_CACHE["info"] and now - _GPU_CACHE["ts"] < 2:
        return _GPU_CACHE["info"]
    def _probe():
        try:
            ni = shutil.which("nvidia-smi")
            if not ni:
                return None
            out = subprocess.run([ni, "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
            line = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                return None
            name = parts[0]
            def f(x):
                try: return int(float(x))
                except: return 0
            return {"name": name, "vramTotal": f(parts[1]), "vramUsed": f(parts[2]), "vramFree": f(parts[3]),
                    "util": f(parts[4]), "temp": f(parts[5]), "power": round(f(parts[6]))}
        except Exception:
            return None
    info = await asyncio.to_thread(_probe)
    _GPU_CACHE["info"] = info
    _GPU_CACHE["ts"] = now
    return info

async def detect_gpu():
    info = await gpu_info()
    if not info:
        return {"auto": False, "reason": "nvidia-smi 不可用"}

def gpu_profile(g):
    v = g["vramTotal"]
    hq = v >= 28 * 1024  # 4080S 32G 实测 BF16 可跑通（~24.5G 峰值 + 编码器）
    maxLv = 2560 if v >= 30 * 1024 else (1440 if v >= 20 * 1024 else 1080)
    warn = ""
    if not hq:
        warn = f"当前显卡 {v // 1024}G 显存，BF16 高质量模式不可用，已切换为 INT8 快速"
    return {"auto": True, "gpu": g["name"], "vramGB": round(v / 1024, 1), "mode": "fast" if not hq else "auto",
            "maxLv": maxLv, "hq": hq, "warn": warn}

def check_oom(model, resLv, prof):
    peak = MODEL_VRAM_GB.get(model, 17.0)
    v = prof["vramGB"]
    if peak > v - 1.5:
        return True, f"当前显卡 {v}G 显存不足以运行 {model.upper()} 模式（约需 {peak}G），请切换模式"
    return False, None

# ================= 鉴权 =================
def check_auth(authorization: Optional[str], admin: bool = False):
    need = ADMIN_TOKEN if admin else TOKEN
    if not need:
        return
    if not authorization:
        raise HTTPException(401, "需要访问口令（Authorization: Bearer <口令>）")
    tok = authorization.replace("Bearer ", "", 1).strip()
    if tok != need:
        raise HTTPException(401, "访问口令错误")

# ================= 任务模型 =================
class GenerateReq(BaseModel):
    prompt: str = ""
    negative_prompt: str = ""          # 负面提示词（非必填，链接负面）
    model: str = "fast"                # fast=INT8 | hq=BF16
    ratio: str = "1:1"
    resLv: int = 0
    steps: int = 28
    cfg: float = 1.0                      # 与用户跑通工作流一致（Qwen-Image 官方 cfg=1）
    sampler: str = "euler"
    scheduler: str = "simple"
    denoise: float = 1.0
    seed: Optional[int] = None
    count: int = 1
    transparent: bool = False
    wf: str = "auto"                   # auto | t2i | t2i_opt | i2i | rmbg | upscale
    optimize: bool = False             # 提示词优化开关（无图时 → 文生图带优化）
    upscale: bool = False              # 高清放大开关（生成/编辑完成后追加放大；rmbg 除外）
    upscale_scale: int = 2             # 放大倍数（2/3/4 等）
    views: list = []
    refs: list = []                    # [{name,data,mask}]

# ================= 任务存储 =================
TASKS = {}
def _mk_task(req: GenerateReq):
    return {"id": uuid.uuid4().hex[:12], "req": req.model_dump(), "status": "queued", "pct": 0,
            "node": "排队中", "etaSec": None, "error": None, "resNote": None,
            "images": [], "sub": [], "startedAt": None, "finishedAt": None,
            "wf": "auto", "nodes": [], "createdAt": time.time(), "cancel": False}
def _task(tid):
    t = TASKS.get(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t
def _queue_info():
    return {"running": sum(1 for x in TASKS.values() if x["status"] == "running"),
            "queued": sum(1 for x in TASKS.values() if x["status"] == "queued"),
            "pct": max((x["pct"] for x in TASKS.values() if x["status"] == "running"), default=0)}
def _comfy_alive():
    import urllib.request
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=4):
            return True
    except Exception:
        return False
def _mem_gb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    t = int(line.split()[1]) // 1024 // 1024
                if line.startswith("MemAvailable"):
                    a = int(line.split()[1]) // 1024 // 1024
                    return {"totalGB": t, "usedGB": max(0, t - a), "pct": round(max(0, t - a) / t * 100)}
    except Exception:
        return None
def cur_res(ratio, resLv):
    w, h = RATIOS.get(ratio, RATIOS["1:1"])
    if resLv > 0:
        long = max(w, h)
        s = resLv / long
        w, h = max(256, round(w * s / 16) * 16), max(256, round(h * s / 16) * 16)
    return w, h

app = FastAPI(title="Qwen Image Studio")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ================= AI 提示词优化（AutoDL Art 大模型 API，OpenAI 兼容） =================
AIOPT_FILE = Path(os.environ.get("AIOPT_FILE", "/root/qwen-aiopt.json"))

# 接口地址封装：AutoDL Art 大模型 API 内部端点（经探测确认为 /api/v1，前端不可修改）
AIOPT_BASE_URL = "https://api.autodl.art/api/v1"

DEFAULT_AIOPT = {
    "base_url": AIOPT_BASE_URL,
    "api_key": "",
    "model": "DeepSeek-V4.1-Flash",
    "models": ["DeepSeek-V4.1-Flash", "gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra",
               "gpt-5.6-luna", "claude-fable-5", "claude-opus-4-8"],
    "token_url": "https://www.autodl.art/large-model/tokens",
    "skills_t2i": "",
    "skills_i2i": "",
}

DEFAULT_SKILLS_T2I = (
    "本助手是文生图提示词专家，为 Qwen-Image-2.1 图像生成模型优化提示词。\n"
    "输入：用户的一句话创意。输出：一段优化后的中文绘画提示词（直接可用的自然语言描述，不要编号、不要解释、不要引号包裹、不要返回其他内容）。\n"
    "要求：\n"
    "1. 用完整的自然语句描述画面——Qwen-Image-2.1 擅长理解整句描述而非标签堆砌；\n"
    "2. 依次覆盖：主体（人物/物体及其特征）、动作或状态、环境背景、构图视角、光线氛围、色彩基调、材质与细节、画质词；\n"
    "3. 画面元素明确无歧义，避免笼统形容（把「好看」具体为五官/装饰/色彩等细节）；\n"
    "4. 可适当加入风格词（电影质感、摄影写实、赛博朋克插画、3D 渲染等）与画质词（高清细节、锐利、质感）；\n"
    "5. 不要输出英文标签列表，不要输出任何负面词，不要改变用户创意的核心内容；\n"
    "6. 最终只输出优化后的提示词本身，长度控制在 80~200 字。"
)

DEFAULT_SKILLS_I2I = (
    "本助手是图生图编辑提示词专家，为 Qwen-Image-2.1 图像编辑模型优化编辑指令。\n"
    "输入：一张或多张按顺序排列的参考图片，以及用户的编辑意图。输出：一段优化后的中文编辑提示词（直接可用的自然语言指令，不要编号、不要解释、不要引号包裹、不要返回其他内容）。\n"
    "要求：\n"
    "1. 先明确「保留」什么：参考图中需要原样保留的主体造型、颜色、材质、纹理、构图与光影关系；\n"
    "2. 再明确「修改/新增」什么：把用户的编辑意图写成清晰的动作指令（例如「把 @图1 人物的衣服换成红色风衣」「在背景中加入…」）；\n"
    "3. 若涉及多张参考图，说明以哪张图为基准；\n"
    "4. 语言全句统一（中文或英文），整体自然连贯，不用标签堆砌；\n"
    "5. 不要输出负面词列表，不要改变用户编辑意图的核心；\n"
    "6. 最终只输出优化后的编辑提示词本身，长度控制在 80~200 字。"
)

_AIOPT_CACHE = None  # 内存缓存，避免每次读文件；文件是唯一持久来源

def _load_aiopt() -> dict:
    global _AIOPT_CACHE
    cfg = _AIOPT_CACHE
    if cfg is None:
        cfg = dict(DEFAULT_AIOPT)
        try:
            if AIOPT_FILE.exists():
                saved = json.loads(AIOPT_FILE.read_text("utf-8"))
                cfg.update({k: v for k, v in saved.items() if k in DEFAULT_AIOPT})
            # base_url 固定封装为 AutoDL Art 内部 API，不受配置文件旧值/脏值影响
            cfg["base_url"] = AIOPT_BASE_URL
            # 默认 skills 仅在用户尚未保存过时写入（首次启动生成默认文件）
            if not cfg.get("skills_t2i"):
                cfg["skills_t2i"] = DEFAULT_SKILLS_T2I
            if not cfg.get("skills_i2i"):
                cfg["skills_i2i"] = DEFAULT_SKILLS_I2I
            _save_aiopt(cfg)
        except Exception as e:
            print(f"[AIOPT] load err: {e}", flush=True)
        _AIOPT_CACHE = cfg
    return cfg

def _save_aiopt(cfg: dict):
    try:
        AIOPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        AIOPT_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
        return True
    except Exception as e:
        print(f"[AIOPT] save err: {e}", flush=True)
        return False

async def aiopt_chat(cfg: dict, system: str, user_parts: list, timeout: int = 60):
    """调用 AutoDL Art 大模型 API（OpenAI 兼容 /chat/completions）。
    user_parts: [{type:"text",text:...} | {type:"image_url",image_url:{url:dataURL}}]
    自动处理：content 为空时回退 reasoning_content，再失败自动重试一次。"""
    base = AIOPT_BASE_URL  # 封装地址，前端不可改
    url = base + "/chat/completions"
    payload = {
        "model": cfg.get("model") or "DeepSeek-V4.1-Flash",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_parts},
        ],
        "max_tokens": 1500,
        "temperature": 0.7,
    }
    headers = {"Content-Type": "application/json"}
    key = (cfg.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key

    async def _once(attempt: int):
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                body = await r.json(content_type=None)
                if r.status != 200:
                    raise RuntimeError(f"大模型 API {r.status}: {json.dumps(body, ensure_ascii=False)[:300]}")
        msg = (body.get("choices") or [{}])[0].get("message", {})
        content = (msg.get("content") or "").strip()
        if not content:
            # DeepSeek 系模型常把 token 花在 reasoning_content 上 → content 空
            reason = (msg.get("reasoning_content") or "").strip()
            if reason and attempt == 0:
                raise RuntimeError("EMPTY_CONTENT_RETRY")
            if reason:
                content = reason[-800:]
        if not content:
            raise RuntimeError("大模型返回为空")
        return content

    for attempt in range(2):
        try:
            return await _once(attempt)
        except RuntimeError as e:
            if str(e) == "EMPTY_CONTENT_RETRY" and attempt == 0:
                continue
            raise

class AiOptReq(BaseModel):
    prompt: str = ""                    # 要优化的原始提示词
    refs: list = []                     # i2i 时按顺序传参考图 [{data: dataURL}]
    mode: str = "auto"                  # auto|t2i|i2i（auto: 有图=图生图）

@app.get("/api/aiopt")
async def api_aiopt_get(authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    cfg = _load_aiopt()
    # 不回传完整密钥：仅告知是否已配置（前端留空 = 保持不变）
    return {
        "configured": bool((cfg.get("api_key") or "").strip()),
        "base_url": cfg.get("base_url"),
        "model": cfg.get("model"),
        "models": cfg.get("models"),
        "token_url": cfg.get("token_url"),
        "skills_t2i": cfg.get("skills_t2i"),
        "skills_i2i": cfg.get("skills_i2i"),
    }

@app.post("/api/aiopt/save")
async def api_aiopt_save(body: dict, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    cfg = _load_aiopt()
    # 接口地址已封装（固定 AutoDL Art 内部 API），前端不可修改；只接受令牌/模型/skills
    for k in ("model", "models", "token_url", "skills_t2i", "skills_i2i"):
        if k in body and body[k] is not None:
            cfg[k] = body[k]
    # 密钥：新值非空才覆盖；留空/省略 = 保持原值
    new_key = (body.get("api_key") or "").strip()
    if new_key:
        cfg["api_key"] = new_key
    ok = _save_aiopt(cfg)
    if not ok:
        raise HTTPException(500, "配置保存失败（文件写入错误）")
    _AIOPT_CACHE = cfg
    return {"ok": True, "configured": bool(cfg.get("api_key"))}

@app.post("/api/aiopt/reset-skills")
async def api_aiopt_reset_skills(body: dict = None, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    cfg = _load_aiopt()
    cfg["skills_t2i"] = DEFAULT_SKILLS_T2I
    cfg["skills_i2i"] = DEFAULT_SKILLS_I2I
    _save_aiopt(cfg)
    _AIOPT_CACHE = cfg
    return {"ok": True, "skills_t2i": DEFAULT_SKILLS_T2I, "skills_i2i": DEFAULT_SKILLS_I2I}

@app.post("/api/aiopt/test")
async def api_aiopt_test(body: dict = None, authorization: Optional[str] = Header(None)):
    """连通性测试：用当前配置发一条 3 字以内的最小请求。"""
    check_auth(authorization)
    cfg = dict(_load_aiopt())
    if (body or {}).get("api_key"):
        cfg["api_key"] = (body["api_key"] or "").strip()
    if not cfg.get("api_key"):
        raise HTTPException(400, "请先填写令牌（API Key）")
    system = "You are a helpful assistant. Reply with exactly: OK"
    try:
        out = await aiopt_chat(cfg, system, [{"type": "text", "text": "connectivity test"}], timeout=30)
        return {"ok": True, "reply": out[:50]}
    except Exception as e:
        msg = str(e)
        if "401" in msg or "Unauthorized" in msg or "Invalid authentication" in msg:
            raise HTTPException(502, "连接失败：令牌无效或已过期，请检查后在设置中重新填写令牌")
        if "429" in msg or "overloaded" in msg:
            raise HTTPException(502, "连接失败：模型当前繁忙（429），请稍后重试或换用其他模型")
        raise HTTPException(502, f"连接失败: {e}")

@app.post("/api/aiopt/optimize")
async def api_aiopt_optimize(req: AiOptReq, authorization: Optional[str] = Header(None)):
    """优化提示词：文生图只发文本；图生图按顺序把参考图发给 AI。
    只返回正面提示词（不生成负面）。"""
    check_auth(authorization)
    cfg = _load_aiopt()
    if not cfg.get("api_key"):
        raise HTTPException(400, "尚未配置大模型令牌，请先在设置中填写 API Key")
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "提示词为空")
    has_img = len(req.refs) > 0
    mode = req.mode if req.mode in ("t2i", "i2i") else ("i2i" if has_img else "t2i")
    if mode == "i2i":
        system = cfg.get("skills_i2i") or DEFAULT_SKILLS_I2I
    else:
        system = cfg.get("skills_t2i") or DEFAULT_SKILLS_T2I
    user_parts = [{"type": "text", "text": prompt}]
    if has_img:
        for i, ref in enumerate(req.refs[:10]):
            data = (ref.get("data") or "").strip()
            if not data:
                continue
            if not data.startswith("data:"):
                data = "data:image/png;base64," + data
            user_parts.append({"type": "image_url",
                               "image_url": {"url": data}})
        user_parts.append({"type": "text", "text": (
            f"\n以上是 {len(req.refs[:10])} 张按顺序排列的参考图。请基于它们优化上述编辑提示词，"
            "保留主体特征与光影一致性，仅返回优化后的正面提示词。" if len(req.refs[:10]) > 1 else
            "\n以上是参考图。请基于它优化上述编辑提示词，保留主体特征与光影一致性，仅返回优化后的正面提示词。")})
    try:
        out = await aiopt_chat(cfg, system, user_parts)
    except Exception as e:
        msg = str(e)
        if "sensitive_words_detected" in msg or "safety guidelines" in msg:
            raise HTTPException(502, "优化被大模型平台拦截：内容命中其安全过滤规则（该平台对部分中文常用字如「你/站/看/女人/男人」等较为敏感）。请尝试改写提示词用词，或换用其他模型后重试。")
        raise HTTPException(502, f"优化请求失败: {e}")
    # 清理可能的多余包裹（引号/编号/前缀标签）
    out = out.strip()
    out = re.sub(r'^["\'“”]+|["\'“”]+$', "", out).strip()
    out = re.sub(r'^(优化(后|的)?提示词[:：]?|提示词[:：]|Optimized prompt[:：]?)\s*', "", out, flags=re.I).strip()
    if not out:
        raise HTTPException(502, "大模型返回为空")
    return {"ok": True, "mode": mode, "prompt": out, "skill": "i2i" if mode == "i2i" else "t2i"}

# ================= 参考图保存 =================
def _save_ref(req: GenerateReq, task_id: str):
    """保存参考图与蒙版（分离存储）：
    - 参考图原样保存（局部修图时蒙版不再合成进参考图，避免破坏画面）
    - 蒙版单独存为 {task_id}_m{i}.png，供 SetLatentNoiseMask 局部重绘
    返回 (refs, masks)：refs=[文件名...]，masks=[蒙版文件名...] 或空列表"""
    saved, masks = [], []
    for i, ref in enumerate(req.refs):
        try:
            data = ref.get("data", "")
            if data.startswith("data:"):
                data = data.split(",", 1)[1] if "," in data else data
            raw = base64.b64decode(data)
            name = f"{task_id}_r{i}.png"
            (COMFY_INPUT / name).write_bytes(raw)
            saved.append(name)
            mdata = ref.get("mask") or ""
            if mdata:
                if mdata.startswith("data:"):
                    mdata = mdata.split(",", 1)[1] if "," in mdata else mdata
                mraw = base64.b64decode(mdata)
                mname = f"{task_id}_m{i}.png"
                # 归一化蒙版：画过区域=白(255)、未画=黑(0) 的 RGB PNG。
                # 前端画笔输出是「透明底+橙色笔画」，alpha 通道即笔画区域；
                # ComfyUI LoadImage 会丢 alpha，故先转成 RGB 黑白图再保存。
                # 关键：做「膨胀」——Qwen VAE 空间降采样 16x，小面积笔画缩到 latent
                # 空间只有 1~2 像素，若不膨胀会在采样时被 round() 丢弃，导致局部修图完全不生效。
                try:
                    from PIL import Image as _PImage, ImageFilter as _PF
                    import io as _pio
                    mimg = _PImage.open(_pio.BytesIO(mraw)).convert("RGBA")
                    alpha = mimg.split()[3]
                    mbw = _PImage.new("L", mimg.size, 0)
                    mbw.paste(255, (0, 0), alpha)
                    # 膨胀：半径随图片尺寸自适应。Qwen VAE 空间降采样 16x，
                    # latent 空间 1px ≈ 图上 16px，小笔刷区域不膨胀会在生成时被
                    # 参考图保真主导而看不到重绘效果。半径 ≈ 短边/128（至少 5px）。
                    from PIL import ImageFilter as _PF2
                    _r = max(5, min(mimg.size) // 128)
                    if _r % 2 == 0:
                        _r += 1
                    mbw = mbw.filter(_PF2.MaxFilter(_r))
                    mbw = mbw.convert("RGB")
                    mbuf = _pio.BytesIO(); mbw.save(mbuf, "PNG")
                    (COMFY_INPUT / mname).write_bytes(mbuf.getvalue())
                except Exception:
                    (COMFY_INPUT / mname).write_bytes(mraw)
                masks.append(mname)
        except Exception as e:
            raise HTTPException(400, f"参考图 {i + 1} 解码失败: {e}")
    return saved, masks

# ================= ComfyUI 工作流（4 条绑定链路 + 局部修图） =================
def build_workflow(req: GenerateReq, refs_saved: list, seed: int, w: int, h: int, task_id: str,
                   prompt_override: str = None, force_i2i: bool = None, wf: str = "auto",
                   masks_saved: list = None):
    """构造 API 工作流。wf 绑定：t2i / t2i_opt / i2i / rmbg；带蒙版时走局部重绘（inpaint）。
    返回 (nodes, is_i2i, wf_used)。"""
    prompt = prompt_override if prompt_override is not None else req.prompt
    m = MODELS.get(req.model, MODELS["fast"])
    negative = (req.negative_prompt or "").strip() or "text, watermark, low quality, blurry"
    is_i2i = force_i2i if force_i2i is not None else len(refs_saved) > 0
    masks = masks_saved or []
    has_mask = len(masks) > 0

    # ---- 工作流决策 ----
    if wf == "auto":
        if req.transparent and len(refs_saved) > 0:
            wf = "rmbg"
        elif len(refs_saved) > 0:
            wf = "i2i"
        elif req.optimize:
            wf = "t2i_opt"
        else:
            wf = "t2i"
    elif wf == "rmbg" and len(refs_saved) == 0:
        wf = "t2i"  # 抠图但无图 → 退回文生图

    # 透明背景：内置抠图提示词（用户填写的提示词一律忽略，避免浪费——移除背景只需抠图，不参与画面生成）
    if req.transparent or wf == "rmbg":
        prompt = "Remove the background, output a PNG image with transparent background, isolated subject, clear alpha channel, no background, PNG"

    nodes = {}
    # ---- 加载器 ----
    # 所有 Qwen-Image 2.1 工作流的 TextEncodeQwenImage21 都使用 qwen3vl CLIP 做条件编码
    # （与用户跑通的 文生图.json 一致：CLIPLoader qwen3vl_8b_int8_convrot -> TextEncode）
    # PE t2i CLIP（TE_T2I）仅用于「文生图带优化」的 TextGenerateLTX2Prompt 提示词扩展。
    if wf == "t2i_opt":
        # 文生图带优化：PE t2i CLIP 给提示词优化；qwen3vl 做提示词编码
        nodes["1"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": TE_T2I, "type": "qwen_image"}}
        nodes["1b"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": TE_VL, "type": "qwen_image"}}
        nodes["2"] = {"class_type": "UNETLoader", "inputs": {"unet_name": m["file"], "weight_dtype": "default"}}
        nodes["3"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}
        nodes["10"] = {"class_type": "TextGenerateLTX2Prompt", "inputs": {
            "clip": ["1", 0],
            "prompt": prompt,
            "max_length": 512,
            # DynamicCombo：主输入为字符串 "on"，子参数用 "sampling_mode.xxx" 前缀键
            "sampling_mode": "on",
            "sampling_mode.temperature": 0.7,
            "sampling_mode.top_k": 64,
            "sampling_mode.top_p": 0.95,
            "sampling_mode.min_p": 0.05,
            "sampling_mode.repetition_penalty": 1.05,
            "sampling_mode.seed": seed,
            "sampling_mode.presence_penalty": 0.0,
            "thinking": False, "use_default_template": True, "mtp": "auto",
        }}
        enc_prompt = ["10", 0]
    else:
        # 文生图 / 图片编辑 / 移除背景：TextEncode 一律用 qwen3vl CLIP（与用户跑通工作流一致）
        nodes["1"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": TE_VL, "type": "qwen_image"}}
        nodes["2"] = {"class_type": "UNETLoader", "inputs": {"unet_name": m["file"], "weight_dtype": "default"}}
        nodes["3"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}
        enc_prompt = prompt

    # ---- 编码节点 ----
    if is_i2i or wf == "rmbg":
        load_ids = {}
        for i, fname in enumerate(refs_saved):
            nid = str(100 + i)
            load_ids[i] = nid
            nodes[nid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
        # 输入几张就启用几张：images.image_1..N
        # ComfyUI 0.37 Autogrow 输入必须用带前缀键 "images.image_1"（不能嵌套 dict），
        # 执行层会按 dynamic_paths 组装回 images={"image_1":...} 传入 execute。
        img_inputs = {}
        for i in load_ids:
            img_inputs["images.image_%d" % (i + 1)] = [load_ids[i], 0]
        nodes["5"] = {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["1", 0],
            "prompt": enc_prompt if isinstance(enc_prompt, str) else enc_prompt,
            "negative_prompt": negative,
            "resolution": 1280,   # 用户跑通的 图片编辑（细）.json 使用 1280：参考图缩放注入
            "vae": ["3", 0],
            **img_inputs,
        }}
        if has_mask and not (wf == "rmbg"):
            # ===== 局部修图（inpaint）：蒙版区域重绘，其余区域保持原图 =====
            # 链路：LoadImage(原图) → VAEEncode → latent
            #      LoadImage(蒙版) → ImageToMask → MASK
            #      SetLatentNoiseMask(latent, mask) → 仅蒙版区域加噪声重绘
            #      KSampler(denoise=1.0 在 masked latent 上采样) → 未蒙版区域原样保留
            nodes["8a"] = {"class_type": "VAEEncode", "inputs": {"pixels": [load_ids[0], 0], "vae": ["3", 0]}}
            # 蒙版图（用户画笔橙色笔画）→ 二值蒙版：ImageToMask 选 alpha 通道，
            # 画过=不透明=要修改；未画=透明=保持原样
            nodes["8b"] = {"class_type": "LoadImage", "inputs": {"image": masks[0]}}
            nodes["8c"] = {"class_type": "ImageToMask", "inputs": {"image": ["8b", 0], "channel": "red"}}
            nodes["8d"] = {"class_type": "SetLatentNoiseMask", "inputs": {
                "samples": ["8a", 0], "mask": ["8c", 0]}}
            latent = ["8d", 0]
        else:
            # KSampler latent 用 EmptyLatentImage（用户工作流 ComfySwitchNode switch=true 选中 on_true=EmptyLatentImage，
            # TextEncode 的 latent 输出并未使用——直接用会导致雪花/全零噪声）
            nodes["9"] = {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
            latent = ["9", 0]
    else:
        nodes["5"] = {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["1", 0],
            "prompt": enc_prompt if isinstance(enc_prompt, str) else enc_prompt,
            "negative_prompt": negative,
            "resolution": 1024,   # 用户跑通的 文生图.json 子图 TextEncode resolution=1024
        }}
        nodes["9"] = {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
        latent = ["9", 0]

    # KSampler：局部修图 denoise=1.0 走 masked latent（SetLatentNoiseMask 已隔离蒙版区域，
    # 未蒙版区域 latent 原样通过，因此 denoise 不会破坏保持区）；其余模式用请求的 denoise。
    denoise = 1.0 if has_mask and not (wf == "rmbg") else min(1.0, max(0.0, req.denoise))
    nodes["6"] = {"class_type": "KSampler", "inputs": {
        "model": ["2", 0], "positive": ["5", 0], "negative": ["5", 1], "latent_image": latent,
        "seed": seed, "steps": max(1, req.steps), "cfg": max(0.0, req.cfg),
        "sampler_name": req.sampler, "scheduler": req.scheduler, "denoise": denoise,
    }}
    nodes["7"] = {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}}
    nodes["8"] = {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": f"qw_{task_id}_{seed}"}}
    return nodes, is_i2i, wf

# ================= 高清放大（ComfyUI-VOSR2：VOSR 2.0 one-step 1.4B） =================
# 节点：LoadImage -> VOSR2ModelLoader -> VOSR2Upscale -> SaveImage
UPSCALE_WF_STEPS = [("2", "放大模型加载"), ("3", "高清放大"), ("4", "保存放大图")]

def build_upscale_workflow(image_name: str, scale: int, seed: int, task_id: str, idx: int = 0):
    """参考图/生成图直接放大工作流（comfy API 格式）。
    image_name 为 ComfyUI input 目录下的文件名。"""
    prefix = f"qwup_{task_id}_{idx}"
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": "VOSR2ModelLoader", "inputs": {"model": "VOSR2", "dtype": "default"}},
        "3": {"class_type": "VOSR2Upscale", "inputs": {
            "model": ["2", 0], "image": ["1", 0],
            "upscale": max(1, min(16, int(scale))), "seed": seed,
            "color_alignment": "wavelet",
            "tile_size": 512, "tile_overlap": 32,
            "vae_tile_size": 1024, "vae_tile_overlap": 32,
        }},
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": prefix}},
    }

async def _run_upscale(t: dict, sub: dict, image_name: str, scale: int, seed: int, idx: int) -> bool:
    """执行一次放大。成功返回 True（sub['file'] 指向放大图）。"""
    nodes = build_upscale_workflow(image_name, scale, seed, t["id"], idx)
    client_id = uuid.uuid4().hex
    # 进度阶段简单推进（放大模型加载 -> 高清放大 -> 保存）
    async def _ws():
        try:
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(f"ws://{COMFY_URL.split('//')[1]}/ws?clientId={client_id}",
                                        timeout=aiohttp.ClientTimeout(total=30)) as ws:
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            evt = json.loads(msg.data)
                        except Exception:
                            continue
                        if evt.get("type") == "executing":
                            nid = evt.get("data", {}).get("node")
                            st = dict(UPSCALE_WF_STEPS)
                            if nid in st:
                                t["node"] = st[nid]
                                t["pct"] = max(t["pct"], 90 + int(list(st.keys()).index(nid) * 3))
                        elif evt.get("type") == "progress":
                            val, mx = evt.get("data", {}).get("value", 0), evt.get("data", {}).get("max", 1)
                            if mx:
                                sub["progress"] = int(val / mx * 100)
                                t["pct"] = max(t["pct"], 93 + int(sub["progress"] * 5 / 100))
        except Exception:
            pass
    ws_task = asyncio.create_task(_ws())
    try:
        r = await comfy_post({"prompt": nodes, "client_id": client_id})
        sub["comfy_id"] = r.get("prompt_id")
    except Exception as e:
        ws_task.cancel()
        t["error"] = f"放大提交失败: {e}"
        return False
    ok, err = await wait_comfy(sub["comfy_id"], sub, t)
    ws_task.cancel()
    if not ok:
        t["error"] = f"放大失败: {err}"
        return False
    return True

# ================= ComfyUI 交互 =================
async def comfy_post(data: dict):
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{COMFY_URL}/prompt", json=data, timeout=aiohttp.ClientTimeout(total=30)) as r:
            body = await r.json()
            if r.status != 200:
                raise RuntimeError(f"ComfyUI 拒绝: {body}")
            if body.get("node_errors"):
                raise RuntimeError(f"ComfyUI 节点校验失败: {json.dumps(body['node_errors'], ensure_ascii=False)[:800]}")
            if not body.get("prompt_id"):
                raise RuntimeError(f"ComfyUI 未返回 prompt_id: {body}")
            return body

async def comfy_get_json(path: str):
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{COMFY_URL}{path}", timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return None
            return await r.json(content_type=None)

async def comfy_interrupt():
    """中断 ComfyUI 当前正在执行的 prompt（网页端取消 → 同步取消 GPU 生成）。"""
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{COMFY_URL}/interrupt", timeout=aiohttp.ClientTimeout(total=10)) as r:
            return r.status == 200

async def comfy_delete_queue(ids: list):
    """从 ComfyUI 队列删除（含排队中/已提交未开始）的 prompt。"""
    if not ids:
        return
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{COMFY_URL}/queue", json={"delete": ids},
                          timeout=aiohttp.ClientTimeout(total=10)) as r:
            return r.status == 200

async def wait_comfy(comfy_id: str, sub: dict, t: dict = None):
    if not comfy_id:
        return False, "ComfyUI 未返回任务 ID（提交失败）"
    deadline = time.time() + 900
    t0 = time.time()
    fail_streak = 0
    while time.time() < deadline:
        if t and t.get("cancel"):
            return False, "已取消"
        try:
            h = await comfy_get_json(f"/history/{comfy_id}")
        except Exception:
            # ComfyUI 失联（进程重启/网络断开）：连续失败即快速报错，不再空等
            fail_streak += 1
            if fail_streak >= 4 and time.time() - t0 > 20:
                return False, "ComfyUI 无响应（连接失败），任务已停止"
            await asyncio.sleep(1.5)
            continue
        fail_streak = 0
        if h and comfy_id in h:
            st = h[comfy_id].get("status", {})
            if st.get("status_str") == "success":
                for o in h[comfy_id].get("outputs", {}).values():
                    for im in o.get("images", []):
                        sub["file"] = os.path.join(COMFY_OUTPUT, im.get("subfolder", ""), im["filename"])
                        break
                return True, None
            if st.get("status_str") == "error":
                msg = ""
                for m in st.get("messages", []):
                    if m[0] == "execution_error":
                        msg = json.dumps(m[1], ensure_ascii=False)[:800]
                return False, msg or "ComfyUI 执行错误"
        await asyncio.sleep(1.5)
    return False, "生成超时（15 分钟）"

# ================= 执行任务（串行） =================
_SEM = asyncio.Semaphore(1)

async def run_task(t: dict):
    req = GenerateReq(**t["req"])
    async with _SEM:
        t["status"] = "running"
        t["startedAt"] = time.time()
        w, h = cur_res(req.ratio, req.resLv)
        note = None
        if req.resLv >= 4096 and max(w, h) > 2752:
            note = "4K 实验档：官方原生上限 2752px 长边，服务端已降级"
            scale = 2752 / max(w, h)
            w, h = max(256, round(w * scale / 16) * 16), max(256, round(h * scale / 16) * 16)
        t["resNote"] = note
        try:
            refs_saved, masks_saved = _save_ref(req, t["id"]) if req.refs else ([], [])
            wf = req.wf
            if wf == "auto":
                if req.transparent and len(refs_saved) > 0:
                    wf = "rmbg"
                elif len(refs_saved) > 0:
                    wf = "i2i"
                elif req.optimize:
                    wf = "t2i_opt"
                else:
                    wf = "t2i"
            t["wf"] = wf
            t["nodes"] = [{"id": nid, "name": name, "status": "pending", "pct": 0} for nid, name in WF_STEPS[wf]]

            # 参考图直接放大（快捷创作）：每张参考图依次放大（每张独立任务）
            if wf == "upscale":
                for i, fname in enumerate(refs_saved):
                    if t.get("cancel"):
                        t["status"] = "cancelled"
                        return
                    seed = (req.seed if req.seed is not None else int(time.time() * 1000) % 900000000 + 100000000) + i
                    sub = {"seed": seed, "comfy_id": None, "file": None, "progress": 0, "view": None}
                    t["sub"].append(sub)
                    t["pct"] = 2 + int(i / len(refs_saved) * 90)
                    t["node"] = "高清放大"
                    if not await _run_upscale(t, sub, fname, req.upscale_scale, seed, i):
                        t["status"] = "failed"
                        t["error"] = t.get("error") or "放大失败"
                        return
                    if sub["file"] and os.path.exists(sub["file"]):
                        raw = Path(sub["file"]).read_bytes()
                        t["images"].append({"n": len(t["images"]), "seed": sub["seed"], "view": sub.get("view"),
                                            "data_url": "data:image/png;base64," + base64.b64encode(raw).decode()})
                t["pct"] = 100
                t["status"] = "done"
                return

            # rmbg 多图：依次提交每张图抠图（每张独立一次生成）
            if wf == "rmbg" and len(refs_saved) > 1:
                for i, fname in enumerate(refs_saved):
                    if t.get("cancel"):
                        t["status"] = "cancelled"
                        return
                    seed = (req.seed if req.seed is not None else int(time.time() * 1000) % 900000000 + 100000000) + i
                    sub = {"seed": seed, "comfy_id": None, "file": None, "progress": 0, "view": None}
                    t["sub"].append(sub)
                    t["pct"] = 2 + int(i / len(refs_saved) * 90)
                    nodes, _i2i, _wf = build_workflow(req, [fname], seed, w, h, t["id"], wf="rmbg")
                    if not await _run_one(t, sub, nodes, _wf, wf):
                        return  # 失败/取消：整任务停止，前端立即看到 failed
                t["pct"] = 100
                t["status"] = "done"
                return

            # 常规：多视角每视角一个提示词；count 张 × 视角数 = 总张数
            prompts = list(req.views) if req.views else [req.prompt]
            total_pics = len(prompts) * max(1, req.count)
            seeds = []
            if req.seed is not None:
                seeds = [req.seed + i for i in range(total_pics)]
            else:
                seeds = [int(time.time() * 1000) % 900000000 + 100000000 + i for i in range(total_pics)]
            pic_i = 0
            for pi, ptext in enumerate(prompts):
                for ci in range(max(1, req.count)):
                    if t.get("cancel"):
                        t["status"] = "cancelled"
                        return
                    seed = seeds[pic_i]
                    sub = {"seed": seed, "comfy_id": None, "file": None, "progress": 0,
                           "view": pi if req.views else None}
                    t["sub"].append(sub)
                    t["pct"] = 2 + int(pic_i / total_pics * 90)
                    nodes, _i2i, _wf = build_workflow(req, refs_saved, seed, w, h, t["id"],
                                                      prompt_override=ptext, wf=wf,
                                                      masks_saved=masks_saved if wf != "rmbg" else None)
                    if not await _run_one(t, sub, nodes, _i2i, wf):
                        return  # 失败/取消：整任务停止，前端立即看到 failed
                    pic_i += 1
            # 生成完成后：若开启高清放大（且非抠图），对每张生成图追加放大
            if req.upscale and wf != "rmbg" and t["sub"] and len(t["images"]) > 0:
                orig_imgs = [dict(im) for im in t["images"]]
                t["images"] = []
                for i, sub in enumerate(t["sub"]):
                    if not sub.get("file") or not os.path.exists(sub["file"]):
                        if i < len(orig_imgs):
                            t["images"].append(orig_imgs[i])
                        continue
                    # 复制生成图到 ComfyUI input 供 LoadImage 使用
                    up_name = f"{t['id']}_up{i}.png"
                    try:
                        shutil.copy(sub["file"], COMFY_INPUT / up_name)
                    except Exception as e:
                        t["error"] = f"放大准备失败: {e}"
                        break
                    sub2 = {"seed": sub["seed"], "comfy_id": None, "file": None, "progress": 0, "view": sub.get("view")}
                    t["pct"] = 90 + int(i / max(1, len(t["sub"])) * 10)
                    t["node"] = "高清放大"
                    if not await _run_upscale(t, sub2, up_name, req.upscale_scale, sub["seed"], i):
                        # 放大失败：保留原图，不阻断任务完成
                        if i < len(orig_imgs):
                            t["images"].append(orig_imgs[i])
                        t["resNote"] = (t["resNote"] or "") + (" " if t["resNote"] else "") + f"第{i+1}张放大失败：{t.get('error') or '未知错误'}"
                        continue
                    if sub2["file"] and os.path.exists(sub2["file"]):
                        raw = Path(sub2["file"]).read_bytes()
                        t["images"].append({"n": len(t["images"]), "seed": sub["seed"], "view": sub.get("view"),
                                            "data_url": "data:image/png;base64," + base64.b64encode(raw).decode()})
            t["pct"] = 100
            t["status"] = "done"
        except Exception as e:
            t["status"] = "failed"
            t["error"] = str(e)[:500]
        finally:
            t["finishedAt"] = time.time()

async def _run_one(t: dict, sub: dict, nodes: dict, is_i2i: bool, wf: str) -> bool:
    """提交并等待单个生成。失败返回 False（已设置 t['status']）。"""
    client_id = uuid.uuid4().hex
    ws_task = asyncio.create_task(comfy_ws_watch(client_id, t, sub, nodes, wf))
    try:
        r = await comfy_post({"prompt": nodes, "client_id": client_id})
        sub["comfy_id"] = r.get("prompt_id")
    except Exception as e:
        ws_task.cancel()
        t["status"] = "failed"
        t["error"] = f"提交失败: {e}"
        return False
    ok, err = await wait_comfy(sub["comfy_id"], sub, t)
    ws_task.cancel()
    if not ok:
        if err == "已取消":
            t["status"] = "cancelled"
        else:
            t["status"] = "failed"
            t["error"] = err
        return False
    if sub["file"] and os.path.exists(sub["file"]):
        raw = Path(sub["file"]).read_bytes()
        t["images"].append({"n": len(t["images"]), "seed": sub["seed"], "view": sub.get("view"),
                            "data_url": "data:image/png;base64," + base64.b64encode(raw).decode()})
    return True

async def comfy_ws_watch(client_id: str, t: dict, sub: dict, nodes: dict, wf: str):
    """订阅 ComfyUI websocket，更新节点级进度（每个节点的状态与采样进度）。"""
    ws_url = f"ws://{COMFY_URL.split('//')[1]}/ws?clientId={client_id}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=30)) as ws:
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        evt = json.loads(msg.data)
                    except Exception:
                        continue
                    etype = evt.get("type")
                    data = evt.get("data", {})
                    if etype == "executing":
                        node = data.get("node")
                        _mark_node(t, node, "running")
                    elif etype == "progress":
                        val, mx = data.get("value", 0), data.get("max", 1)
                        if mx:
                            sub["progress"] = int(val / mx * 100)
                            _mark_progress(t, node_of_progress(t), sub)
                    if t["startedAt"] and t["pct"] > 5:
                        el = time.time() - t["startedAt"]
                        t["etaSec"] = int(el / t["pct"] * (100 - t["pct"])) if t["pct"] > 0 else None
    except Exception:
        pass

def node_of_progress(t: dict):
    """当前正在采样的节点（KSampler id=6）。"""
    return "6"

def _mark_node(t: dict, node_id, status):
    if node_id is None:
        return
    nid = str(node_id)
    for n in t.get("nodes", []):
        if n["id"] == nid:
            n["status"] = status
    # 节点运行中，映射总进度
    st = WF_STEPS.get(t.get("wf", "t2i"), [])
    idx = next((i for i, (a, _b) in enumerate(st) if a == nid), None)
    if idx is not None:
        base = 2 + int(idx / max(1, len(st)) * 80)
        t["pct"] = max(t["pct"], base)
        t["node"] = dict(st)[nid] if False else next((b for a, b in st if a == nid), "生成中")
        # 标记之前的节点为完成
        for i2, (a, _b) in enumerate(st):
            if i2 < idx:
                for n in t.get("nodes", []):
                    if n["id"] == a and n["status"] != "done":
                        n["status"] = "done"

def _mark_progress(t: dict, node_id, sub):
    """采样进度：45~85 区间 + 当前采样节点进度。"""
    p = sub.get("progress", 0)
    t["pct"] = max(t["pct"], 45 + int(p * 40 / 100))
    t["node"] = "采样生成"
    for n in t.get("nodes", []):
        if n["id"] == node_id:
            n["pct"] = p
            n["status"] = "running"

# ================= 启动后台 worker =================
QUEUE: Optional[asyncio.Queue] = None
_WORKER_TASK = None

async def worker_loop():
    print("[WORKER] started", flush=True)
    while True:
        t = await QUEUE.get()
        print(f"[WORKER] got task {t['id']}", flush=True)
        try:
            if t.get("cancel"):   # 排队期间已被取消：直接跳过，不启动生成
                t["status"] = "cancelled"
            else:
                await run_task(t)
        except Exception as e:
            t["status"] = "failed"
            t["error"] = str(e)[:500]
        finally:
            QUEUE.task_done()

@app.on_event("startup")
async def _start():
    global QUEUE, _WORKER_TASK
    QUEUE = asyncio.Queue()
    _WORKER_TASK = asyncio.create_task(worker_loop())
    _WORKER_TASK.add_done_callback(lambda ft: print(f"[WORKER] DONE? exc={ft.exception()}", flush=True))
    prof = await detect_gpu()
    print(f"[GPU] {json.dumps(prof, ensure_ascii=False)}", flush=True)

# ================= 路由 =================
@app.get("/api/ping")
async def api_ping():
    g = await gpu_info()
    prof = gpu_profile(g) if g else {"auto": False, "gpu": "unknown", "vramGB": 0, "mode": "fast", "maxLv": 1080, "hq": True, "warn": ""}
    return {"ok": True, "name": "Qwen Image Studio", "model": MODELS,
            "ratios": {k: list(v) for k, v in RATIOS.items()},
            "gpu": prof, "comfy": _comfy_alive(),
            "wfs": {"t2i": "文生图", "t2i_opt": "文生图带优化", "i2i": "图片编辑（细）", "rmbg": "移除背景"}}

@app.get("/api/stats")
async def api_stats(authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    g = await gpu_info()
    prof = gpu_profile(g) if g else None
    mem = await asyncio.to_thread(_mem_gb)
    return {
        "ts": time.time(), "gpu": g, "gpuProfile": prof,
        "mem": mem, "comfy": await asyncio.to_thread(_comfy_alive),
        "queue": _queue_info(),
        "load": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
    }

@app.post("/api/generate")
async def api_generate(req: GenerateReq, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    if len(req.refs) > 10:
        raise HTTPException(400, "参考图最多 10 张")
    if req.count not in (1, 2, 4):
        raise HTTPException(400, "count 仅支持 1|2|4")
    if len(req.views) > 12:
        raise HTTPException(400, "多视角最多 12 个视角")
    if req.ratio not in RATIOS:
        raise HTTPException(400, f"ratio 无效，可选 {list(RATIOS)}")
    if req.wf not in ("auto", "t2i", "t2i_opt", "i2i", "rmbg", "upscale"):
        raise HTTPException(400, "wf 无效，可选 auto|t2i|t2i_opt|i2i|rmbg|upscale")
    # 移除背景必须带图
    if req.wf == "rmbg" and not req.refs:
        raise HTTPException(400, "移除背景模式需要至少 1 张参考图")
    # 参考图直接放大必须带图
    if req.wf == "upscale" and not req.refs:
        raise HTTPException(400, "放大模式需要至少 1 张参考图")
    # 放大倍数限制
    if req.upscale_scale < 1 or req.upscale_scale > 16:
        raise HTTPException(400, "放大倍数需在 1~16 之间")
    g = await gpu_info()
    if g:
        prof = gpu_profile(g)
        if req.model == "hq" and not prof["hq"]:
            raise HTTPException(400, f"当前显卡 {prof['gpu']}（{prof['vramGB']}GB）不支持 BF16 高质量模式，请改用「快速生成」")
        if req.resLv > prof["maxLv"]:
            raise HTTPException(400, f"当前显卡 {prof['gpu']}（{prof['vramGB']}GB）分辨率档位最高 {prof['maxLv']}（{prof['warn']}）")
        hard, oom = check_oom(req.model, req.resLv, prof)
        if hard:
            raise HTTPException(400, oom)
    if QUEUE.qsize() >= MAX_QUEUE:
        raise HTTPException(429, "队列已满，请稍后再试")
    t = _mk_task(req)
    soft = None
    if g:
        _h, soft = check_oom(req.model, req.resLv, gpu_profile(g))
        if soft:
            t["resNote"] = soft
    TASKS[t["id"]] = t
    await QUEUE.put(t)
    return {"taskId": t["id"], "status": "queued", "queue": QUEUE.qsize(), "resNote": soft}

@app.get("/api/task/{task_id}")
async def api_task(task_id: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    t = _task(task_id)
    return {
        "taskId": t["id"],
        "status": t["status"],
        "pct": t["pct"],
        "node": t["node"],
        "etaSec": t["etaSec"],
        "queue": QUEUE.qsize(),
        "error": t["error"],
        "resNote": t.get("resNote"),
        "wf": t.get("wf"),
        "nodes": t.get("nodes", []),
        "images": [{"n": im["n"], "seed": im["seed"], "view": im.get("view")} for im in t["images"]],
    }

@app.post("/api/cancel/{task_id}")
async def api_cancel(task_id: str, authorization: Optional[str] = Header(None)):
    """取消任务：标记取消 → 中断 ComfyUI 当前执行 → 删除尚未开始的排队 prompt（前后端同步取消）"""
    check_auth(authorization)
    t = _task(task_id)
    if t["status"] in ("done", "failed", "cancelled"):
        return {"status": t["status"], "cancelled": False}
    t["cancel"] = True
    t["node"] = "正在取消…"
    # 中断 ComfyUI 当前正在执行的 prompt（GPU 立即停下）
    try:
        await comfy_interrupt()
    except Exception:
        pass
    # 删除本任务已提交但尚未开始执行的 prompt（多视角/多图场景残留队列）
    ids = [s.get("comfy_id") for s in t.get("sub", []) if s.get("comfy_id")]
    try:
        await comfy_delete_queue(ids)
    except Exception:
        pass
    t["status"] = "cancelled"
    t["error"] = None
    return {"status": "cancelled", "cancelled": True}

@app.get("/api/image/{task_id}")
async def api_image(task_id: str, n: int = Query(0), authorization: Optional[str] = Header(None),
                    token: str = Query(None), thumb: int = Query(0)):
    """图片直链：支持 Authorization: Bearer 或 ?token= 两种鉴权（?token= 便于 <img> 标签懒加载直链）。
    thumb=1 返回 WEBP 缩略图（历史/相册预览用，省内存省流量）；默认返回原图（灯箱/下载用）。
    带 Cache-Control 让浏览器缓存，避免多图场景下重复下载卡顿。"""
    try:
        check_auth(authorization)
    except HTTPException:
        if not token or not TOKEN or token != TOKEN:
            raise HTTPException(401, "需要访问口令")
    t = _task(task_id)
    if t["status"] != "done":
        raise HTTPException(409, "任务尚未完成")
    if n < 0 or n >= len(t["images"]):
        raise HTTPException(404, "图片序号不存在")
    raw = base64.b64decode(t["images"][n]["data_url"].split(",", 1)[1])
    if thumb:
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(raw))
            img.thumbnail((512, 512), Image.LANCZOS)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "WEBP", quality=82, method=4)
            return Response(content=buf.getvalue(), media_type="image/webp",
                            headers={"Cache-Control": "public, max-age=86400, immutable"})
        except Exception:
            pass  # 缩略图失败则回退原图
    return Response(content=raw, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400, immutable"})

@app.post("/api/restart")
async def api_restart(authorization: Optional[str] = Header(None)):
    check_auth(authorization, admin=True)
    await asyncio.to_thread(_restart_comfy)
    return {"status": "restarted"}

def _restart_comfy():
    try:
        subprocess.run(["pkill", "-f", "main.py --port 6006"], timeout=10)
    except Exception:
        pass
    time.sleep(3)
    env = os.environ.copy()
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    subprocess.Popen(
        ["/root/miniconda3/bin/python", "/root/ComfyUI/main.py", "--port", "6006", "--listen", "0.0.0.0", "--disable-auto-launch"],
        cwd="/root/ComfyUI", env=env,
        stdout=open("/root/comfyui.log", "a"), stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        time.sleep(1)
        try:
            with urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=3):
                return
        except Exception:
            pass
    raise RuntimeError("ComfyUI 重启超时")

if __name__ == "__main__":
    import urllib.request
    import uvicorn
    from fastapi.staticfiles import StaticFiles
    web_dir = os.environ.get("WEB_DIR", "/root/qwen-web")
    if os.path.isdir(web_dir):
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    port = int(os.environ.get("PORT", "6008"))
    print(f"Qwen Image Studio API on :{port}  (token={TOKEN})")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
