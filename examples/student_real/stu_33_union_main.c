/*
 * 来源：GitHub（bhagavansprasad/students，学生练习）
 * 场景：union + 函数内 typedef + 隐式 int main
 * 已知问题：未知
 * 规模：约 45 行
 * 特点：typedef union 多类型成员、union 初始化 {65}、
 *       union 成员依次赋值（后者覆盖前者）、
 *       函数内 typedef、main() 无返回类型（隐式 int）、
 *       union 整体作 printf 参数
 * 价值：验证 union 支持 + 隐式 int main
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

int main()
{
    typedef union
    {
        char b[10];
        int a;
        float c;
    } Union;

    Union x, y = {65};

    x.a = 50;
    x.c = 1.5;
    strcpy(x.b, "hello");
    printf(" union x: %d %s %f \n", x.a, x.b, x.c);
    printf(" union y: %d %s %f \n", y.a, y.b, y.c);

    /* 二级指针分配矩阵 */
    {
        int **m;
        int i, j;
        m = malloc(sizeof(int *) * 3);
        for (i = 0; i < 3; i++) {
            m[i] = malloc(sizeof(int) * 3);
            for (j = 0; j < 3; j++) {
                m[i][j] = i * 3 + j;
            }
        }
        printf("m[2][2]=%d\n", m[2][2]);
        for (i = 0; i < 3; i++) {
            free(m[i]);
        }
        free(m);
    }

    return 0;
}

