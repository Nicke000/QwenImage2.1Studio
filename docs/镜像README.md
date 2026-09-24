![Qwen Image 2.1 Creative Studio](https://raw.githubusercontent.com/Nicke000/QwenImage2.1Studio/main/docs/images/cover.jpg)

# Qwen Image 2.1 Creative Studio

面向公众使用的 **Qwen-Image-2.1** 图像创作工作台：网页界面 + FastAPI 后端 + ComfyUI 推理。
克隆本镜像创建实例后 **开机自动启动、零配置**，只需在控制台映射 **6008** 端口即可开始创作。

**核心能力**

- **文生图**：INT8 快速 / BF16 高质量双模式，7 种官方比例，最高原生 2K（2752px 长边）
- **图片编辑**：最多 10 张参考图，每张可单独画笔蒙版，局部重绘
- **PNG 格式抠图**：输出真透明 RGBA（非黑底）
- **高清放大**：2× / 3× / 4×（VOSR2），可在出图后自动对每张结果追加放大
- **快捷创作**：快速换装 · 人物替换 · 全景摄影 · 多视角多图 · 人物三视图 · 参考图放大
- **AI 提示词优化**：接入 AutoDL Art 大模型 API，使用**你自己的令牌**
- **创作记录**：完整参数归档、灯箱查看、以种子再生成、参数回填、一键下载
- **显卡自适应**：按显存自动裁剪模式与分辨率，提交前防爆显存校验

---

## 界面预览

**主界面**（左：创作区 / 右：任务队列 + 创作记录）

![主界面](https://raw.githubusercontent.com/Nicke000/QwenImage2.1Studio/main/docs/images/ui-main.png)

**生成模式**：快速生成（INT8 量化，出图快）与 高质量（BF16 原版，细节优先）二选一

![生成模式](https://raw.githubusercontent.com/Nicke000/QwenImage2.1Studio/main/docs/images/ui-mode.png)

**提示词区**：快捷创作一键套用、`@` 引用参考图、透明 PNG、真人/动漫负面词预设、AI 优化

![提示词区](https://raw.githubusercontent.com/Nicke000/QwenImage2.1Studio/main/docs/images/ui-prompt.png)

**生成参数**：比例 / 分辨率 / 数量 / 步数 / 种子，以及**高清放大开关 + 倍率**（开启后出图自动追加放大）

![生成参数](https://raw.githubusercontent.com/Nicke000/QwenImage2.1Studio/main/docs/images/ui-params.png)

---

## 硬件要求

| 显存 | 可用能力 |
|---|---|
| **最低 24GB** | 仅 INT8 快速模式，最高 2K |
| **推荐 32GB 以上** | 高质量 BF16 更从容，长边分辨率更稳 |
| 40GB ~ 48GB 及以上 | 高质量 + 2K/4K 全开，批量生成更稳 |

> 显存不足时页面会自动置灰「高质量」并裁剪过高档位，不会爆显存。

---

## 一键使用

1. **创建实例**：镜像选择本镜像；显存 **最低 24GB，推荐 32GB 以上**。
2. **端口映射**：控制台 →「自定义服务」→ 映射 **6008**（应用入口）。
3. **打开页面**：访问映射后的地址即可开始创作（默认无需口令）。
4. **模型**：镜像内的模型为软链接，指向 AutoDL 公共模型库（区域支持时自动生效）。
   若你的实例没有公共模型库挂载，执行下面命令自动下载到数据盘（约 16GB）：
   ```bash
   python3 /root/fetch_models.py
   ```
5. **AI 优化（可选）**：页面「API 设置」填入你自己的 AutoDL Art 令牌即可使用。

---

## 基本环境

- **Python**：3.12.3（`/root/miniconda3`）
- **框架及版本**：PyTorch 2.8.0 + CUDA 12.8；ComfyUI 0.37.0（原生支持 Qwen-Image 2.1）；FastAPI + uvicorn（应用后端）
- **CUDA 版本**：12.8
- **PyTorch**：2.8.0（CUDA 12.8 构建）
- **关键依赖**：fastapi / uvicorn / aiohttp / pillow / pydantic
- **自定义节点**：ComfyUI-VOSR2（高清放大）、ComfyUI-Manager、Impact-Pack 等
- **模型**：Qwen-Image-2.1 系列（软链接至 AutoDL 公共模型库）；VOSR2 放大权重随镜像内置

### 磁盘建议

| 项 | 建议 | 说明 |
|---|---|---|
| 系统盘 | **≥30GB（推荐 50GB）** | 镜像本身约 19GB；生成图片默认存系统盘，长期使用建议留足 |
| 数据盘 | ≥20GB | 仅当需要 `fetch_models.py` 下载模型时使用（约 16GB） |

---

## 构建过程

### 代码 Clone

```bash
cd /root
git clone https://github.com/Nicke000/QwenImage2.1Studio.git
```

### 依赖安装

```bash
pip install -r /root/QwenImage2.1Studio/requirements.txt
```

### 部署与启动

```bash
# 部署后端 / 前端 / 工作流 / 开机自启钩子
bash /root/QwenImage2.1Studio/scripts/install.sh --no-deps

# 启动 ComfyUI(:6006) 与应用(:6008)
bash /root/start-all.sh
```

> 开机自启由 AutoDL 官方钩子 `/etc/autodl.sh` 实现
> （`/init/bin/customer.cmd.sh` 在开机时调用它），随镜像已配置好。

---

## 环境验证代码

执行命令：

```bash
python /root/QwenImage2.1Studio/tools/check_env.py
```

预期输出：

```
==============================================================
 Qwen Image Studio — 环境自检
==============================================================
[1/7] Python 环境            [ OK ] Python 3.12 满足要求 (>=3.10)
[2/7] 运行依赖               [ OK ] FastAPI / uvicorn / aiohttp / Pillow / pydantic
[3/7] ComfyUI                [ OK ] ComfyUI 目录存在 / main.py 存在
[4/7] 模型软链接             [ OK ] 6 个模型软链接有效
[5/7] 工作流                 [ OK ] 共 6 个
[6/7] 应用文件               [ OK ] server.py / web/index.html
[7/7] 服务端口

 结果: 通过 18 项 / 失败 0 项 / 提示 1 项
 状态: 环境合格 ✅
```

该脚本**纯标准库实现**（无需安装任何依赖），检查 Python 版本、运行依赖、ComfyUI、
模型软链接、工作流文件、应用文件与端口监听；全部必须项通过时退出码为 `0`。

服务可用性验证：

```bash
curl -s http://127.0.0.1:6008/api/ping
# → {"ok":true,"name":"Qwen Image Studio","model":{...}}
```

---

## 目录与端口

| 路径 / 端口 | 说明 |
|---|---|
| `/root/QwenImage2.1Studio` | 源码仓库（本镜像包含） |
| `/root/qwen-server.py` | 应用后端（FastAPI，网页 + API 同源） |
| `/root/qwen-web/index.html` | 前端工作台（单文件） |
| `/root/ComfyUI` | 推理引擎与工作流 |
| `/root/start-all.sh` | 启动脚本（模型软链接自愈 → ComfyUI → 应用） |
| `/etc/autodl.sh` | 开机自启钩子（AutoDL 官方机制） |
| **6008** | **应用入口 —— 映射这个端口** |
| 6006 | ComfyUI 原生界面（仅内网监听，无需映射） |

---

## 常见问题

| 现象 | 处理 |
|---|---|
| 页面打不开 / 提示连接失败 | 确认已映射 **6008**；SSH 执行 `bash /root/start-all.sh` 后重试 |
| 重启实例后服务没起来 | 查看 `/tmp/autodl.sh.log` 与 `/root/qwen-studio-start.log`；确认 `/etc/autodl.sh` 存在 |
| 生成时报「模型文件不存在」 | 实例未挂载公共模型库；执行 `python3 /root/fetch_models.py`（约 16GB，存数据盘） |
| 「高质量」模式置灰 | 当前显存不足（BF16 需约 28GB 以上），属正常防爆显存设计 |
| 想改访问口令 | 以 `API_TOKEN=你的口令` 环境变量启动后端（默认留空 = 免口令开箱即用） |
| 6006 打不开 | 设计如此：ComfyUI 默认仅内网，普通使用不需要它 |

---

## 交流

- B 站主页：<https://space.bilibili.com/1233399780>

---

## 声明

Qwen-Image-2.1 模型版权归 Qwen 官方所有，商用需另行申请授权；本镜像为非商业研究用途。
