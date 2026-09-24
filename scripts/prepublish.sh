#!/bin/bash
# ============================================================
# prepublish.sh — AutoDL 保存镜像前清理脚本
# 用途：发布应用给他人克隆前，清除个人数据 + 敏感配置，保证镜像干净
# 清理项：
#   1. ComfyUI/output 全部生成图片（个人数据）
#   2. ComfyUI/input 全部参考图（个人数据）
#   3. 各类日志文件
#   4. 命令行历史
#   5. AI 优化令牌 api_key 清空（防令牌随镜像泄露！）
#      克隆用户在自己的实例上重新填写自己的令牌即可
# 注意：/root 下除 autodl-tmp 外全部内容都会进镜像，
#      因此【不要】在本机留任何令牌备份文件。
# 用法：bash /root/prepublish.sh
# ============================================================
set -e
echo "===== prepublish $(date '+%F %T') ====="

# ---- 1. 清空生成图片 / 参考图 ----
echo "[1/5] 清空 ComfyUI output ..."
rm -rf /root/ComfyUI/output/* 2>/dev/null || true
echo "      完成"
echo "[2/5] 清空 ComfyUI input ..."
rm -rf /root/ComfyUI/input/* 2>/dev/null || true
echo "      完成"

# ---- 2. 清日志 ----
echo "[3/5] 清理日志 ..."
for f in /root/*.log; do
  [ -f "$f" ] && : > "$f" 2>/dev/null && echo "      清空 $(basename "$f")"
done

# ---- 3. 清命令行历史 ----
echo "[4/5] 清空 bash 历史 ..."
: > /root/.bash_history 2>/dev/null || true

# ---- 4. AI 优化令牌清空（防泄露） ----
echo "[5/5] 清空 AI 优化令牌 api_key 并重置默认模型 ..."
if [ -f /root/qwen-aiopt.json ]; then
  /root/miniconda3/bin/python - <<'PYEOF'
import json
p = "/root/qwen-aiopt.json"
d = json.load(open(p))
d["api_key"] = ""
# 重置为开箱可用的默认模型（发布版避免残留个人测试的受限模型）
if "model" in d:
    d["model"] = "DeepSeek-V4.1-Flash"
json.dump(d, open(p, "w"), ensure_ascii=False, indent=2)
print("      已清空 api_key、重置 model（其余配置保留）")
PYEOF
else
  echo "      未找到 qwen-aiopt.json"
fi

echo "===== prepublish 完成 ====="
echo "下一步：在 AutoDL 控制台保存镜像 → 发布应用。"
echo "克隆用户在自己的实例上：网页右上角「锁」填入自己的令牌即可用 AI 优化。"