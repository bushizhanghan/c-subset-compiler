/* ===========================================================================
 * optimize.c   —— 实验一 内容一 / 内容二
 *
 * 来源：课件 slide 74 的源程序，修正了两处笔误并把 float 改为 double。
 *
 * 【笔误 1】printf(Result is%lf\n,result);   缺少前引号
 * 【笔误 2】return 0；                        全角分号
 *
 * 【为什么 float 要改成 double —— 重要】
 *   课件原版用 float counter，循环上界 = 2000*2000*2000/20 + 2020 ≈ 4.0e8。
 *   IEEE 754 单精度 float 尾数只有 23 位，只能精确表示 2^24 = 16777216 以内的
 *   连续整数。当 counter 涨到 16777216 后，"counter += 1.0f" 的精确结果
 *   16777217 无法用 float 表示，按就近舍入又回到 16777216，增量被吃掉；
 *   而循环条件 counter < 400002016 恒成立 —— 程序进入【死循环】，永不结束。
 *   服务器实测：timeout 30 秒跑不完，循环 2 亿次后 counter 卡死在 16777216.0。
 *   改成 double（尾数 52 位）后正常终止，输出 Result is 400002019.000000。
 *
 * 【编译与运行】
 *   gcc optimize.c -o optimize_o        # -o 只指定输出文件名，无优化（= -O0）
 *   gcc -O optimize.c -o optimize_O     # -O 才开启优化（= -O1）
 *   time ./optimize_o
 *   time ./optimize_O
 *
 * 【内容二：不同优化参数】
 *   for opt in 0 1 2 3 s; do
 *       gcc -O$opt optimize.c -o opt_$opt
 *       echo "===== -O$opt ====="
 *       time ./opt_$opt
 *   done
 * ===========================================================================*/
#include <stdio.h>

int main(void)
{
    double counter;
    double result;
    double temp;

    for (counter = 0;
         counter < 2000.0 * 2000.0 * 2000.0 / 20.0 + 2020;
         counter += (5 - 1) / 4) {
        temp = counter / 1979;
        result = counter;
    }

    printf("Result is %lf\n", result);
    return 0;
}
