#!/bin/bash
# ============================================================
# Qwen Image Studio — AutoDL 镜像开机自启脚本
# 端口规划（镜像内绑定，用户通过 AutoDL「自定义服务」映射后自访问）：
#   6006 = ComfyUI（0.0.0.0，用户可直接打开 ComfyUI 界面）
#   6008 = Qwen Image Studio（FastAPI：前端 + API 同源）
# 功能：模型软链接自愈 → 启动 ComfyUI → 启动 FastAPI → 就绪自检
# 用法：chmod +x /root/start-all.sh && bash /root/start-all.sh
#       （或注册为 systemd 服务 qwen-studio.service 开机自启）
# ============================================================
export PATH=/root/miniconda3/bin:$PATH
LOG=/root/qwen-studio-start.log
exec >>"$LOG" 2>&1
echo "===== $(date '+%F %T') Qwen Image Studio start ====="

# ComfyUI 监听地址（发布版隔离）：
#   默认 127.0.0.1 = 仅本机回环，克隆用户无法直接打开 ComfyUI 界面（防误改工作流/节点）
#   qwen-server 通过 127.0.0.1:6006 本地调用 ComfyUI，不受影响
#   如需对外暴露（如开发者自己实例维护），export COMFY_LISTEN=0.0.0.0 后重启即可
COMFY_LISTEN="${COMFY_LISTEN:-127.0.0.1}"

COMFY=/root/ComfyUI
SERVER=/root/qwen-server.py
WEB_DIR=/root/qwen-web
[ -d "$WEB_DIR" ] || WEB_DIR=/root/qwen-web

# ---- 0. 等待公共库 AutoFS 就绪（模型在 /.autodl/ 或 /.autodl-model/data） ----
sleep 2

# ---- 1. 模型软链接自愈：缺失/失效时尝试从公共库找回 ----
DM="$COMFY/models/diffusion_models"
TE="$COMFY/models/text_encoders"
VAE="$COMFY/models/vae"
mkdir -p "$DM" "$TE" "$VAE"

# 期望的文件清单（软链接名 → 公共库 hash 路径前缀）
# 覆盖全部 6 个工作流实际引用的模型；克隆用户实例挂载同一公共库后自动生效
declare -A EXPECT=(
  ["$DM/qwen_image_2.1_bf16.safetensors"]="25/bd/b2/25bdb21b951a9cc4e78aefe96916c6f3"
  ["$DM/qwen_image_2.1_int8_convrot.safetensors"]="7c/40/26/7c4026516e59abf14c6d2eea96f5b64d"
  ["$TE/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors"]="3d/3f/3a/3d3f3a0b0ba34f661417445411d510eb"
  ["$TE/qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors"]="2e/e4/cf/2ee4cf11996deea1b17772686edd7a2c"
  ["$TE/qwen3vl_8b_bf16.safetensors"]="6b/20/2d/6b202dae088cde09726ee08040902f0b"
  ["$TE/qwen3vl_8b_int8_convrot.safetensors"]="6f/32/f9/6f32f9794879cea9e29830e9262c019c"
  ["$VAE/qwen_image_2.1_vae_bf16.safetensors"]="e9/91/c8/e991c8302f76ed7491244e0a5f0b64db"
)

for link in "${!EXPECT[@]}"; do
  hash_path="${EXPECT[$link]}"
  if [ -L "$link" ] && [ -s "$link" ]; then
    echo "OK   $link"
    continue
  fi
  echo "WARN 链接缺失: $link"
  # 尝试从公共库 hash 路径找回（AutoFS 懒挂载，访问即触发）
  for base in /.autodl /root/.autodl /.autodl-model/data; do
    src="$base/$hash_path"
    if [ -f "$src" ] && [ -s "$src" ]; then
      ln -sf "$src" "$link"
      echo "RELI 已重建软链接: $src -> $link"
      break
    fi
  done
  if [ ! -s "$link" ]; then
    echo "MISS 未找到公共库文件（hash=$hash_path），请确认实例已挂载该模型"
  fi
done

# ---- 2. 启动 ComfyUI（${COMFY_LISTEN}:6006） ----
if curl -s -m 3 http://127.0.0.1:6006/system_stats >/dev/null 2>&1; then
  echo "ComfyUI 已在运行 (6006)"
else
  echo "启动 ComfyUI -> ${COMFY_LISTEN}:6006 ..."
  nohup /root/miniconda3/bin/python "$COMFY/main.py" --port 6006 --listen "$COMFY_LISTEN" --disable-auto-launch \
    >>/root/comfyui.log 2>&1 &
  for i in $(seq 1 60); do
    sleep 2
    if curl -s -m 3 http://127.0.0.1:6006/system_stats >/dev/null 2>&1; then
      echo "ComfyUI 就绪 (${i}*2s)"
      break
    fi
  done
fi

# ---- 3. 启动 Qwen Image Studio（0.0.0.0:6008） ----
if curl -s -m 3 http://127.0.0.1:6008/api/ping >/dev/null 2>&1; then
  echo "Qwen Image Studio 已在运行 (6008)"
else
  echo "启动 Qwen Image Studio -> 0.0.0.0:6008 ..."
  cd /root
  PORT=6008 WEB_DIR="$WEB_DIR" nohup /root/miniconda3/bin/python "$SERVER" >>/root/qwen-server.log 2>&1 &
  for i in $(seq 1 30); do
    sleep 1
    if curl -s -m 3 http://127.0.0.1:6008/api/ping >/dev/null 2>&1; then
      echo "Qwen Image Studio 就绪 (${i}s)"
      break
    fi
  done
fi

echo "===== $(date '+%F %T') 启动完成 ====="
echo "  6006 ComfyUI   : http://127.0.0.1:6006 (仅内网，隔离；需要时 export COMFY_LISTEN=0.0.0.0 再重启)"
echo "  6008 应用/API  : http://<实例>:6008   (AutoDL 自定义服务映射此端口供访问，页面右上角「锁」可设口令)"
