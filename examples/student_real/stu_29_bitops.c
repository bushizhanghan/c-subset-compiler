/*
 * 来源：GitHub（ALX low_level_programming + NHL95PC，合并单文件）
 * 场景：位运算递归 + 指针转换判断字节序 + qsort 回调
 * 已知问题：未知（学生课程作业 / 游戏工具库）
 * 规模：约 40 行，3 个函数
 * 特点：unsigned long int 递归右移、n & 1 位运算、
 *       (char*)&x 指针强转看字节序、const void* 回调里
 *       强制转 int* 再解引用、_putchar 外部函数
 * 价值：验证位运算表达式 + 指针转换链 + const void*
 */
#include <stdio.h>
#include <stdlib.h>

/* qsort 回调：const void* 转 int* 再解引用 */
int CmpInt(const void *a, const void *b)
{
    int r;
    r = 0;
    if (*(int *)a > *(int *)b) r = 1;
    else if (*(int *)a < *(int *)b) r = -1;
    return r;
}

/* 位运算递归打印二进制 */
void print_binary(unsigned long int n)
{
    if (n > 1)
        print_binary(n >> 1);
    putchar('0' + (n & 1));
}

/* 指针强转判断字节序 */
int get_endianness(void)
{
    unsigned int x = 1;
    char *c;
    c = (char *) &x;
    return ((int) *c);
}

/* qsort 实际使用 */
int main(void)
{
    int arr[5] = {5, 3, 1, 4, 2};
    int i;
    qsort(arr, 5, sizeof(int), CmpInt);
    for (i = 0; i < 5; i++) {
        printf("%d ", arr[i]);
    }
    printf("\n");
    print_binary(10UL);
    printf("\nendian=%d\n", get_endianness());
    return 0;
}
