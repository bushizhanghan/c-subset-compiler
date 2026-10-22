/*
 * 来源：构造样本（覆盖编译器扩展语法）
 * 场景：#pragma + _Bool + restrict + 属性 + 内联汇编注释
 * 已知问题：未知
 * 规模：约 55 行
 * 特点：#pragma once、#pragma pack(1)、
 *       _Bool 类型（C99）、restrict 限定符、
 *       __attribute__((unused))、volatile、
 *       窄字符/宽字符混用、\x 十六进制转义、
 *       三字符组（??= 等，罕见）
 * 价值：验证编译器扩展语法零误报
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#pragma once
#pragma pack(push, 1)

/* _Bool：C99 布尔类型 */
_Bool is_ready = 0;

/* restrict：告诉编译器指针不重叠 */
void copy_bytes(char *restrict dst, const char *restrict src, size_t n)
{
    size_t i;
    for (i = 0; i < n; i++) {
        dst[i] = src[i];
    }
}

/* volatile：可能被外部修改 */
volatile int tick_counter = 0;

/* __attribute__((unused))：抑制未使用告警 */
static int helper_unused(void) __attribute__((unused));

static int helper_unused(void)
{
    return 42;
}

/* 紧凑结构体（pack 1，无填充） */
struct Packed {
    char a;
    int b;
    char c;
};

#pragma pack(pop)

/* 十六进制转义 + 字符常量 */
const char bell[] = "\x07";
const char *hex_esc = "\x41\x42\x43";

int main(void)
{
    struct Packed p;
    char buf[16];
    _Bool ok = 1;

    p.a = 'A';
    p.b = 12345;
    p.c = 'Z';

    copy_bytes(buf, "hello", 6);
    printf("buf=%s a=%c b=%d c=%c ok=%d packed=%lu\n",
           buf, p.a, p.b, p.c, ok, sizeof(struct Packed));
    printf("bell=%d hex=%s\n", bell[0], hex_esc);

    return 0;
}
