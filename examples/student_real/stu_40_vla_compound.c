/*
 * 来源：构造样本（覆盖剩余 C99 特性）
 * 场景：VLA + 复合字面量 + for 内声明 + 混合声明
 * 已知问题：未知
 * 规模：约 60 行
 * 特点：int arr[n] 变长数组（C99）、
 *       (Pos){1,2} 复合字面量、for(int i=...) 循环内声明、
 *       int a = 1, *p = &a, arr[3] = {0}; 混合声明组、
 *       strlen/sizeof 常量表达式、switch 里 default 位置不定
 * 价值：验证 VLA + 复合字面量 + 混合声明零误报
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int r, c;
} Pos;

/* VLA：变长数组参数 */
int sum_vla(int n, int a[n])
{
    int i, s = 0;
    for (i = 0; i < n; i++) {
        s += a[i];
    }
    return s;
}

/* 复合字面量作参数 */
int dist(Pos a, Pos b)
{
    int dr = a.r - b.r;
    int dc = a.c - b.c;
    return dr * dr + dc * dc;
}

int main(void)
{
    int n = 5;
    /* VLA：运行时决定大小 */
    int vla[n];
    int i;

    for (i = 0; i < n; i++) {
        vla[i] = i * i;
    }
    printf("vla sum=%d\n", sum_vla(n, vla));

    /* 复合字面量 */
    printf("dist=%d\n", dist((Pos){0, 0}, (Pos){3, 4}));

    /* 混合声明组：变量 + 指针 + 数组 */
    {
        int a = 1, *p = &a, arr[3] = {7, 8, 9};
        printf("a=%d *p=%d arr[1]=%d\n", a, *p, arr[1]);
    }

    /* for 内声明 + 嵌套 */
    for (int j = 0; j < 3; j++) {
        for (int k = j; k < 3; k++) {
            if (j == k) continue;
            printf("%d%d ", j, k);
        }
    }
    printf("\n");

    /* sizeof 常量表达式 */
    {
        char buf[32];
        size_t len = strlen("hello");
        if (len < sizeof(buf)) {
            memcpy(buf, "hello", len + 1);
            printf("buf=%s len=%zu\n", buf, len);
        }
    }

    /* switch 的 default 不在最后 */
    switch (n) {
    default:
        printf("default first\n");
        break;
    case 5:
        printf("five\n");
        break;
    }

    return 0;
}
