/*
 * 来源：构造样本（覆盖复杂表达式组合）
 * 场景：三目嵌套 + 逗号 + do-while + 复杂初始化
 * 已知问题：未知
 * 规模：约 60 行
 * 特点：逗号表达式 for(i=0,j=10; i<j; i++,j--)、
 *       do-while(0) 宏模式、嵌套三目 a?b?c:d:e、
 *       指定初始化器 {[0]=1, [2]=3}、.field = v、
 *       结构体嵌套初始化、数组的数组、
 *       ++i 与 i++ 混用、复合逻辑短路 &&
 * 价值：验证复杂表达式零误报
 */
#include <stdio.h>
#include <stdlib.h>

typedef struct {
    int x, y;
} Point;

typedef struct {
    Point top_left;
    Point bottom_right;
    int color;
} Rect;

int main(void)
{
    int i, j, sum = 0;
    int grid[3][3] = {{1, 2, 3}, {4, 5, 6}, {7, 8, 9}};
    int sparse[5] = {[0] = 10, [2] = 30, [4] = 50};
    Rect r = {{0, 0}, {100, 50}, 0xFF0000};
    Rect *rp = &r;

    /* 逗号表达式在 for 里 */
    for (i = 0, j = 10; i < j; i++, j--) {
        sum += i * j;
    }

    /* do-while(0) 宏模式 */
    do {
        if (sum < 0) break;
        sum += 1;
    } while (0);

    /* 嵌套三目 */
    {
        int v = sum;
        int sign = v > 0 ? 1 : (v < 0 ? -1 : 0);
        printf("sign=%d\n", sign);
    }

    /* 数组的数组 + 指定初始化 */
    printf("grid[1][2]=%d sparse[2]=%d\n", grid[1][2], sparse[2]);

    /* 结构体指针成员访问 */
    rp->color = 0x00FF00;
    rp->top_left.x = 5;
    printf("color=%d tl=(%d,%d)\n", rp->color, rp->top_left.x,
           rp->top_left.y);

    /* ++i / i++ 混用 */
    i = 0;
    sum = 0;
    while (i < 5) {
        sum += ++i;
    }
    printf("sum=%d\n", sum);

    /* 复合逻辑短路 */
    if (sum > 5 && grid[0][0] == 1 || sparse[0] == 10) {
        printf("logic ok\n");
    }

    return 0;
}
