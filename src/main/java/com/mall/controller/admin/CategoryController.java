package com.mall.controller.admin;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.annotation.AutoLog;
import com.mall.common.result.Result;
import com.mall.pojo.dto.CategoryPageQueryDTO;
import com.mall.pojo.entity.Category;
import com.mall.service.CategoryService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 管理端 - 分类
 */
@RestController
@RequestMapping("/admin/category")
public class CategoryController {

    @Autowired
    private CategoryService categoryService;

    @AutoLog(module = "分类模块", type = "新增")
    @PostMapping
    public Result<Void> save(@RequestBody Category category) {
        categoryService.save(category);
        return Result.success();
    }

    @AutoLog(module = "分类模块", type = "修改")
    @PutMapping
    public Result<Void> update(@RequestBody Category category) {
        categoryService.update(category);
        return Result.success();
    }

    @AutoLog(module = "分类模块", type = "删除")
    @DeleteMapping
    public Result<Void> deleteById(Long id) {
        categoryService.deleteById(id);
        return Result.success();
    }

    @GetMapping("/page")
    public Result<Page<Category>> page(CategoryPageQueryDTO dto) {
        return Result.success(categoryService.page(dto));
    }

    @GetMapping("/list")
    public Result<List<Category>> list(Integer type) {
        return Result.success(categoryService.list(type));
    }
}
