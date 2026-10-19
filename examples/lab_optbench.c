/* ===========================================================================
 * optbench.c   —— 实验一 内容四
 *
 * 选取类型：高内存需求型（Cache 访存局部性）
 * 算法：二维数组拷贝的两种循环顺序 —— copyij / copyji
 *       （即报告模板插图中给出的那组对比代码）
 *
 *   copyij : for (i) for (j)  dst[i][j] = src[i][j];
 *            行优先连续访问，Cache 友好
 *   copyji : for (j) for (i)  dst[i][j] = src[i][j];
 *            列优先跳跃访问，每次跨 N*4 = 8192 字节，Cache 不友好
 *
 * N = 2048 时，每个数组 2048*2048*4 = 16 MiB，一行 8 KiB，
 * 两个数组合计 32 MiB —— 超过服务器 30 MB 的 L3 Cache。
 *
 * 【编译与运行】
 *   for opt in 0 1 2 3 s; do
 *       gcc -O$opt optbench.c -o ob_$opt
 *       echo "===== -O$opt ====="
 *       ./ob_$opt
 *   done
 *
 * 【服务器实测结果】
 *   优化等级 | copyij   | copyji   | 倍数
 *   -O0      | 0.0217 s | 0.1427 s | 6.58x
 *   -O1      | 0.0117 s | 0.1154 s | 9.86x
 *   -O2      | 0.0116 s | 0.1155 s | 9.96x
 *   -O3      | 0.0113 s | 0.0291 s | 2.58x   <-- 拐点
 *   -Os      | 0.0119 s | 0.1160 s | 9.75x
 * ===========================================================================*/
#include <stdio.h>
#include <time.h>

#define N 2048

/* 不加 static，且被 checksum() 读取：
   确保编译器不能把整个拷贝过程当死代码优化掉 */
int src[N][N];
int dst[N][N];

static double now_sec(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

/* i 外层、j 内层：地址连续递增 */
static void copyij(void)
{
    int i, j;
    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            dst[i][j] = src[i][j];
}

/* j 外层、i 内层：地址每次跳 8192 字节 */
static void copyji(void)
{
    int i, j;
    for (j = 0; j < N; j++)
        for (i = 0; i < N; i++)
            dst[i][j] = src[i][j];
}

/* 读回结果做校验 */
static long checksum(void)
{
    long s = 0;
    int i, j;
    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            s += dst[i][j];
    return s;
}

int main(void)
{
    int i, j;
    double t0, t1;

    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            src[i][j] = i * 3 + j;

    t0 = now_sec();
    copyij();
    t1 = now_sec();
    printf("copyij : %8.4f s  checksum=%ld\n", t1 - t0, checksum());

    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            dst[i][j] = 0;

    t0 = now_sec();
    copyji();
    t1 = now_sec();
    printf("copyji : %8.4f s  checksum=%ld\n", t1 - t0, checksum());

    return 0;
}
