package com.mall.pojo.entity;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 用户（C端）
 */
@Data
public class User implements Serializable {

    private Long id;
    private String username;
    private String password;
    private String phone;
    private Integer status;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
}
