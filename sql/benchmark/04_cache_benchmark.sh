#!/usr/bin/env bash
# ============================================================
# 缓存收益压测：验证「命中率」与「缓存对数据库查询的削减」
# 场景A：纯读（理想命中率）
# 场景B：读写混合（约 9% 写操作触发缓存失效）—— 对应真实业务的命中率口径
# 场景C：每次读前清空缓存（模拟无缓存）—— 对比 DB 直查耗时
# 用法：bash 04_cache_benchmark.sh
# 连接参数可用环境变量覆盖：BASE_URL / REDIS_CLI / REDIS_PASSWORD / DEMO_PASSWORD
# ============================================================
BASE="${BASE_URL:-http://localhost:8080}"
REDIS_CLI="${REDIS_CLI:-redis-cli}"      # 默认取 PATH 中的 redis-cli
REDIS_PASSWORD="${REDIS_PASSWORD:-123456}"   # Redis 口令，与 application.yml 默认值一致
DEMO_PASSWORD="${DEMO_PASSWORD:-123456}"   # 演示账号口令，与种子数据一致
CURL() { curl -s --noproxy "*" "$@"; }

ADMIN_TOKEN=$(CURL -X POST "$BASE/admin/employee/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"$DEMO_PASSWORD\"}" | sed 's/.*"token":"\([^"]*\)".*/\1/')
USER_TOKEN=$(CURL -X POST "$BASE/user/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"zhangsan\",\"password\":\"$DEMO_PASSWORD\"}" | sed 's/.*"token":"\([^"]*\)".*/\1/')

CACHE_KEY="dish:cache:list:2"
DISH_ID=3000   # 用于模拟写操作的压测商品

stats() {
  CURL "$BASE/admin/cache/stats" -H "token: $ADMIN_TOKEN" \
    | sed 's/.*"hitCount":\([0-9]*\),"hitRatio":\([0-9.]*\),"missCount":\([0-9]*\).*/\1 \2 \3/'
}

read_once() {
  CURL -o /dev/null -w "%{time_total}" "$BASE/user/dish/list?categoryId=2" -H "token: $USER_TOKEN"
}

# 采样耗时：N 次读，输出 avg / P95（ms）
sample() {
  local n=$1 file=$(mktemp)
  for ((i = 0; i < n; i++)); do
    t=$(read_once); awk -v t="$t" 'BEGIN{printf "%.0f\n", t*1000}' >> "$file"
  done
  awk -v n="$n" '{s+=$1} END {printf "avg=%.1fms", s/n}' "$file"
  echo -n "  "
  sort -n "$file" | awk -v n="$n" 'NR==int(n*0.95)+1 {printf "P95=%dms\n", $1}'
  rm -f "$file" 2>/dev/null
}

echo "======== 场景A：纯读 200 次（理想命中率） ========"
before=($(stats))
for ((i = 0; i < 200; i++)); do read_once > /dev/null; done
after=($(stats))
echo "本轮 hit=$((${after[0]} - ${before[0]}))  miss=$((${after[2]} - ${before[2]}))"
echo -n "纯读耗时："; sample 50

echo ""
echo "======== 场景B：读写混合 220 次（每 11 次 1 次写，写占比约 9%） ========"
before=($(stats))
for ((i = 1; i <= 220; i++)); do
  if (( i % 11 == 0 )); then
    # 写操作：切换商品上下架状态（会触发 Cache Aside 删除缓存）
    CURL -o /dev/null -X POST "$BASE/admin/dish/status/1?id=$DISH_ID" -H "token: $ADMIN_TOKEN"
  else
    read_once > /dev/null
  fi
done
after=($(stats))
h=$((${after[0]} - ${before[0]})); m=$((${after[2]} - ${before[2]}))
echo "本轮 hit=$h  miss=$m"
if (( h + m > 0 )); then
  awk -v h="$h" -v m="$m" 'BEGIN{printf "读写混合命中率 = %.1f%%\n", h*100/(h+m)}'
fi

echo ""
echo "======== 场景C：无缓存（每次读前删除缓存 key，强制回源 DB） ========"
"$REDIS_CLI" -a 123456 DEL "$CACHE_KEY" > /dev/null 2>&1
echo -n "无缓存耗时："
for ((i = 0; i < 50; i++)); do
  "$REDIS_CLI" -a 123456 DEL "$CACHE_KEY" > /dev/null 2>&1
  read_once > /dev/null
done
# 逐次删缓存再读，统计耗时
file=$(mktemp)
for ((i = 0; i < 50; i++)); do
  "$REDIS_CLI" -a 123456 DEL "$CACHE_KEY" > /dev/null 2>&1
  t=$(read_once); awk -v t="$t" 'BEGIN{printf "%.0f\n", t*1000}' >> "$file"
done
awk '{s+=$1} END {printf "avg=%.1fms", s/NR}' "$file"
echo -n "  "
sort -n "$file" | awk 'NR==int(50*0.95)+1 {printf "P95=%dms\n", $1}'
rm -f "$file" 2>/dev/null
