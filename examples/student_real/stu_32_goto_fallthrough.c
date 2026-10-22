/*
 * 来源：构造样本（覆盖剩余未验证语法）
 * 场景：goto 清理 + switch fallthrough + 状态机 + 复合赋值位运算
 * 已知问题：未知
 * 规模：约 75 行，4 个函数
 * 特点：goto cleanup 模式（C 错误处理标准写法）、
 *       switch 有意 fallthrough（case 穿透）、
 *       case 里定义变量、switch 嵌套、位运算复合赋值
 *       |= &= ^= <<= >>=、逗号表达式、三目嵌套
 * 价值：验证 goto 清理 + fallthrough + 位运算复合赋值零误报
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* goto cleanup 模式：C 的错误处理标准写法 */
int process_file(const char *name)
{
    FILE *fp = NULL;
    char *buf = NULL;
    int rc = -1;

    fp = fopen(name, "r");
    if (fp == NULL) goto cleanup;

    buf = malloc(256);
    if (buf == NULL) goto cleanup;

    if (fgets(buf, 256, fp) == NULL) goto cleanup;
    printf("%s", buf);
    rc = 0;

cleanup:
    free(buf);
    if (fp != NULL) fclose(fp);
    return rc;
}

/* switch 有意 fallthrough */
const char *classify(int score)
{
    switch (score / 10) {
    case 10:
    case 9:
        return "A";
    case 8:
    case 7:
        return "B";
    case 6:
        /* 有意穿透：60-69 也归 C */
    case 5:
        return "C";
    default:
        return "F";
    }
}

/* 状态机 + 位运算复合赋值 */
unsigned int pack_flags(int state, unsigned int acc)
{
    switch (state) {
    case 0:
        acc |= 1u;
        /* fallthrough: 0 也要做 1 的处理 */
    case 1:
        acc &= ~2u;
        acc ^= 4u;
        break;
    case 2:
        acc <<= 1;
        acc >>= 2;
        break;
    default:
        acc = 0;
        break;
    }
    return acc;
}

/* 三目嵌套 + 逗号表达式 */
int clamp_to_range(int v, int lo, int hi)
{
    int t = (v < lo) ? lo : (v > hi) ? hi : v;
    return t;
}

int main(void)
{
    unsigned int acc = 0xFF;
    int i;
    for (i = 0; i < 3; i++) {
        acc = pack_flags(i, acc);
    }
    printf("acc=%u cls=%s\n", acc, classify(67));
    printf("clamp=%d\n", clamp_to_range(15, 0, 10));
    return 0;
}
