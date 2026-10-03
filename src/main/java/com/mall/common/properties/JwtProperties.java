package com.mall.common.properties;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * JWT 配置属性
 */
@Data
@Component
@ConfigurationProperties(prefix = "mall.jwt")
public class JwtProperties {

    private String adminSecret;
    private long adminTtl;
    private String adminTokenName;

    private String userSecret;
    private long userTtl;
    private String userTokenName;
}
