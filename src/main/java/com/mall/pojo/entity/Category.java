package com.mall.pojo.entity;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 分类
 */
@Data
public class Category implements Serializable {

    private Long id;
    private String name;
    /** 类型 1商品分类 2组合套装分类 */
    private Integer type;
    private Integer sort;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
}
