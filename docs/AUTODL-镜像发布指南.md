# Qwen Image Studio — AutoDL 镜像发布与用户使用指南

> 目标：把当前已验证的实例保存为 AutoDL **自定义镜像**，发布后每个用户自行创建实例、
> 在自己的实例里通过 6006/6008 端口使用，无需任何配置。

---

## 一、当前实例已包含的内容（保存镜像即带走）

| 内容 | 位置 | 说明 |
|---|---|---|
| ComfyUI 0.37.0 | `/root/ComfyUI` | 原生支持 QwenImage21，含 t2i / i2i 工作流 |
| 后端 FastAPI | `/root/qwen-server.py` | GPU 检测 / /api/stats / 防爆显存，监听 0.0.0.0:6008 |
| 前端单文件 | `/root/qwen-web/index.html` | GPU 自适应 + 顶栏实时性能条 |
| 自启脚本 | `/root/start-all.sh` | 模型软链接自愈 → 拉起 ComfyUI + FastAPI |
| **开机自启** | **`/etc/autodl.sh`** | **AutoDL 官方钩子**：`/init/bin/customer.cmd.sh` 开机时调用它 |
| 模型软链接 | `/root/ComfyUI/models/{diffusion_models,text_encoders,vae}` | 指向公共库 hash 路径（内容寻址） |

> **重要**：AutoDL 容器**不会执行 `/etc/rc.local`**（旧的 rc.local + .bashrc 方案无效）。
> 官方开机钩子是 `/etc/autodl.sh`，由 `/init/bin/customer.cmd.sh` 在开机时执行，
> 日志见 `/tmp/autodl.sh.log`。本项目通过 `scripts/autodl.sh` 安装该钩子。
> 开机自启效果自检：`cat /tmp/autodl.sh.log` + `curl -s http://127.0.0.1:6008/api/ping`。

> 镜像内的软链接指向 `/.autodl/<hash>` 公共库路径。该路径是**内容寻址**的：
> 任何用户在实例上挂载同一公共库模型后，路径一致，软链接自动生效；
> 万一失效，`start-all.sh` 会在开机时按 hash 在公共库中找回并重建。

---

## 二、发布者操作（只做一次）

### 1. 保存自定义镜像

AutoDL 控制台 → 实例 → 更多操作 → **保存镜像**

- 镜像名：如 `qwen-image-2.1-studio`，备注写明：
  - 端口：**6008 = Qwen Image Studio 应用**，6006 = ComfyUI 界面
  - **开箱即用，无需口令**（后端未配置 API_TOKEN）；发布者如需保护可设 `API_TOKEN=xxx`
  - 需要挂载的公共库模型（见下）
- 等待保存完成（保存期间实例会关机，请确认当前无生成任务）

### 2. （可选）发布到社区 / 设为公开

- 镜像市场 → 我的镜像 → 设为公开 / 复制给他人
- 若只在群内分享：直接给镜像 ID 或「分享链接」

### 3. 写清用户须知（发布描述/说明文档）

见下方「用户须知模板」，建议附在镜像备注或群公告。

---

## 三、用户操作（每个使用者）

### 1. 创建实例

- AutoDL 控制台 → 租用新实例 → 镜像选择「自定义镜像」→ 选中 `qwen-image-2.1-studio`
- 显卡按需选择：**24GB 起**（3090/4090/24G 档 → 仅 INT8 快速模式，最高 2K）；
  48GB+（3090/48G、A6000、A40）→ 全功能；96GB（Pro6000 等）→ 全功能 + 4K
- 按发布者说明**勾选公共库模型挂载**（5 个文件，见文末清单）

### 2. 开机即用（零配置）

实例开机后，AutoDL 官方钩子 `/etc/autodl.sh` 自动执行 `/root/start-all.sh`：

1. 自愈模型软链接（公共模型库 → 数据盘副本）
2. 启动 ComfyUI → `127.0.0.1:6006`（默认仅内网）
3. 启动 Qwen Image Studio → `0.0.0.0:6008`
4. 日志：`/tmp/autodl.sh.log`、`/root/qwen-studio-start.log`、`/root/qwen-server.log`、`/root/comfyui.log`

等待约 30~60 秒后：
- SSH 执行 `curl -s http://127.0.0.1:6008/api/ping` 应返回 `{"ok":true,...}`
- 或在控制台「自定义服务」添加端口映射后直接用浏览器访问

### 3. 端口映射（访问方式）

AutoDL 控制台 → 实例 → **自定义服务**：

| 端口 | 用途 | 是否必须 |
|---|---|---|
| **6008** | Qwen Image Studio 应用（前端 + API 同源） | ✅ 必填 |
| 6006 | ComfyUI 原生界面 | ❌ **不要映射**（默认仅内网监听，隔离用户） |

> ComfyUI 默认以 `COMFY_LISTEN=127.0.0.1` 启动，**只监听本机回环**：
> 普通用户无法打开 ComfyUI 界面，避免误改工作流/自定义节点导致镜像不可用；
> 后端通过 `127.0.0.1:6006` 本地调用不受影响。开发者如需调试：
> `export COMFY_LISTEN=0.0.0.0 && bash /root/start-all.sh` 后再临时映射 6006。

映射后浏览器打开 `https://<区域>.seetacloud.com:<公网端口>/` 即可使用。

### 4. 访问口令（可选）

- **默认开箱即用**：后端未配置 `API_TOKEN` 时无需任何口令，打开即用
- 仅当发布者设置了 `API_TOKEN`：页面右上角「锁」→ 输入口令保存；或 URL 加 `?token=<口令>`
- 口令错误时页面提示「访问口令错误，请点击右上角「锁」重新设置」

### 5. 按显卡自动适配（无需手动判断）

打开页面后顶部实时性能条显示 GPU 型号与显存，后端自动：

- 显存 <24GB：「高质量（BF16）」卡**置灰**并标注所需显存，只能用快速生成（INT8）
- 分辨率档位超出显卡上限 → 自动隐藏 + 切换回官方原生并提示
- 提交时若预计峰值显存会爆 → 直接拒绝并说明原因；接近上限 → 黄色软警告

---

## 四、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 页面显示「连接失败（检查口令/服务）」 | 后端设置了口令但未填写，或 6008 未映射；右上角「锁」输入口令（默认开箱即用无需填） |
| 页面显示「演示 · 未连接后端」且出图带水印 | 浏览器访问不到 6008 后端，检查端口映射 / 是否通过公网地址访问 |
| 提交后提示「队列已满」 | 排队上限 8，稍后再试 |
| 「高质量」置灰 | 当前显卡显存不足（BF16 需 ≥28GB 才稳），属正常防爆显存 |
| 生成任务一直「排队中」不动 | 查看 `/root/qwen-server.log`；如 worker 未启动，执行 `bash /root/start-all.sh` |
| 重启实例后服务没起来 | 检查 `/tmp/autodl.sh.log` 与 `/root/qwen-studio-start.log`；确认 `/etc/autodl.sh` 存在且可执行；也可手动 `bash /root/start-all.sh` |
| 模型报「文件不存在」 | 实例未挂载公共模型库（区域不支持时属正常）；两条路：① 创建实例时挂载公共模型；② 执行 `python3 /root/fetch_models.py` 从 hf-mirror 下载（约 16GB 到数据盘） |
| 想改访问口令 | 停后端 → 用 `API_TOKEN` / `ADMIN_TOKEN` 环境变量启动（留空 = 免口令开箱即用） |
| 端口 6006 打不开 | 设计如此：ComfyUI 默认仅内网监听，普通用户无需访问；开发者如需调试见上文「端口说明」 |

---

## 五、公共库模型挂载清单（5 个文件）

用户创建实例时在 AutoDL「公共库」搜索并挂载以下模型（均来自 Qwen 官方/镜像市场公共库）：

```
diffusion_models/
  qwen_image_2.1_bf16.safetensors            (13.25 GB)  — 高质量 BF16
  qwen_image_2.1_int8_convrot.safetensors    (6.76 GB)   — 快速 INT8
text_encoders/
  qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors  (8.82 GB)
  qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors  (8.82 GB)
vae/
  qwen_image_2.1_vae_bf16.safetensors
```

> 公共库挂载不占实例本地磁盘。**注意**：AutoDL 公共模型库按区域挂载，部分区域实例上
> 没有该挂载（只有 `/autodl-pub` 公开数据集）。此时软链接会失效，可执行
> `python3 /root/fetch_models.py` 从 hf-mirror 下载模型到数据盘（文件名与工作流一致），
> `start-all.sh` 会自动把软链接指向数据盘副本。

---

## 六、发布前最终自检清单（发布者）

- [ ] `curl -s http://127.0.0.1:6008/api/ping` 返回 `{"ok":true,...}`（含 gpu 档位）
- [ ] 顶栏实时性能条显示 GPU/显存/内存（服务在线）
- [ ] t2i 出一张图；i2i + 蒙版出一张图；cutout 出一张透明 PNG
- [ ] `/root/start-all.sh` 执行两遍不冲突（幂等：已在运行则跳过）
- [ ] **`/etc/autodl.sh` 存在且 `chmod +x`**（AutoDL 官方开机钩子，rc.local 无效）
- [ ] `/root/QwenImage2.1Studio` 仓库存在（发布镜像的审核要求）
- [ ] 已执行 `bash /root/prepublish.sh`（清个人图片 + 日志 + AI 令牌）
- [ ] 启动后执行 `python3 /root/QwenImage2.1Studio/tools/check_env.py` 全通过
- [ ] 镜像备注写清：端口 6008、是否设口令（默认免口令）、模型获取方式、显卡建议
