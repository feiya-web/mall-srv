package com.mall.controller.admin;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.annotation.AutoLog;
import com.mall.common.result.Result;
import com.mall.pojo.dto.DishDTO;
import com.mall.pojo.dto.DishPageQueryDTO;
import com.mall.pojo.vo.DishVO;
import com.mall.service.DishService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 管理端 - 商品
 */
@RestController
@RequestMapping("/admin/dish")
public class DishController {

    @Autowired
    private DishService dishService;

    @AutoLog(module = "商品模块", type = "新增")
    @PostMapping
    public Result<Void> save(@RequestBody DishDTO dishDTO) {
        dishService.saveWithFlavor(dishDTO);
        return Result.success();
    }

    @AutoLog(module = "商品模块", type = "修改")
    @PutMapping
    public Result<Void> update(@RequestBody DishDTO dishDTO) {
        dishService.updateWithFlavor(dishDTO);
        return Result.success();
    }

    @AutoLog(module = "商品模块", type = "批量删除")
    @DeleteMapping
    public Result<Void> deleteBatch(@RequestParam List<Long> ids) {
        dishService.deleteBatch(ids);
        return Result.success();
    }

    @GetMapping("/page")
    public Result<Page<DishVO>> page(DishPageQueryDTO dto) {
        return Result.success(dishService.page(dto));
    }

    @GetMapping("/list")
    public Result<List<DishVO>> listByCategoryId(Long categoryId) {
        return Result.success(dishService.listByCategoryId(categoryId));
    }

    @AutoLog(module = "商品模块", type = "上下架")
    @PostMapping("/status/{status}")
    public Result<Void> startOrStop(@PathVariable Integer status, Long id) {
        dishService.startOrStop(status, id);
        return Result.success();
    }
}
