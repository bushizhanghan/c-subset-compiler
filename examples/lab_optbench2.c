/* ============================================================================
 * optbench.c  ——  实验一「内容四」
 *
 * 选取类型：高内存需求型（Cache 访存效率）
 * 算法：二维数组拷贝的两种循环顺序（报告模板插图中给出的 copyij / copyji）
 *
 *   copyij : for (i) for (j)  dst[i][j] = src[i][j];   按行连续访问，Cache 友好
 *   copyji : for (j) for (i)  dst[i][j] = src[i][j];   按列跳跃访问，Cache 不友好
 *
 * N = 2048 时每个数组 2048*2048*4 = 16 MiB，一行 8 KiB。
 * copyji 每访问一个元素就要跨过 8 KiB，几乎每次都 Cache Miss。
 *
 * 编译与运行（不同优化参数）：
 *   for opt in 0 1 2 3 s; do
 *       gcc -O$opt optbench.c -o bench_$opt
 *       echo "===== -O$opt ====="; ./bench_$opt
 *   done
 * ==========================================================================*/
#include <stdio.h>
#include <time.h>

#define N 2048

/* 不加 static：确保编译器不能把整个拷贝当成死代码删掉 */
int src[N][N];
int dst[N][N];

static double now_sec(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

/* i 外层、j 内层：地址连续递增，Cache 命中率高 */
static void copyij(void)
{
    int i, j;
    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            dst[i][j] = src[i][j];
}

/* j 外层、i 内层：地址每次跳 N*sizeof(int) = 8192 字节，Cache 命中率低 */
static void copyji(void)
{
    int i, j;
    for (j = 0; j < N; j++)
        for (i = 0; i < N; i++)
            dst[i][j] = src[i][j];
}

/* 读回结果，防止被优化掉，同时作为正确性校验 */
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
    printf("copyij (行优先, i外层) : %8.3f s   checksum=%ld\n", t1 - t0, checksum());

    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            dst[i][j] = 0;

    t0 = now_sec();
    copyji();
    t1 = now_sec();
    printf("copyji (列优先, j外层) : %8.3f s   checksum=%ld\n", t1 - t0, checksum());

    return 0;
}
