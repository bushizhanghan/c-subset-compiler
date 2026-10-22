/*
 * 来源：构造样本（覆盖多级指针操作）
 * 场景：二级/三级指针 + 字符串数组操作 + 指针函数返回
 * 已知问题：未知
 * 规模：约 65 行
 * 特点：char **argv 风格遍历、int **pp 二级指针、
 *       指针的指针作参数（修改调用方的指针）、
 *       char *strs[] 字符串数组 + strcmp 排序、
 *       函数返回 char*（静态缓冲）、const char ** 遍历
 * 价值：验证多级指针零误报
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* 函数返回指针（静态缓冲） */
const char *status_str(int code)
{
    static const char *names[] = {"ok", "warn", "fail"};
    if (code < 0 || code > 2) return "unknown";
    return names[code];
}

/* 二级指针作参数：修改调用方的指针 */
int allocate_buf(char **out, size_t n)
{
    char *p = malloc(n);
    if (p == NULL) return -1;
    *out = p;
    return 0;
}

/* 二级指针释放 */
void free_buf(char **out)
{
    if (*out != NULL) {
        free(*out);
        *out = NULL;
    }
}

/* 字符串数组排序（冒泡，指针交换） */
void sort_strs(char *strs[], int n)
{
    int i, j;
    for (i = 0; i < n - 1; i++) {
        for (j = 0; j < n - 1 - i; j++) {
            if (strcmp(strs[j], strs[j + 1]) > 0) {
                char *t = strs[j];
                strs[j] = strs[j + 1];
                strs[j + 1] = t;
            }
        }
    }
}

/* 遍历 const char *数组 */
void print_all(const char *const *items, int n)
{
    int i;
    for (i = 0; i < n; i++) {
        printf("%s\n", items[i]);
    }
}

int main(void)
{
    char *buf = NULL;
    char *words[4] = {"banana", "apple", "cherry", "date"};
    int i;

    if (allocate_buf(&buf, 32) == 0) {
        strcpy(buf, "hello world");
        printf("buf=%s\n", buf);
    }

    sort_strs(words, 4);
    print_all((const char *const *)words, 4);

    for (i = 0; i < 3; i++) {
        printf("status[%d]=%s\n", i, status_str(i));
    }

    /* 二级指针解引用 */
    {
        int val = 42;
        int *p = &val;
        int **pp = &p;
        printf("**pp=%d\n", **pp);
    }

    free_buf(&buf);
    printf("buf after free=%s\n", buf == NULL ? "NULL" : "not null");

    return 0;
}
