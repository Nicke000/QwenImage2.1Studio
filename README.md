# Qwen Image Studio · Qwen-Image-2.1 图像创作工作台

面向公众使用的 **Qwen-Image-2.1** 图像创作应用：网页工作台 + FastAPI 后端 + ComfyUI 推理，
随 AutoDL 自定义镜像一起发布，用户克隆实例后 **开机即用、零配置**。

- 🎨 文生图 / 图生图 / 局部重绘（画笔蒙版）/ 移除背景（透明 PNG）
- 🌐 全景摄影、多视角多图、人物三视图、快速换装、人物替换等快捷创作
- 🤖 AI 提示词优化（接入 AutoDL Art 大模型 API，用户填自己的令牌）
- 🔍 高清放大（ComfyUI-VOSR2 节点，2×/3×/4×，可在出图后自动追加）
- 🖼️ 创作记录灯箱（右侧多图缩略图切换、滚轮缩放、以种子再生成、参数回填）
- ⚡ 按显卡显存自动适配（模式/分辨率裁剪 + 防爆显存校验）

---

## 目录结构

```
.
├── server.py                    # FastAPI 后端（网页 + API 同源，监听 6008）
├── web/
│   ├── index.html               # 前端单文件工作台
│   └── favicon.svg
├── workflows/                   # ComfyUI 工作流（文生图/图生图/抠图/放大…）
├── scripts/
│   ├── start-all.sh             # 启动：模型软链接自愈 → ComfyUI → FastAPI
│   ├── autodl.sh                # 开机自启钩子 → 安装为 /etc/autodl.sh（AutoDL 官方机制）
│   ├── prepublish.sh            # 保存镜像前清理（个人图片 / 日志 / 令牌）
│   ├── rc.local                 # 兼容保留（部分镜像环境会执行 rc.local）
│   └── qwen-studio.service      # 有 systemd 的环境可选用的单元文件
├── tools/
│   ├── check_env.py             # 环境自检（依赖 / 模型软链接 / 工作流 / 端口）
│   └── fetch_models.py          # 模型下载工具（公共模型库不可用时的兜底）
└── docs/
    ├── AUTODL-镜像发布指南.md    # 镜像发布与克隆用户使用说明
    └── 镜像README.md             # 发布页「镜像说明」可直接粘贴的文案
```

---

## 运行架构

```
浏览器 ──6008──> server.py (FastAPI, asyncio 队列, 串行 _SEM=1)
                     │  HTTP 127.0.0.1:6006
                     ▼
                 ComfyUI (GPU 推理, 默认仅内网监听)
                     │
                     └─ 模型：软链接 → AutoDL 公共模型库 /.autodl/<hash>
```

- 端口 **6008** = 应用入口（前端 + API 同源），AutoDL「自定义服务」映射此端口即可访问。
- 端口 **6006** = ComfyUI 原生界面，**默认仅内网**（`COMFY_LISTEN=127.0.0.1`）——
  普通用户无法直接打开 ComfyUI，避免误改工作流/节点；开发者需要时
  `export COMFY_LISTEN=0.0.0.0 && bash scripts/start-all.sh` 即可对外。

---

## 环境要求

| 项 | 要求 |
|---|---|
| GPU | NVIDIA，显存 **≥ 24GB**（24GB 档仅 INT8 快速模式；48GB+ 全功能；96GB 可开 4K 实验档） |
| 系统 | Ubuntu 22.04（AutoDL 容器，无 systemd） |
| Python | 3.10+（AutoDL 镜像自带 `/root/miniconda3`） |
| ComfyUI | 已安装在 `/root/ComfyUI`（镜像内预置） |

Python 依赖（`requirements.txt`）：`fastapi`、`uvicorn`、`aiohttp`、`pillow`、`pydantic`。

---

## 快速开始（在 AutoDL 实例内）

```bash
# 1) 安装依赖
pip install -r requirements.txt

# 2) 环境自检（依赖 / 模型软链接 / 工作流 / 端口）
python tools/check_env.py

# 3) 一键部署（把仓库文件落到 /root 下的运行位置）
bash scripts/install.sh

# 4) 启动（模型软链接自愈 → ComfyUI → FastAPI）
bash scripts/start-all.sh

# 5) 验证
curl -s http://127.0.0.1:6008/api/ping
```

看到 `{"ok":true,...}` 后，在 AutoDL 控制台「自定义服务」映射 **6008**，浏览器打开即可使用。

---

## 模型说明（软链接 + 公共库）

镜像**不含大模型本体**，`ComfyUI/models/` 下均为软链接，指向 AutoDL 公共模型库
`/.autodl/<hash>`（内容寻址，只读）。克隆用户只要在控制台挂载对应公共库模型，软链接即刻生效；
万一失效，`start-all.sh` 会在开机时按 hash 自动找回并重建。

| 文件 | 用途 |
|---|---|
| `diffusion_models/qwen_image_2.1_bf16.safetensors` | 高质量模式（BF16） |
| `diffusion_models/qwen_image_2.1_int8_convrot.safetensors` | 快速模式（INT8） |
| `text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors` | 文生图文本编码器 |
| `text_encoders/qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors` | 图生图文本编码器 |
| `text_encoders/qwen3vl_8b_int8_convrot.safetensors` / `qwen3vl_8b_bf16.safetensors` | 工作流用视觉语言编码器 |
| `vae/qwen_image_2.1_vae_bf16.safetensors` | VAE |

> 「图像放大」功能依赖 **ComfyUI-VOSR2** 自定义节点及其 VOSR2 权重（随镜像打包）。

---

## 外部 API 契约

```
POST /api/generate          提交生成任务（后端未设 API_TOKEN 时无需鉴权头）
  Headers: Authorization: Bearer <访问口令>      // 仅当部署方设置了 API_TOKEN
  Body: { prompt, model:"fast"|"hq", ratio, resLv, steps,
          cfg, sampler, scheduler, denoise,
          seed, count, refs:[{name,data,mask}],
          transparent:false,                      // 透明背景 RGBA
          views:["正面","左侧"],                   // 多视角：逐张生成
          upscale:false, upscale_scale:2 }         // 出图后高清放大
  → { taskId, status:"queued", queue, resNote? }

GET  /api/task/{taskId}     轮询进度
  → { status:"running"|"done"|"failed", pct, node, etaSec, error?, resNote?,
      images:[{n,seed,view?}] }

GET  /api/image/{taskId}?n=0&thumb=1            取图（thumb=1 为缩略图）

POST /api/restart           重启生成服务（需管理员口令）
POST /api/aiopt/optimize    AI 提示词优化（使用调用方自己的令牌）
GET  /api/stats             3s 轮询：GPU/显存/内存/队列状态
```

---

## 访问口令（可选）

后端默认 **不鉴权、开箱即用**（`API_TOKEN` 为空）。部署方若需保护：

```bash
API_TOKEN=你的口令 bash scripts/start-all.sh
```

用户打开页面后点击右上角 🔒 填入口令即可（或 URL 追加 `?token=口令`）。

---

## 发布镜像前

```bash
bash scripts/prepublish.sh
```

会清空个人生成图片（`ComfyUI/output`）、参考图（`ComfyUI/input`）、日志、shell 历史，
并**清空 AI 优化令牌（api_key）**，确保镜像内不含任何个人数据与凭据。

详见 [`docs/AUTODL-镜像发布指南.md`](docs/AUTODL-镜像发布指南.md)。

---

## 授权

Qwen-Image-2.1 模型版权归 Qwen 官方（阿里通义）所有，商用需另行申请授权。
本仓库为部署与前端实现代码。
