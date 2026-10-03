package com.mall.controller.admin;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.annotation.AutoLog;
import com.mall.common.result.Result;
import com.mall.pojo.dto.SetmealDTO;
import com.mall.pojo.dto.SetmealPageQueryDTO;
import com.mall.pojo.vo.SetmealVO;
import com.mall.service.SetmealService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 管理端 - 组合套装
 */
@RestController
@RequestMapping("/admin/setmeal")
public class SetmealController {

    @Autowired
    private SetmealService setmealService;

    @AutoLog(module = "组合套装模块", type = "新增")
    @PostMapping
    public Result<Void> save(@RequestBody SetmealDTO setmealDTO) {
        setmealService.saveWithDishes(setmealDTO);
        return Result.success();
    }

    @AutoLog(module = "组合套装模块", type = "修改")
    @PutMapping
    public Result<Void> update(@RequestBody SetmealDTO setmealDTO) {
        setmealService.updateWithDishes(setmealDTO);
        return Result.success();
    }

    @AutoLog(module = "组合套装模块", type = "批量删除")
    @DeleteMapping
    public Result<Void> deleteBatch(@RequestParam List<Long> ids) {
        setmealService.deleteBatch(ids);
        return Result.success();
    }

    @GetMapping("/page")
    public Result<Page<SetmealVO>> page(SetmealPageQueryDTO dto) {
        return Result.success(setmealService.page(dto));
    }

    @GetMapping("/{id}")
    public Result<SetmealVO> getById(@PathVariable Long id) {
        return Result.success(setmealService.getByIdWithDishes(id));
    }

    @AutoLog(module = "组合套装模块", type = "上下架")
    @PostMapping("/status/{status}")
    public Result<Void> startOrStop(@PathVariable Integer status, Long id) {
        setmealService.startOrStop(status, id);
        return Result.success();
    }
}
