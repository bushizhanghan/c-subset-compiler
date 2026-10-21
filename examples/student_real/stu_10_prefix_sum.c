/*
 * 来源：学生写的（BUPT， captain 转交，第十份 ）
 * 场景：前缀和/累加求和（双层循环）
 * 已知问题：算法正确但 O(n^2)：外层 n 次调用 sum(i)，内层再循环 i 次，n=1e5 时约 50 亿次操作必超时；局部变量 sum 与函数名 sum 同名，遮蔽函数
 * 规模：约 16 行，2 个函数
 */
#define _CRT_SECURE_NO_WARNINGS
#include<stdio.h>
int sum(int n) {
	int sum=0;
	for (int i = 1; i <= n; i++) {
		sum += i;
	}return sum;
}
int main() {
	int n;
	scanf("%d", &n);
	for (int i = 1; i <= n; i++) {
		printf("%d\n", sum(i));
	}return 0;
}
