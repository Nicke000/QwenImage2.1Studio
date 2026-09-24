#!/bin/bash
# ============================================================
# /etc/autodl.sh — AutoDL 官方开机自启钩子
# 由 /init/bin/customer.cmd.sh 在实例开机时自动执行（日志 /tmp/autodl.sh.log）
# 作用：拉起 Qwen Image Studio（模型软链接自愈 → ComfyUI:6006 → 应用:6008）
# ============================================================
export PATH=/root/miniconda3/bin:$PATH
echo "=== Qwen Studio autostart $(date '+%F %T') ==="

# 等 GPU 驱动就绪（最多 60s）
for i in $(seq 1 30); do
  nvidia-smi -L >/dev/null 2>&1 && break
  sleep 2
done

# 拉起服务（start-all.sh 幂等：已在运行则跳过）
bash /root/start-all.sh

echo "=== Qwen Studio autostart done $(date '+%F %T') ==="
