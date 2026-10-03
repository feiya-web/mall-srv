package com.mall.common.utils;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.JwtException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * JWT 工具单元测试（对应异常用例「鉴权/参数边界」组）
 */
class JwtUtilTest {

    private static final String SECRET = "unit-test-jwt-secret-key-0123456789abcdef";
    private static final long TTL = 60_000;

    @Test
    @DisplayName("签发后可解析出正确的用户 id")
    void createAndParse() {
        String token = JwtUtil.createToken(SECRET, TTL, 1001L);
        Claims claims = JwtUtil.parseToken(SECRET, token);
        assertEquals("1001", claims.getSubject());
    }

    @Test
    @DisplayName("篡改的 token 解析失败")
    void tamperedTokenShouldThrow() {
        String token = JwtUtil.createToken(SECRET, TTL, 1L);
        String tampered = token.substring(0, token.length() - 2) + "xx";
        assertThrows(JwtException.class, () -> JwtUtil.parseToken(SECRET, tampered));
    }

    @Test
    @DisplayName("错误密钥的 token 解析失败")
    void wrongSecretShouldThrow() {
        String token = JwtUtil.createToken(SECRET, TTL, 1L);
        assertThrows(JwtException.class,
                () -> JwtUtil.parseToken("another-secret-key-0123456789abcdef000000", token));
    }

    @Test
    @DisplayName("过期 token 解析抛出 ExpiredJwtException")
    void expiredTokenShouldThrow() {
        String token = JwtUtil.createToken(SECRET, -1, 1L);
        assertThrows(ExpiredJwtException.class, () -> JwtUtil.parseToken(SECRET, token));
    }
}
