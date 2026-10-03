package com.mall.common.utils;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * 密码工具单元测试（与建库脚本 SHA2('123456',256) 口径一致性验证）
 */
class PasswordUtilTest {

    @Test
    @DisplayName("SHA-256 摘要与 MySQL SHA2 口径一致")
    void matchesMysqlSha2() {
        // SELECT SHA2('123456', 256) 的期望值
        String expected = "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92";
        assertEquals(expected, PasswordUtil.encode("123456"));
        assertTrue(PasswordUtil.matches("123456", expected));
    }

    @Test
    @DisplayName("错误密码校验不通过")
    void wrongPasswordShouldNotMatch() {
        String encoded = PasswordUtil.encode("123456");
        assertFalse(PasswordUtil.matches("1234567", encoded));
    }

    @Test
    @DisplayName("同一明文摘要结果稳定（无随机盐）")
    void encodeIsDeterministic() {
        assertEquals(PasswordUtil.encode("abc"), PasswordUtil.encode("abc"));
    }
}
