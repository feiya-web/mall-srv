package com.mall.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.common.constant.MessageConstant;
import com.mall.common.constant.StatusConstant;
import com.mall.common.exception.DeletionNotAllowedException;
import com.mall.common.exception.ParameterException;
import com.mall.mapper.DishMapper;
import com.mall.mapper.SetmealDishMapper;
import com.mall.mapper.SetmealMapper;
import com.mall.pojo.dto.SetmealDTO;
import com.mall.pojo.dto.SetmealPageQueryDTO;
import com.mall.pojo.entity.Dish;
import com.mall.pojo.entity.Setmeal;
import com.mall.pojo.entity.SetmealDish;
import com.mall.pojo.vo.SetmealVO;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.CollectionUtils;

import java.time.Duration;
import java.util.List;
import java.util.stream.Collectors;

/**
 * 组合套装服务：级联维护组合套装明细 + Cache Aside 缓存
 */
@Service
public class SetmealService {

    public static final String CACHE_PREFIX = "setmeal:cache:list:";

    private static final Duration CACHE_TTL = Duration.ofMinutes(30);

    @Autowired
    private SetmealMapper setmealMapper;
    @Autowired
    private SetmealDishMapper setmealDishMapper;
    @Autowired
    private DishMapper dishMapper;
    @Autowired
    private CacheService cacheService;

    @Transactional
    public void saveWithDishes(SetmealDTO setmealDTO) {
        Setmeal setmeal = new Setmeal();
        BeanUtils.copyProperties(setmealDTO, setmeal);
        setmealMapper.insert(setmeal);
        insertDishes(setmeal.getId(), setmealDTO.getSetmealDishes());
    }

    @Transactional
    public void updateWithDishes(SetmealDTO setmealDTO) {
        Setmeal setmeal = new Setmeal();
        BeanUtils.copyProperties(setmealDTO, setmeal);
        setmealMapper.updateById(setmeal);
        setmealDishMapper.delete(new LambdaQueryWrapper<SetmealDish>()
                .eq(SetmealDish::getSetmealId, setmeal.getId()));
        insertDishes(setmeal.getId(), setmealDTO.getSetmealDishes());
        cacheService.evictByPrefix(CACHE_PREFIX);
    }

    /**
     * 批量删除组合套装：起售中的组合套装不允许删除
     */
    @Transactional
    public void deleteBatch(List<Long> ids) {
        Long onSaleCount = setmealMapper.selectCount(new LambdaQueryWrapper<Setmeal>()
                .in(Setmeal::getId, ids).eq(Setmeal::getStatus, StatusConstant.ENABLE));
        if (onSaleCount > 0) {
            throw new DeletionNotAllowedException(MessageConstant.SETMEAL_ON_SALE);
        }
        setmealMapper.deleteBatchIds(ids);
        setmealDishMapper.delete(new LambdaQueryWrapper<SetmealDish>()
                .in(SetmealDish::getSetmealId, ids));
        cacheService.evictByPrefix(CACHE_PREFIX);
    }

    public Page<SetmealVO> page(SetmealPageQueryDTO dto) {
        Page<SetmealVO> page = new Page<>(dto.getPage(), dto.getPageSize());
        return setmealMapper.pageQuery(page, dto.getName(), dto.getCategoryId(), dto.getStatus());
    }

    /**
     * 按分类查询起售组合套装：Cache Aside 读路径
     */
    @SuppressWarnings("unchecked")
    public List<Setmeal> listByCategoryId(Long categoryId) {
        String key = CACHE_PREFIX + categoryId;
        List<Setmeal> cached = cacheService.getList(key, Setmeal.class);
        if (cached != null) {
            return cached;
        }
        List<Setmeal> list = setmealMapper.selectList(new LambdaQueryWrapper<Setmeal>()
                .eq(Setmeal::getCategoryId, categoryId)
                .eq(Setmeal::getStatus, StatusConstant.ENABLE)
                .orderByDesc(Setmeal::getUpdateTime));
        cacheService.set(key, list, CACHE_TTL);
        return list;
    }

    public SetmealVO getByIdWithDishes(Long id) {
        Setmeal setmeal = setmealMapper.selectById(id);
        if (setmeal == null) {
            throw new ParameterException(MessageConstant.ORDER_NOT_FOUND);
        }
        SetmealVO vo = new SetmealVO();
        BeanUtils.copyProperties(setmeal, vo);
        vo.setSetmealDishes(setmealDishMapper.selectList(
                new LambdaQueryWrapper<SetmealDish>().eq(SetmealDish::getSetmealId, id)));
        return vo;
    }

    /**
     * 起售/停售组合套装：起售前校验组合套装内所有商品必须起售
     */
    public void startOrStop(Integer status, Long id) {
        if (StatusConstant.ENABLE.equals(status)) {
            List<SetmealDish> dishes = setmealDishMapper.selectList(
                    new LambdaQueryWrapper<SetmealDish>().eq(SetmealDish::getSetmealId, id));
            List<Long> dishIds = dishes.stream().map(SetmealDish::getDishId).collect(Collectors.toList());
            if (!CollectionUtils.isEmpty(dishIds)) {
                Long disableCount = dishMapper.selectCount(new LambdaQueryWrapper<Dish>()
                        .in(Dish::getId, dishIds).eq(Dish::getStatus, StatusConstant.DISABLE));
                if (disableCount > 0) {
                    throw new DeletionNotAllowedException(MessageConstant.SETMEAL_CONTAINS_DISABLE_DISH);
                }
            }
        }
        Setmeal setmeal = new Setmeal();
        setmeal.setId(id);
        setmeal.setStatus(status);
        setmealMapper.updateById(setmeal);
        cacheService.evictByPrefix(CACHE_PREFIX);
    }

    private void insertDishes(Long setmealId, List<SetmealDish> setmealDishes) {
        if (!CollectionUtils.isEmpty(setmealDishes)) {
            setmealDishes.forEach(sd -> sd.setSetmealId(setmealId));
            setmealDishes.forEach(setmealDishMapper::insert);
        }
    }
}
