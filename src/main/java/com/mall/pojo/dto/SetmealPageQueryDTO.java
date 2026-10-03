package com.mall.pojo.dto;

import lombok.Data;

/**
 * 组合套装分页查询 DTO
 */
@Data
public class SetmealPageQueryDTO {

    private Integer page = 1;
    private Integer pageSize = 10;
    private String name;
    private Long categoryId;
    private Integer status;
}
