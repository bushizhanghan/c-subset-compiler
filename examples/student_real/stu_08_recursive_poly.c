/*
 * 来源：学生写的（BUPT， captain 转交，第八份 ）
 * 场景：递归求多项式（类切比雪夫递归）
 * 已知问题：n 为负时无限递归（无 n<0 保护）；斐波那契式重复递归，O(2^n) 复杂度，n 大时超时。代码逻辑本身正确
 * 规模：约 20 行，2 个函数
 */
#define _CRT_SECURE_NO_WARNINGS
#include<stdio.h>
int c(int n,int x) {
    if (n == 0) {
        return 1;
    }
    else if (n == 1) {
        return 2 * x;
    }
    else return 2 * x * (c(n - 1, x)) - 2 * (n - 1) * (c(n - 2, x));
}
int main() {
    int n, x;
    scanf("%d %d", &n, &x);
    printf("%d", c(n, x));
    return 0;
}
