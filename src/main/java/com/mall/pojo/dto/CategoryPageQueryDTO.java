package com.mall.pojo.dto;

import lombok.Data;

/**
 * 分类分页查询 DTO
 */
@Data
public class CategoryPageQueryDTO {

    private Integer page = 1;
    private Integer pageSize = 10;
    private String name;
    private Integer type;
}
