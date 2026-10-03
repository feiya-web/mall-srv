package com.mall.pojo.dto;

import lombok.Data;

/**
 * 登录 DTO（员工/用户通用）
 */
@Data
public class LoginDTO {

    private String username;
    private String password;
}
