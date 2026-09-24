# Qwen Image Studio · Qwen-Image-2.1 图像创作工作台

开箱即用的 **Qwen-Image-2.1** 图像创作应用镜像：网页工作台 + FastAPI 后端 + ComfyUI 推理。
克隆本镜像创建实例后，**开机自动启动，无需任何配置**。

---

## 一键使用

1. **创建实例**：镜像选择本镜像；显卡建议 **显存 ≥24GB**（24GB 仅 INT8 快速模式；48GB+ 全功能；96GB 可开 4K 实验档）。
2. **挂载公共模型库**（重要）：在控制台挂载以下模型（不占本地磁盘）：
   - `qwen_image_2.1_bf16.safetensors`（高质量）
   - `qwen_image_2.1_int8_convrot.safetensors`（快速）
   - `qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors`
   - `qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors`
   - `qwen3vl_8b_int8_convrot.safetensors`
   - `qwen_image_2.1_vae_bf16.safetensors`
3. **端口映射**：控制台「自定义服务」→ 映射端口 **6008**。
4. **打开页面**：浏览器访问映射后的地址即可开始创作。

> 开机后约 30~60 秒服务就绪。自检命令：`python3 /root/QwenImage2.1Studio/tools/check_env.py`

---

## 功能一览

| 功能 | 说明 |
|---|---|
| 文生图 / 图生图 | 快速（INT8）/ 高质量（BF16）双模式，7 种官方比例，官方原生分辨率 |
| 局部重绘 | 最多 10 张参考图，每张可单独画笔蒙版 |
| 移除背景 | 输出真透明 RGBA PNG |
| 高清放大 | 2×/3×/4×（VOSR2），可在出图后自动追加，也可对参考图直接放大 |
| 快捷创作 | 全景摄影、多视角多图（8 固定角度）、人物三视图、快速换装、人物替换、PNG 抠图 |
| AI 提示词优化 | 使用**你自己的** AutoDL Art 大模型令牌（设置面板填写） |
| 创作记录 | 参数归档、灯箱查看、以种子再生成、参数回填、下载 |
| 显卡自适应 | 按显存自动裁剪模式与分辨率档位，提交前防爆显存校验 |

---

## 端口说明

| 端口 | 用途 | 是否映射 |
|---|---|---|
| **6008** | 应用主入口（前端 + API 同源） | ✅ **只需映射这个** |
| 6006 | ComfyUI 原生界面 | ❌ 默认仅内网监听，**请勿映射**（防误改工作流） |

---

## 常见问题

| 现象 | 处理 |
|---|---|
| 打开页面提示「连接失败」 | 确认已映射 **6008**；查看 `/root/qwen-server.log` |
| 服务没起来 | SSH 执行 `bash /root/start-all.sh` |
| 模型报文件不存在 | 未挂载公共模型库，按上文第 2 步挂载后执行 `bash /root/start-all.sh` |
| AI 优化提示未配置令牌 | 打开「API 设置」，填入你自己的 AutoDL Art 令牌（仅保存在你自己的实例） |
| 高质量模式置灰 | 当前显卡显存不足（BF16 需 ≥28GB），属正常防爆显存设计 |
| 想自己调试 ComfyUI | `export COMFY_LISTEN=0.0.0.0 && bash /root/start-all.sh` 后再临时映射 6006 |

---

## 源码仓库

https://github.com/Nicke000/QwenImage2.1Studio

镜像内路径：`/root/QwenImage2.1Studio`

---

## 声明

Qwen-Image-2.1 模型版权归 Qwen 官方所有，商用需另行申请授权。
