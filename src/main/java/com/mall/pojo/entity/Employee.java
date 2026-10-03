package com.mall.pojo.entity;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 员工
 */
@Data
public class Employee implements Serializable {

    private Long id;
    private String name;
    private String username;
    private String password;
    private String phone;
    private Integer status;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
}
