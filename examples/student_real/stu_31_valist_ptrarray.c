/*
 * 来源：GitHub（libcmini printf + monty free.c，学生课程项目）
 * 场景：va_list 可变参数 + char** 指针数组 + 嵌套解引用
 * 已知问题：未知
 * 规模：约 60 行，5 个函数
 * 特点：va_list/va_start/va_end 宏、const char *fmt, ... 原型、
 *       char **argv 作参数、argv[i] 二级下标解引用、
 *       free 指针数组元素、NULL 哨兵循环
 * 价值：验证可变参数 + 指针数组 + 哨兵循环零误报
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>

/* va_list 转发 */
int my_printf(const char *fmt, ...)
{
    va_list va;
    int ret;
    va_start(va, fmt);
    ret = vprintf(fmt, va);
    va_end(va);
    return ret;
}

/* char** 指针数组释放，NULL 作哨兵 */
void free_strtow(char **argv)
{
    int i;
    if (argv != NULL) {
        for (i = 0; argv[i] != NULL; i++) {
            free(argv[i]);
        }
        free(argv[i]);
        free(argv);
    }
}

/* 指针数组构建 */
char **build_args(const char *src, const char *sep)
{
    char **out = malloc(sizeof(char *) * 16);
    char *tok;
    int i = 0;
    if (out == NULL) return NULL;
    tok = strtok((char *)src, sep);
    while (tok != NULL && i < 15) {
        out[i] = malloc(strlen(tok) + 1);
        if (out[i] != NULL) {
            strcpy(out[i], tok);
            i++;
        }
        tok = strtok(NULL, sep);
    }
    out[i] = NULL;
    return out;
}

/* 二级指针作参数修改 */
void set_first(char **p, char *v)
{
    *p = v;
}

int main(void)
{
    char buf[64];
    char **args;
    char *first = NULL;
    strcpy(buf, "a b c");
    args = build_args(buf, " ");
    if (args != NULL) {
        set_first(args, "X");
        first = args[0];
        my_printf("first=%s\n", first);
        free_strtow(args);
    }
    return 0;
}
