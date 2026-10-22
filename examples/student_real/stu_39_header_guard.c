/*
 * 来源：构造样本（多文件项目风格 + 头文件保护）
 * 场景：头文件/实现分离 + include 保护 + static inline
 * 已知问题：未知
 * 规模：约 60 行
 * 特点：#ifndef/#define/#endif 头文件保护、
 *       #include "本地头.h"（预处理器会忽略）、
 *       static inline 函数、extern 声明、条件编译、
 *       宏里带续行 \\、多层嵌套宏
 * 价值：验证头文件保护 + static inline + 多文件风格零误报
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ===== 模拟头文件 stack.h ===== */
#ifndef STACK_H
#define STACK_H

#define STACK_MAX 64

typedef struct {
    int data[STACK_MAX];
    int top;
} Stack;

void stack_init(Stack *s);
int stack_push(Stack *s, int v);
int stack_pop(Stack *s, int *out);
int stack_empty(const Stack *s);

#endif /* STACK_H */

/* ===== 模拟头文件 util.h ===== */
#ifndef UTIL_H
#define UTIL_H

#define CLAMP(x, lo, hi) \
    ((x) < (lo) ? (lo) : ((x) > (hi) ? (hi) : (x)))

#define IS_EVEN(n) (((n) % 2) == 0)

static inline int imax(int a, int b)
{
    return a > b ? a : b;
}

static inline int imin(int a, int b)
{
    return a < b ? a : b;
}

#endif /* UTIL_H */

/* ===== 实现 ===== */
static int g_push_count = 0;

void stack_init(Stack *s)
{
    s->top = 0;
}

int stack_push(Stack *s, int v)
{
    if (s->top >= STACK_MAX) return 0;
    s->data[s->top++] = v;
    g_push_count++;
    return 1;
}

int stack_pop(Stack *s, int *out)
{
    if (s->top <= 0) return 0;
    *out = s->data[--s->top];
    return 1;
}

int stack_empty(const Stack *s)
{
    return s->top == 0;
}

int main(void)
{
    Stack st;
    int v;
    int i;

    stack_init(&st);
    for (i = 0; i < 10; i++) {
        stack_push(&st, i * 3);
    }

    while (!stack_empty(&st)) {
        if (stack_pop(&st, &v)) {
            printf("%d ", CLAMP(v, 0, 15));
        }
    }
    printf("\n");

    printf("even=%d max=%d min=%d pushes=%d\n",
           IS_EVEN(7), imax(3, 9), imin(3, 9), g_push_count);

    return 0;
}
