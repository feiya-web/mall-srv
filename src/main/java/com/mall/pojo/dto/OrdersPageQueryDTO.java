package com.mall.pojo.dto;

import lombok.Data;

import java.time.LocalDate;

/**
 * 订单分页查询 DTO（管理端）
 */
@Data
public class OrdersPageQueryDTO {

    private Integer page = 1;
    private Integer pageSize = 10;
    private String number;
    private Integer status;
    /** 下单日期起始（yyyy-MM-dd） */
    private LocalDate beginTime;
    /** 下单日期结束（yyyy-MM-dd） */
    private LocalDate endTime;
}
