/* ===========================================================================
 * madd.c   —— 实验一 内容三
 *
 * 学号：2025212285
 *   百位 x = 2 ，个位 y = 5
 *   a[i] = i - 50
 *   b[i] = i + y = i + 5
 *   数组长度 LEN = 100，a / b 均定义在 madd() 函数内部
 *
 * 需要单步观察的目标：
 *   xy = 25
 *   a[25] = 25 - 50 = -25          十六进制 0xffffffe7
 *   b[25] = 25 +  5 =  30          十六进制 0x1e
 *   a[25] + b[25]   =   5          十六进制 0x5
 *
 *   madd() 返回值 = SUM(i-50) + SUM(i+5)
 *                 = (4950-5000) + (4950+500) = -50 + 5450 = 5400
 *
 * 【编译 —— 题目给的命令会失败，必须加 -no-pie】
 *   题目原命令（本平台链接失败）：
 *     gcc -g -fno-pie -fno-stack-protector madd.c -o madd
 *   报错：
 *     /usr/bin/ld: relocation R_X86_64_32 against `.rodata' can not be used
 *     when making a PIE object; recompile with -fPIC
 *   原因：本平台 gcc 配置了 enable-default-pie，-fno-pie 只管编译阶段，
 *         链接阶段仍需 -no-pie 才能关掉 PIE。
 *   实际可用命令：
 *     gcc -g -fno-pie -fno-stack-protector -no-pie madd.c -o madd
 *
 *   不要加 -O，保持 -O0，反汇编才能和源码逐行对应。
 *
 * 【运行】
 *   ./madd        →  madd() = 5400
 *
 * 【反汇编】
 *   objdump -d -S madd > madd.txt
 * ===========================================================================*/
#include <stdio.h>

#define LEN 100

int madd(void)
{
    int a[LEN];
    int b[LEN];
    int i;
    int sum = 0;

    /* 填充两个数组 */
    for (i = 0; i < LEN; i++) {
        a[i] = i - 50;
        b[i] = i + 5;              /* y = 5：学号 2025212285 的个位 */
    }

    /* 求和：a[i] + b[i] 对应的汇编指令就在这个循环里 */
    for (i = 0; i < LEN; i++) {
        sum += a[i] + b[i];
    }

    return sum;
}

int main(void)
{
    printf("madd() = %d\n", madd());
    return 0;
}
