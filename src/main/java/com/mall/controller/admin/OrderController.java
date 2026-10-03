package com.mall.controller.admin;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.annotation.AutoLog;
import com.mall.common.result.Result;
import com.mall.pojo.dto.OrdersPageQueryDTO;
import com.mall.pojo.entity.Orders;
import com.mall.pojo.vo.OrderStatisticsVO;
import com.mall.pojo.vo.OrderVO;
import com.mall.service.OrderService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

/**
 * 管理端 - 订单
 */
@RestController
@RequestMapping("/admin/order")
public class OrderController {

    @Autowired
    private OrderService orderService;

    @GetMapping("/page")
    public Result<Page<Orders>> page(OrdersPageQueryDTO dto) {
        return Result.success(orderService.adminPage(dto));
    }

    @GetMapping("/details/{id}")
    public Result<OrderVO> details(@PathVariable Long id) {
        return Result.success(orderService.details(id, null));
    }

    @AutoLog(module = "订单模块", type = "发货")
    @PutMapping("/accept/{id}")
    public Result<Void> accept(@PathVariable Long id) {
        orderService.accept(id);
        return Result.success();
    }

    @AutoLog(module = "订单模块", type = "取消订单")
    @PutMapping("/reject/{id}")
    public Result<Void> reject(@PathVariable Long id, String reason) {
        orderService.reject(id, reason);
        return Result.success();
    }

    @AutoLog(module = "订单模块", type = "配送")
    @PutMapping("/delivery/{id}")
    public Result<Void> delivery(@PathVariable Long id) {
        orderService.delivery(id);
        return Result.success();
    }

    @AutoLog(module = "订单模块", type = "完成")
    @PutMapping("/complete/{id}")
    public Result<Void> complete(@PathVariable Long id) {
        orderService.complete(id);
        return Result.success();
    }

    @GetMapping("/statistics")
    public Result<OrderStatisticsVO> statistics() {
        return Result.success(orderService.statistics());
    }
}
