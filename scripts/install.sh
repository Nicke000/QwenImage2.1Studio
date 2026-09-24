#!/bin/bash
# ============================================================
# install.sh — 把本仓库文件部署到运行位置
# 从仓库根目录执行：bash scripts/install.sh [--no-deps]
#   仓库                              运行位置
#   server.py                     ->  /root/qwen-server.py
#   web/*                         ->  /root/qwen-web/
#   workflows/*.json              ->  /root/ComfyUI/user/default/workflows/
#   scripts/start-all.sh          ->  /root/start-all.sh
#   scripts/prepublish.sh         ->  /root/prepublish.sh
#   scripts/rc.local              ->  /etc/rc.local  (AutoDL 开机自启入口)
# ============================================================
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMFY="${COMFY_DIR:-/root/ComfyUI}"
WEB="${WEB_DIR:-/root/qwen-web}"

echo "===== install.sh ====="
echo "仓库: $REPO"
echo "ComfyUI: $COMFY"

# ---- 依赖 ----
if [ "$1" != "--no-deps" ]; then
  echo "[1/6] 安装 Python 依赖 ..."
  pip install -r "$REPO/requirements.txt"
else
  echo "[1/6] 跳过依赖安装 (--no-deps)"
fi

# ---- 后端 ----
echo "[2/6] 部署后端 server.py -> /root/qwen-server.py"
cp -f "$REPO/server.py" /root/qwen-server.py

# ---- 前端 ----
echo "[3/6] 部署前端 -> $WEB"
mkdir -p "$WEB"
cp -f "$REPO/web/index.html" "$WEB/index.html"
[ -f "$REPO/web/favicon.svg" ] && cp -f "$REPO/web/favicon.svg" "$WEB/favicon.svg"

# ---- 工作流 ----
echo "[4/6] 部署工作流 -> $COMFY/user/default/workflows/"
mkdir -p "$COMFY/user/default/workflows"
cp -f "$REPO"/workflows/*.json "$COMFY/user/default/workflows/" 2>/dev/null || true

# ---- 脚本 ----
echo "[5/6] 部署启动/清理脚本"
cp -f "$REPO/scripts/start-all.sh" /root/start-all.sh
cp -f "$REPO/scripts/prepublish.sh" /root/prepublish.sh
[ -f "$REPO/tools/fetch_models.py" ] && cp -f "$REPO/tools/fetch_models.py" /root/fetch_models.py
chmod +x /root/start-all.sh /root/prepublish.sh

# ---- 开机自启（AutoDL 官方钩子）----
# AutoDL 容器无 systemd，/etc/rc.local 不会被自动执行；
# 官方机制是 /init/bin/customer.cmd.sh 在开机时执行 /etc/autodl.sh
echo "[6/6] 配置开机自启 /etc/autodl.sh"
cp -f "$REPO/scripts/autodl.sh" /etc/autodl.sh
chmod +x /etc/autodl.sh
# 兼容：部分镜像环境仍会执行 rc.local，一并写入
[ -f "$REPO/scripts/rc.local" ] && cp -f "$REPO/scripts/rc.local" /etc/rc.local && chmod +x /etc/rc.local

echo "===== 部署完成 ====="
echo "启动:    bash /root/start-all.sh"
echo "自检:    python3 $REPO/tools/check_env.py"
echo "端口:    6008 = 应用（映射此端口）  6006 = ComfyUI（默认仅内网）"
echo "开机自启: /etc/autodl.sh（AutoDL 由 /init/bin/customer.cmd.sh 调用）"
