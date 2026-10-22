/*
 * 来源：GitHub（9alireza5/C-What-I-Do + shanchuann/CStudy 合成）
 * 场景：结构体数组 + 数组成员 + scanf 进结构体
 * 已知问题：未知
 * 规模：约 50 行
 * 特点：struct Student students[3] 结构体数组、
 *       students[i].name 成员访问（数组下标 + 点成员）、
 *       scanf("%s", students[i].name) 数组名衰减传参、
 *       scanf("%f", &students[i].score) 取成员地址、
 *       double scores[3] 数组成员、%31s 宽度限制
 * 价值：验证结构体数组 + 复合成员访问 + scanf 进结构体零误报
 */
#include <stdio.h>

struct Student {
    char name[50];
    float score;
};

struct Student2 {
    char name[32];
    double scores[3];
};

int main(void)
{
    struct Student students[3];
    struct Student2 s;
    double average = 0.0;
    int i;
    float sum = 0;

    for (i = 0; i < 3; i++) {
        printf("Enter name of student %d: ", i + 1);
        scanf("%s", students[i].name);

        printf("Enter score of %s: ", students[i].name);
        scanf("%f", &students[i].score);

        sum += students[i].score;
    }
    printf("Average score: %.2f\n", sum / 3);

    /* 数组成员 + 宽度限制的 %s */
    if (scanf("%31s %lf %lf %lf", s.name, &s.scores[0],
              &s.scores[1], &s.scores[2]) != 4) {
        return 0;
    }
    average = (s.scores[0] + s.scores[1] + s.scores[2]) / 3.0;
    printf("%s avg=%.2f\n", s.name, average);

    return 0;
}
