package com.mall.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.common.constant.MessageConstant;
import com.mall.common.exception.DeletionNotAllowedException;
import com.mall.mapper.DishFlavorMapper;
import com.mall.mapper.DishMapper;
import com.mall.mapper.SetmealDishMapper;
import com.mall.pojo.dto.DishDTO;
import com.mall.pojo.dto.DishPageQueryDTO;
import com.mall.pojo.entity.Dish;
import com.mall.pojo.entity.DishFlavor;
import com.mall.pojo.entity.SetmealDish;
import com.mall.pojo.vo.DishVO;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.CollectionUtils;
import org.springframework.util.StringUtils;

import java.time.Duration;
import java.util.List;

/**
 * 商品服务：
 * 1. 级联保存/更新喜好（@Transactional 多表写入）
 * 2. 删除前置校验（起售中 / 组合套装关联）
 * 3. Cache Aside 缓存：按分类维度缓存商品列表，更新/删除后清除
 */
@Service
public class DishService {

    /** 商品分类维度缓存 key 前缀 */
    public static final String CACHE_PREFIX = "dish:cache:list:";

    private static final Duration CACHE_TTL = Duration.ofMinutes(30);

    @Autowired
    private DishMapper dishMapper;
    @Autowired
    private DishFlavorMapper dishFlavorMapper;
    @Autowired
    private SetmealDishMapper setmealDishMapper;
    @Autowired
    private CacheService cacheService;

    /**
     * 新增商品 + 喜好：两表写入在同一事务内，喜好插入失败则整体回滚
     */
    @Transactional
    public void saveWithFlavor(DishDTO dishDTO) {
        Dish dish = new Dish();
        BeanUtils.copyProperties(dishDTO, dish);
        dishMapper.insert(dish);
        insertFlavors(dish.getId(), dishDTO.getFlavors());
    }

    /**
     * 修改商品 + 喜好：先删后插喜好，事务内完成；同时清除商品/组合套装相关缓存
     */
    @Transactional
    public void updateWithFlavor(DishDTO dishDTO) {
        Dish dish = new Dish();
        BeanUtils.copyProperties(dishDTO, dish);
        dishMapper.updateById(dish);
        dishFlavorMapper.delete(new LambdaQueryWrapper<DishFlavor>().eq(DishFlavor::getDishId, dish.getId()));
        insertFlavors(dish.getId(), dishDTO.getFlavors());
        // Cache Aside：更新后删除缓存，下次读取回源重建
        cacheService.evictByPrefix(CACHE_PREFIX);
        cacheService.evictByPrefix(SetmealService.CACHE_PREFIX);
    }

    /**
     * 批量删除商品：起售中、被组合套装关联的商品不允许删除
     */
    @Transactional
    public void deleteBatch(List<Long> ids) {
        Long onSaleCount = dishMapper.selectCount(new LambdaQueryWrapper<Dish>()
                .in(Dish::getId, ids)
                .eq(Dish::getStatus, com.mall.common.constant.StatusConstant.ENABLE));
        if (onSaleCount > 0) {
            throw new DeletionNotAllowedException(MessageConstant.DISH_ON_SALE);
        }
        Long inSetmealCount = setmealDishMapper.selectCount(
                new LambdaQueryWrapper<SetmealDish>().in(SetmealDish::getDishId, ids));
        if (inSetmealCount > 0) {
            throw new DeletionNotAllowedException(MessageConstant.DISH_IN_SETMEAL);
        }
        dishMapper.deleteBatchIds(ids);
        dishFlavorMapper.delete(new LambdaQueryWrapper<DishFlavor>().in(DishFlavor::getDishId, ids));
        cacheService.evictByPrefix(CACHE_PREFIX);
    }

    /**
     * 分页查询：dish LEFT JOIN category（多表关联分页）
     */
    public Page<DishVO> page(DishPageQueryDTO dto) {
        Page<DishVO> page = new Page<>(dto.getPage(), dto.getPageSize());
        return dishMapper.pageQuery(page, dto.getName(), dto.getCategoryId(), dto.getStatus());
    }

    /**
     * 按分类查询商品（含喜好）：Cache Aside 读路径
     * 先查缓存 -> 未命中回源数据库 -> 写回缓存（TTL 30 分钟）
     */
    @SuppressWarnings("unchecked")
    public List<DishVO> listByCategoryId(Long categoryId) {
        String key = CACHE_PREFIX + categoryId;
        List<DishVO> cached = cacheService.getList(key, DishVO.class);
        if (cached != null) {
            return cached;
        }
        List<Dish> dishes = dishMapper.selectList(new LambdaQueryWrapper<Dish>()
                .eq(Dish::getCategoryId, categoryId)
                .orderByDesc(Dish::getUpdateTime));
        List<DishVO> result = dishes.stream().map(dish -> {
            DishVO vo = new DishVO();
            BeanUtils.copyProperties(dish, vo);
            vo.setFlavors(dishFlavorMapper.selectList(
                    new LambdaQueryWrapper<DishFlavor>().eq(DishFlavor::getDishId, dish.getId())));
            return vo;
        }).collect(java.util.stream.Collectors.toList());
        cacheService.set(key, result, CACHE_TTL);
        return result;
    }

    /**
     * 起售/停售：变更后清除缓存
     */
    public void startOrStop(Integer status, Long id) {
        Dish dish = new Dish();
        dish.setId(id);
        dish.setStatus(status);
        dishMapper.updateById(dish);
        cacheService.evictByPrefix(CACHE_PREFIX);
        cacheService.evictByPrefix(SetmealService.CACHE_PREFIX);
    }

    private void insertFlavors(Long dishId, List<DishFlavor> flavors) {
        if (!CollectionUtils.isEmpty(flavors)) {
            flavors.forEach(f -> f.setDishId(dishId));
            flavors.forEach(dishFlavorMapper::insert);
        }
    }
}
