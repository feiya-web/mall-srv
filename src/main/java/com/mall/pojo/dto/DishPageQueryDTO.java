package com.mall.pojo.dto;

import lombok.Data;

/**
 * 商品分页查询 DTO
 */
@Data
public class DishPageQueryDTO {

    private Integer page = 1;
    private Integer pageSize = 10;
    private String name;
    private Long categoryId;
    private Integer status;
}
