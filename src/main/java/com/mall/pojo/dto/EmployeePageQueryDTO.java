package com.mall.pojo.dto;

import lombok.Data;

/**
 * 员工分页条件查询 DTO
 */
@Data
public class EmployeePageQueryDTO {

    private Integer page = 1;
    private Integer pageSize = 10;

    /** 姓名，模糊匹配；为空表示不限 */
    private String name;

    /** 状态 0禁用 1启用；null 表示不限 */
    private Integer status;
}
