#!/bin/bash
# ============================================================
# Qwen Image Studio — 开机启动脚本
#   1. 模型软链接自愈（公共模型库 → 数据盘副本）
#   2. 缺模型时可选自动下载（AUTO_FETCH_MODELS=1，默认开）
#   3. 启动 ComfyUI（默认仅内网 127.0.0.1:6006）
#   4. 启动 Qwen Image Studio（0.0.0.0:6008）
#
# 入口：/etc/autodl.sh（AutoDL 官方开机钩子，由 /init/bin/customer.cmd.sh 调用）
# 用法：bash /root/start-all.sh
# ============================================================
export PATH=/root/miniconda3/bin:$PATH
LOG=/root/qwen-studio-start.log
exec >>"$LOG" 2>&1
echo "===== $(date '+%F %T') Qwen Image Studio start ====="

COMFY="${COMFY_DIR:-/root/ComfyUI}"
SERVER="${SERVER_FILE:-/root/qwen-server.py}"
WEB_DIR="${WEB_DIR:-/root/qwen-web}"
LOCAL_MODELS="${MODELS_DIR:-/root/autodl-tmp/models}"
COMFY_LISTEN="${COMFY_LISTEN:-127.0.0.1}"
AUTO_FETCH_MODELS="${AUTO_FETCH_MODELS:-0}"   # 1=缺模型时自动下载（默认关，按需开启）

DM="$COMFY/models/diffusion_models"
TE="$COMFY/models/text_encoders"
VAE="$COMFY/models/vae"
mkdir -p "$DM" "$TE" "$VAE"

# ---- 1. 模型软链接自愈 ----
# 期望文件（软链接名 → AutoDL 公共模型库 hash 路径）
declare -A EXPECT=(
  ["$DM/qwen_image_2.1_bf16.safetensors"]="25/bd/b2/25bdb21b951a9cc4e78aefe96916c6f3"
  ["$DM/qwen_image_2.1_int8_convrot.safetensors"]="7c/40/26/7c4026516e59abf14c6d2eea96f5b64d"
  ["$TE/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors"]="3d/3f/3a/3d3f3a0b0ba34f661417445411d510eb"
  ["$TE/qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors"]="2e/e4/cf/2ee4cf11996deea1b17772686edd7a2c"
  ["$TE/qwen3vl_8b_bf16.safetensors"]="6b/20/2d/6b202dae088cde09726ee08040902f0b"
  ["$TE/qwen3vl_8b_int8_convrot.safetensors"]="6f/32/f9/6f32f9794879cea9e29830e9262c019c"
  ["$VAE/qwen_image_2.1_vae_bf16.safetensors"]="e9/91/c8/e991c8302f76ed7491244e0a5f0b64db"
)
# 公共模型库可能的挂载位置（AutoDL 随实例/区域不同）
PUBLIC_BASES=(/.autodl /.autodl-model/data /root/.autodl /autodl-pub/models)

fix_one() {
  local link="$1" hash_path="$2"
  # 已有效
  if [ -L "$link" ] && [ -s "$link" ]; then echo "OK   $(basename "$link")"; return 0; fi
  # a) 公共模型库找回
  for base in "${PUBLIC_BASES[@]}"; do
    local src="$base/$hash_path"
    if [ -f "$src" ] && [ -s "$src" ]; then
      ln -sf "$src" "$link"; echo "RELI $(basename "$link") <- 公共库 $base"; return 0
    fi
  done
  # b) 数据盘已下载副本
  local sub name dst
  case "$link" in
    "$DM"/*) sub="diffusion_models" ;;
    "$TE"/*) sub="text_encoders" ;;
    "$VAE"/*) sub="vae" ;;
    *) sub="diffusion_models" ;;
  esac
  name="$(basename "$link")"
  dst="$LOCAL_MODELS/$sub/$name"
  if [ -f "$dst" ] && [ -s "$dst" ]; then
    ln -sf "$dst" "$link"; echo "LINK $(basename "$link") <- 数据盘"; return 0
  fi
  echo "MISS $(basename "$link")"
  return 1
}

MISSING=0
for link in "${!EXPECT[@]}"; do
  fix_one "$link" "${EXPECT[$link]}" || MISSING=$((MISSING+1))
done

# ---- 2. 缺模型时自动下载（默认开启，可用 AUTO_FETCH_MODELS=0 关闭）----
if [ "$MISSING" -gt 0 ] && [ "$AUTO_FETCH_MODELS" = "1" ]; then
  if pgrep -f "fetch_models.py" >/dev/null 2>&1; then
    echo "模型下载已在进行中，跳过"
  else
    FREE_GB=$(df -BG --output=avail "$LOCAL_MODELS" 2>/dev/null | tail -1 | tr -dc '0-9')
    echo "缺失 $MISSING 个模型，可用空间 ${FREE_GB:-?}GB"
    if [ "${FREE_GB:-0}" -ge 18 ]; then
      echo "后台自动下载基础模型（fast 集，约 16GB）-> $LOCAL_MODELS"
      setsid python /root/fetch_models.py >>/root/fetch_models.log 2>&1 </dev/null &
    else
      echo "WARN 数据盘空间不足（需 ≥18GB），请手动执行: python3 /root/fetch_models.py"
    fi
  fi
fi

# ---- 3. 启动 ComfyUI（${COMFY_LISTEN}:6006）----
if curl -s -m 3 http://127.0.0.1:6006/system_stats >/dev/null 2>&1; then
  echo "ComfyUI 已在运行 (6006)"
else
  echo "启动 ComfyUI -> ${COMFY_LISTEN}:6006 ..."
  cd "$COMFY" && nohup /root/miniconda3/bin/python "$COMFY/main.py" --port 6006 --listen "$COMFY_LISTEN" --disable-auto-launch \
    >>/root/comfyui.log 2>&1 &
  for i in $(seq 1 60); do
    sleep 2
    if curl -s -m 3 http://127.0.0.1:6006/system_stats >/dev/null 2>&1; then
      echo "ComfyUI 就绪 (${i}*2s)"; break
    fi
  done
fi

# ---- 4. 启动 Qwen Image Studio（0.0.0.0:6008）----
if curl -s -m 3 http://127.0.0.1:6008/api/ping >/dev/null 2>&1; then
  echo "Qwen Image Studio 已在运行 (6008)"
else
  echo "启动 Qwen Image Studio -> 0.0.0.0:6008 ..."
  cd /root
  PORT=6008 WEB_DIR="$WEB_DIR" nohup /root/miniconda3/bin/python "$SERVER" >>/root/qwen-server.log 2>&1 &
  for i in $(seq 1 30); do
    sleep 1
    if curl -s -m 3 http://127.0.0.1:6008/api/ping >/dev/null 2>&1; then
      echo "Qwen Image Studio 就绪 (${i}s)"; break
    fi
  done
fi

echo "===== $(date '+%F %T') 启动完成 ====="
echo "  6008 应用 : http://<实例>:6008   (AutoDL 自定义服务映射此端口)"
echo "  6006 ComfyUI: http://127.0.0.1:6006 (仅内网; 需外露: COMFY_LISTEN=0.0.0.0)"
echo "  日志: /root/qwen-studio-start.log | 模型下载: /root/fetch_models.log"
