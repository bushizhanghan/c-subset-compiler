#include <stdio.h>
#include <string.h>

#define MAX_STU 8
#define COLS 3
#define PASS_LINE 60

/* 学生记录：定长字符数组 + 浮点成绩 */
typedef struct student {
    int id;
    char name[16];
    float score;
} Student, *StudentPtr;

/* 成绩表：按行优先铺平的一维缓冲，行列数单独记录 */
typedef struct table {
    int rows;
    int cols;
    int cell[24];
} Table;

int fact(int n);
int fib(int n);
int gcd(int a, int b);
float avgOf(const int *data, int n);
void swapInt(int *a, int *b);
int rowSum(const Table *t, int row);
int tableTotal(const Table *t);
void fillTable(Table *t, int rows);
void showStudent(StudentPtr sp);
int gradeOf(int score);

/* ---------- 递归：阶乘 ---------- */

int fact(int n)
{
    if (n <= 1) {
        return 1;
    }
    return n * fact(n - 1);
}

/* ---------- 迭代：斐波那契 ---------- */

int fib(int n)
{
    int a = 0;
    int b = 1;
    int i;
    int t;

    for (i = 0; i < n; i++) {
        t = a + b;
        a = b;
        b = t;
    }
    return a;
}

/* ---------- 辗转相除求最大公约数 ---------- */

int gcd(int a, int b)
{
    int r;

    while (b != 0) {
        r = a % b;
        a = b;
        b = r;
    }
    return a;
}

/* ---------- 数组求平均：注意返回 float ---------- */

float avgOf(const int *data, int n)
{
    int i;
    int sum = 0;

    if (n <= 0) {
        return 0.0;
    }
    for (i = 0; i < n; i++) {
        sum = sum + data[i];
    }
    return sum * 1.0 / n;
}

/* ---------- 交换两个整数 ---------- */

void swapInt(int *a, int *b)
{
    int t = *a;

    *a = *b;
    *b = t;
}

/* ---------- 一维缓冲：按 row * cols + col 取元素 ---------- */

int rowSum(const Table *t, int row)
{
    int j;
    int s = 0;

    for (j = 0; j < t->cols; j++) {
        s = s + t->cell[row * t->cols + j];
    }
    return s;
}

/* ---------- 二维数组：全表求和 ---------- */

int tableTotal(const Table *t)
{
    int i, j, total = 0;

    for (i = 0; i < t->rows; i++) {
        for (j = 0; j < t->cols; j++) {
            total = total + t->cell[i * t->cols + j];
        }
    }
    return total;
}

/* ---------- 填表：写成常量下标会越界，这里用变量 ---------- */

void fillTable(Table *t, int rows)
{
    int i, j;

    t->rows = rows;
    t->cols = COLS;
    for (i = 0; i < rows; i++) {
        for (j = 0; j < COLS; j++) {
            t->cell[i * t->cols + j] = (i + 1) * 10 + j;
        }
    }
}

/* ---------- 打印一个学生 ---------- */

void showStudent(StudentPtr sp)
{
    printf("学号 %d  姓名 %s  成绩 %.1f\n", sp->id, sp->name, sp->score);
}

/* ---------- 分数转等级：用 switch 演示 break 的合法位置 ---------- */

int gradeOf(int score)
{
    int g;
    int band;

    if (score >= 90) {
        band = 9;
    } else if (score >= 80) {
        band = 8;
    } else if (score >= 70) {
        band = 7;
    } else if (score >= 60) {
        band = 6;
    } else {
        band = 0;
    }

    switch (band) {
    case 9:
        g = 5;
        break;
    case 8:
        g = 4;
        break;
    case 7:
        g = 3;
        break;
    case 6:
        g = 2;
        break;
    default:
        g = 1;
        break;
    }
    return g;
}

/* ---------- main ---------- */

int main(void)
{
    Student stu;
    StudentPtr sp = &stu;
    Table tab;
    int i, n = 4, total;
    int scores[MAX_STU];
    float mean;
    char again = 'n';

    stu.id = 1;
    strcpy(stu.name, "Zhang San");
    stu.score = 88.5;
    showStudent(sp);

    for (i = 0; i < n; i++) {
        scores[i] = PASS_LINE + i * 7;
    }
    mean = avgOf(scores, n);
    printf("平均分 %.2f\n", mean);

    fillTable(&tab, 3);
    total = tableTotal(&tab);
    printf("全表合计 %d\n", total);
    printf("第 2 行合计 %d\n", rowSum(&tab, 1));

    printf("5! = %d\n", fact(5));
    printf("fib(10) = %d\n", fib(10));
    printf("gcd(84,36) = %d\n", gcd(84, 36));

    i = gradeOf(88);
    printf("88 分对应等级 %d\n", i);

    swapInt(&scores[0], &scores[1]);

    do {
        printf("是否继续？(y/n)：");
        scanf(" %c", &again);
    } while (again == 'y');

    printf("程序结束。\n");
    return 0;
}
