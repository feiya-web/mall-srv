package com.mall.controller.admin;

import com.mall.common.result.Result;
import com.mall.service.CacheService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;

/**
 * 管理端 - 缓存命中率统计
 * 压测后通过 hit/(hit+miss) 计算缓存命中率
 */
@RestController
@RequestMapping("/admin/cache")
public class CacheController {

    @Autowired
    private CacheService cacheService;

    @GetMapping("/stats")
    public Result<Map<String, Object>> stats() {
        Map<String, Object> stats = new HashMap<>();
        stats.put("hitCount", cacheService.getHitCount());
        stats.put("missCount", cacheService.getMissCount());
        stats.put("hitRatio", cacheService.hitRatio());
        return Result.success(stats);
    }
}
