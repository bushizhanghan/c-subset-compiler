/* ============================================================================
 * optime.c  ——  自带高精度计时 + 循环次数可配置的性能测试程序
 *
 * 用途：一次编译，通过命令行参数指定优化等级无关的循环规模，
 *       程序内部用 clock_gettime 直接打印耗时，避免 shell time 的精度问题。
 *
 * 用法：
 *   gcc -O0 optime.c -o o0 && ./o0
 *   gcc -O1 optime.c -o o1 && ./o1
 *   ...
 *
 * 设计要点：
 *   1. 循环上界 2000*2000*2000/20 = 4e8，与课件 optimize.c 完全一致
 *   2. 程序内部重复测量 RUNS 次，输出 最小/平均 时间，减小噪声
 *   3. temp / result 都用 volatile 之外的方式保留语义，但允许编译器优化
 *      （这正是要观察的：不同 -O 等级下编译器如何处理）
 * ==========================================================================*/
#include <stdio.h>
#include <time.h>

#define ITER   (2000.0 * 2000.0 * 2000.0 / 20.0 + 2020)
#define RUNS   3

static double now_sec(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

int main(void)
{
    float counter, result, temp;
    double t[RUNS], sum = 0.0, best;
    int r;

    for (r = 0; r < RUNS; r++) {
        double t0 = now_sec();

        result = 0.0f;
        for (counter = 0.0f; counter < (float)ITER; counter += (5 - 1) / 4) {
            temp = counter / 1979.0f;
            result = counter;
        }

        t[r] = now_sec() - t0;
        sum += t[r];
        printf("  run %d : %8.4f s\n", r + 1, t[r]);
    }

    best = t[0];
    for (r = 1; r < RUNS; r++)
        if (t[r] < best)
            best = t[r];

    printf("  best   : %8.4f s\n", best);
    printf("  avg    : %8.4f s\n", sum / RUNS);
    printf("  result : %f\n", result);

    return 0;
}
