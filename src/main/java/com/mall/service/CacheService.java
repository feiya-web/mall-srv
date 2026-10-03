package com.mall.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 缓存服务：统一封装 Cache Aside 读写 + 命中率统计
 * <p>
 * 读策略：先查缓存，未命中回源数据库并写入缓存（带 TTL）；
 * 写策略：更新/删除数据后直接删除对应缓存，下次读取时回源重建。
 */
@Slf4j
@Service
public class CacheService {

    @Autowired
    private StringRedisTemplate stringRedisTemplate;

    private static final ObjectMapper MAPPER = new ObjectMapper().registerModule(new JavaTimeModule());

    /** 缓存命中/未命中计数（用于命中率统计上报） */
    private final AtomicLong hitCount = new AtomicLong();
    private final AtomicLong missCount = new AtomicLong();

    /**
     * 读缓存（单对象），未命中返回 null 并计数
     */
    public <T> T get(String key, Class<T> clazz) {
        try {
            String json = stringRedisTemplate.opsForValue().get(key);
            if (json != null) {
                hitCount.incrementAndGet();
                return MAPPER.readValue(json, clazz);
            }
        } catch (Exception e) {
            log.warn("缓存读取异常, key={}: {}", key, e.getMessage());
        }
        missCount.incrementAndGet();
        return null;
    }

    /**
     * 读缓存（List 泛型集合）
     */
    @SuppressWarnings("unchecked")
    public <T> List<T> getList(String key, Class<T> elementClass) {
        try {
            String json = stringRedisTemplate.opsForValue().get(key);
            if (json != null) {
                hitCount.incrementAndGet();
                return (List<T>) MAPPER.readValue(json,
                        MAPPER.getTypeFactory().constructCollectionType(List.class, elementClass));
            }
        } catch (Exception e) {
            log.warn("缓存读取异常, key={}: {}", key, e.getMessage());
        }
        missCount.incrementAndGet();
        return null;
    }

    /**
     * 写缓存（带 TTL，防止与数据库长期不一致）
     */
    public void set(String key, Object value, Duration ttl) {
        try {
            stringRedisTemplate.opsForValue().set(key, MAPPER.writeValueAsString(value), ttl);
        } catch (Exception e) {
            log.warn("缓存写入异常, key={}: {}", key, e.getMessage());
        }
    }

    /**
     * 删除缓存（更新/删除数据后调用，保证 Cache Aside 一致性）
     */
    public void evict(String key) {
        try {
            stringRedisTemplate.delete(key);
        } catch (Exception e) {
            log.warn("缓存删除异常, key={}: {}", key, e.getMessage());
        }
    }

    /**
     * 按前缀批量删除（如商品更新后清除所有分类维度的商品缓存）
     */
    public void evictByPrefix(String prefix) {
        try {
            java.util.Set<String> keys = stringRedisTemplate.keys(prefix + "*");
            if (keys != null && !keys.isEmpty()) {
                stringRedisTemplate.delete(keys);
            }
        } catch (Exception e) {
            log.warn("缓存批量删除异常, prefix={}: {}", prefix, e.getMessage());
        }
    }

    /**
     * 命中率统计：hit / (hit + miss)
     */
    public double hitRatio() {
        long hit = hitCount.get();
        long miss = missCount.get();
        long total = hit + miss;
        return total == 0 ? 0.0 : Math.round(hit * 10000.0 / total) / 100.0;
    }

    public long getHitCount() {
        return hitCount.get();
    }

    public long getMissCount() {
        return missCount.get();
    }
}
