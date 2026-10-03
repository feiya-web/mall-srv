#!/usr/bin/env bash
# ============================================================
# 接口层压测脚本（无需 JMeter）：对比「优化前（无联合索引）」与「优化后（联合索引）」的接口响应时间
# 统计指标：平均耗时 / P95 耗时（单位 ms）
# 用法：bash 03_api_benchmark.sh [请求次数]
# 连接参数可用环境变量覆盖：BASE_URL / DEMO_PASSWORD
# ============================================================
BASE="${BASE_URL:-http://localhost:8080}"
N=${1:-100}
DEMO_PASSWORD="${DEMO_PASSWORD:-123456}"   # 演示账号口令，与种子数据一致
CURL() { curl -s --noproxy "*" "$@"; }

# 登录拿 token
USER_TOKEN=$(CURL -X POST "$BASE/user/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"zhangsan\",\"password\":\"$DEMO_PASSWORD\"}" | sed 's/.*"token":"\([^"]*\)".*/\1/')
ADMIN_TOKEN=$(CURL -X POST "$BASE/admin/employee/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"$DEMO_PASSWORD\"}" | sed 's/.*"token":"\([^"]*\)".*/\1/')

# 取一个真实订单号/订单ID用于详情查询
ORDER_ID=$(CURL "$BASE/user/order/history?page=1&pageSize=1" -H "token: $USER_TOKEN" \
  | sed 's/.*"id":\([0-9]*\).*/\1/')

# 单次接口耗时采样：返回毫秒
timing() {
  CURL -o /dev/null -w "%{time_total}" "$@"
}

# 采样 N 次并统计 avg / P95
measure() {
  local label="$1" ; shift
  local file=$(mktemp)
  for ((i = 0; i < N; i++)); do
    t=$(timing "$@")
    awk -v t="$t" 'BEGIN{printf "%.0f\n", t*1000}' >> "$file"
  done
  local avg=$(awk '{s+=$1} END {printf "%.1f", s/NR}' "$file")
  local p95=$(sort -n "$file" | awk -v n="$N" 'NR==int(n*0.95)+1 {print $1}')
  printf "%-28s avg=%8s ms   P95=%8s ms\n" "$label" "$avg" "$p95"
  rm -f "$file"
}

echo "======== 接口层压测（每接口 $N 次） ========"
measure "用户历史订单 GET" "$BASE/user/order/history?page=1&pageSize=20" -H "token: $USER_TOKEN"
measure "订单详情 GET(含明细)" "$BASE/user/order/$ORDER_ID" -H "token: $USER_TOKEN"
measure "管理端订单分页 GET" "$BASE/admin/order/page?page=1&pageSize=20&status=2" -H "token: $ADMIN_TOKEN"
measure "C端商品列表 GET(缓存)" "$BASE/user/dish/list?categoryId=2" -H "token: $USER_TOKEN"

echo ""
echo "======== 缓存命中率（重启应用后累计值） ========"
CURL "$BASE/admin/cache/stats" -H "token: $ADMIN_TOKEN"
echo ""
