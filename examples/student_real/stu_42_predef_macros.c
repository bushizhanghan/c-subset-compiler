/*
 * 来源：构造样本（覆盖预处理与字面量边界）
 * 场景：预定义宏 + 宽字符 + 字符串拼接 + 三元链
 * 已知问题：未知
 * 规模：约 60 行
 * 特点：#error/#warning 指令、__LINE__/__FILE__/__func__、
 *       宽字符字符串 L"..."、字符串字面量自动拼接
 *       "abc" "def"、三元链 a?b:c?d:e、
 *       sizeof(表达式) 不加括号、字符数组 vs 指针
 * 价值：验证预处理指令 + 宽字符 + 字面量特性零误报
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* 预定义宏（预处理器提供） */
#define STRINGIFY(x) #x
#define CONCAT(a, b) a##b

/* 字符串字面量跨行拼接 */
const char *usage =
    "usage: prog [options] file\n"
    "  -h  show help\n"
    "  -v  verbose\n";

int main(void)
{
    /* 预定义宏：__func__ 是当前函数名（编译器提供） */
    printf("in %s\n", __func__);
    printf("at %s:%d\n", __FILE__, __LINE__);

    /* 三元链 */
    {
        int score = 75;
        const char *grade = score >= 90 ? "A"
                         : score >= 80 ? "B"
                         : score >= 70 ? "C"
                         : "F";
        printf("grade=%s\n", grade);
    }

    /* sizeof(表达式) 不加括号 */
    {
        int x = 5;
        printf("sizeof x=%zu\n", sizeof x);
        printf("sizeof x+1=%zu\n", sizeof(x + 1));
    }

    /* 字符数组 vs 字符指针 */
    {
        char arr[] = "hello";       /* 可修改的副本 */
        const char *p = "hello";    /* 只读的指针 */
        printf("arr=%s p=%s len=%zu/%zu\n", arr, p,
               sizeof arr, strlen(p));
    }

    /* 宏拼接 */
    {
        int CONCAT(va, r) = 42;
        printf("var=%d\n", var);
    }

    /* 字符串化 */
    printf("%s\n", STRINGIFY(hello world));

    printf("%s", usage);

    return 0;
}
