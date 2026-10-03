package com.mall.config;

import com.baomidou.mybatisplus.annotation.DbType;
import com.baomidou.mybatisplus.extension.plugins.MybatisPlusInterceptor;
import com.baomidou.mybatisplus.extension.plugins.inner.PaginationInnerInterceptor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * MyBatis-Plus 配置：分页插件
 */
@Configuration
public class MybatisPlusConfig {

    /**
     * 全局分页上限，第二道防线。与 EmployeeService.MAX_PAGE_SIZE 保持一致；
     * 两者不一致时以更小的为准，不会互相放行。
     */
    private static final long GLOBAL_MAX_PAGE_SIZE = 100L;

    @Bean
    public MybatisPlusInterceptor mybatisPlusInterceptor() {
        MybatisPlusInterceptor interceptor = new MybatisPlusInterceptor();

        PaginationInnerInterceptor pagination = new PaginationInnerInterceptor(DbType.MYSQL);
        // handlerLimit() 的判定条件是 (size > limit || size < 0)，所以负数也夹得住，
        // 任何调用方（哪怕绕过 Service 直接调 mapper）都逃不掉。
        // 但它只是兜底，不能替代 Service 的参数归一 —— 原因见下面"意外收获"。
        pagination.setMaxLimit(GLOBAL_MAX_PAGE_SIZE);

        interceptor.addInnerInterceptor(pagination);
        return interceptor;
    }
}
