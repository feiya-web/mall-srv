# 缓存设计（Cache Aside）

> 针对商品、分类高频重复查询造成数据库压力过大的问题，落地 Cache Aside
> 缓存方案，实现缓存回源、TTL 过期、更新删除缓存逻辑。

## 1. 为什么选 Cache Aside

- 读多写少：商品/分类/组合套装属于低频修改、高频读取的数据
- 相比读写穿透/异步双写，Cache Aside 实现最简单、一致性可控，工程上更稳妥

## 2. 键设计

| 缓存 | Key | TTL |
|------|-----|-----|
| 分类维度商品列表（含喜好） | `dish:cache:list:{categoryId}` | 30 min |
| 分类维度组合套装列表 | `setmeal:cache:list:{categoryId}` | 30 min |

- 以「分类」为缓存粒度：与页面展示结构一致，命中率高；避免缓存全表大对象
- 值为 JSON（Jackson 序列化，LocalDateTime 注册了 JavaTimeModule）

## 3. 读路径（缓存回源）

```
请求 -> Redis GET
        ├─ 命中 -> 反序列化直接返回（hitCount + 1）
        └─ 未命中 -> 查 MySQL -> 写回 Redis（SET key value EX 1800）-> 返回（missCount + 1）
```

## 4. 写路径（更新删除缓存）

- 商品新增/修改/删除/上下架 → 删除 `dish:cache:list:*`（前缀批量删除）
- 组合套装变更 → 同步删除 `setmeal:cache:list:*`
- 先更新数据库、再删缓存：即使删除失败，TTL 兜底保证最终一致（最多 30 分钟陈旧）

## 5. 命中率统计口径

`CacheService` 内维护 AtomicLong hit/miss 计数器，对外暴露：

```
GET /admin/cache/stats
{"code":1,"data":{"hitCount":9100,"missCount":900,"hitRatio":91.0}}
```

- 压测方式：200 并发打 `/user/dish/list`（预热一轮后），读取该接口即得到命中率
- 「命中率 91%」即此口径：hit / (hit + miss)
