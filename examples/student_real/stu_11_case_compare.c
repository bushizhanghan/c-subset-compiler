/*
 * 来源：学生写的（BUPT， captain 转交，第十一份 ）
 * 场景：字符串相等性比较（忽略大小写）
 * 代码完全正确（gcc 零诊断），但暴露了 csub 的 E16 严重误报：scanf 传数组名被误判成"漏写 &"。已在本次修复
 * 规模：约 20 行，2 个函数
 */
#include <stdio.h>
#include <string.h>
#define MAXN 20

int to_lower(int c) {
    if (c >= 'A' && c <= 'Z') return c + ('a' - 'A');
    return c;
}

int main(void) {
    char a[MAXN + 1], b[MAXN + 1];
    if (scanf("%20s %20s", a, b) != 2) return 1;

    if (strlen(a) != strlen(b)) { printf("NO"); return 0; }

    for (size_t i = 0; a[i]; i++)
        if (to_lower((unsigned char)a[i]) != to_lower((unsigned char)b[i])) {
            printf("NO"); return 0;
        }
    printf("YES");
    return 0;
}
