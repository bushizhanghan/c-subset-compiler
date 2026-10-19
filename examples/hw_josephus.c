#include <stdio.h>
#include <stdlib.h>

#define MAX_N 50  // 人数上限，题目要求 N < 50

// 循环链表结点，存一个人的信息
typedef struct node {
    int num;            // 编号
    char name[20];      // 姓名
    char sex[4];        // 性别
    int age;            // 年龄
    struct node *next;  // 后继指针，最后一个结点指回头结点
} Node, *LinkList;

LinkList initList(void);
int createList(LinkList head);
void printList(LinkList head);
Node *findNode(LinkList head, int num);
void josephus(LinkList head, int S, int M, int X);
void printRest(LinkList head);
void destroyList(LinkList head);
int inputInt(const char *prompt, int lo, int hi);

/* ---------- initList：建立只含头结点的空循环链表 ---------- */

LinkList initList(void)
{
    LinkList head = (LinkList)malloc(sizeof(Node));
    if (head == NULL) {
        printf("内存分配失败！\n");
        exit(1);
    }
    head->next = head;  // 空表，头结点的 next 指向自己
    return head;
}

/* ---------- createList：尾插法读入 N 个人，正向建循环链表，返回人数 ---------- */

int createList(LinkList head)
{
    Node *rear = head;  // rear 始终指向最后一个结点
    int i, n;
    Node *p;

    n = inputInt("请输入人数 N（0 < N < 50）：", 1, MAX_N - 1);

    for (i = 1; i <= n; i++) {
        p = (Node *)malloc(sizeof(Node));
        if (p == NULL) {
            printf("内存分配失败！\n");
            exit(1);
        }
        printf("请输入第 %d 个人的编号、姓名、性别、年龄：", i);
        scanf("%d %s %s %d", &p->num, p->name, p->sex, &p->age);
        p->next = rear->next;
        rear->next = p;
        rear = p;
    }
    rear->next = head;  // 最后一个结点指回头结点，连成环
    return n;
}

/* ---------- printList：输出链表各结点的值 ---------- */

void printList(LinkList head)
{
    Node *p = head->next;
    printf("\n===== 循环链表当前各结点的值 =====\n");
    printf("编号  姓名        性别   年龄\n");
    while (p != head) {
        printf("%-6d %-12s %-6s %-4d\n", p->num, p->name, p->sex, p->age);
        p = p->next;
    }
    printf("==================================\n");
}

/* ---------- findNode：按编号查找结点 ---------- */

Node *findNode(LinkList head, int num)
{
    Node *p = head->next;
    while (p != head) {
        if (p->num == num)
            return p;
        p = p->next;
    }
    return NULL;
}

/* ---------- josephus：定位 S，报数出列直至剩 X 人 ---------- */

void josephus(LinkList head, int S, int M, int X)
{
    Node *prev, *cur, *del;
    int i, count = 0;
    int out = 0;  // 已出列人数

    for (cur = head->next; cur != head; cur = cur->next)
        count++;

    // 先找到编号为 S 的人，prev 记下他的前驱，删除的时候要用
    prev = head;
    cur = head->next;
    while (cur != head && cur->num != S) {
        prev = cur;
        cur = cur->next;
    }
    printf("\n已找到编号为 %d 的结点：%s（%s，%d 岁）\n",
           cur->num, cur->name, cur->sex, cur->age);

    printf("\n===== 出列过程（编号为 %d 开始报数，每第 %d 人出列）=====\n", S, M);
    printf("出列顺序：");
    while (count > X) {
        // 从当前这个人开始由 1 报数，前进 M-1 步就是第 M 个人
        for (i = 1; i < M; i++) {
            prev = cur;
            cur = cur->next;
            if (cur == head) {
                // 越过哨兵头结点，cur 跳回第一个人；第一个人的前驱就是头结点，
                // prev 也要一起改成 head，不然删除的时候会断错链，把链表写坏
                cur = head->next;
                prev = head;
            }
        }
        del = cur;
        printf("%d", del->num);
        count--;
        out++;
        if (count > X)
            printf(" -> ");
        prev->next = del->next;  // 前驱越过 del，把 del 删掉
        cur = del->next;
        if (cur == head) {
            cur = head->next;
            prev = head;
        }
        free(del);
    }
    printf("\n（共 %d 人出列，剩余 %d 人）\n", out, X);
}

/* ---------- printRest：输出最后剩余人员的完整信息 ---------- */

void printRest(LinkList head)
{
    Node *p = head->next;
    printf("\n===== 最后剩余人员的完整信息 =====\n");
    printf("编号  姓名        性别   年龄\n");
    while (p != head) {
        printf("%-6d %-12s %-6s %-4d\n", p->num, p->name, p->sex, p->age);
        p = p->next;
    }
    printf("===================================\n");
}

/* ---------- destroyList：释放全部结点，包括头结点 ---------- */

void destroyList(LinkList head)
{
    Node *p = head->next, *q;
    while (p != head) {
        q = p->next;
        free(p);
        p = q;
    }
    free(head);
}

/* ---------- inputInt：输入限定范围内的整数，非法则重输 ---------- */

int inputInt(const char *prompt, int lo, int hi)
{
    int v;
    printf("%s", prompt);
    while (scanf("%d", &v) != 1 || v < lo || v > hi) {
        while (getchar() != '\n')  // 清掉刚才输错的东西
            ;
        printf("输入无效，请重新输入（%d ~ %d）：", lo, hi);
    }
    return v;
}

/* ---------- main：总体流程，可反复运行多组数据 ---------- */

int main(void)
{
    LinkList head;
    int S, M, X, n;
    char again = 'n';

    printf("========== 数据结构实验（一）：约瑟夫问题 ==========\n");

    do {
        head = initList();
        n = createList(head);
        printList(head);

        // 输入 S、M、X：M 要 >= 1，X 要小于总人数，S 必须是表里有的人
        for (;;) {
            printf("\n请输入开始报数的人的编号 S、间隔 M、剩余人数 X：");
            if (scanf("%d %d %d", &S, &M, &X) == EOF) {
                printf("\n检测到输入结束，程序退出。\n");
                exit(0);
            }
            if (M < 1) {
                printf("输入无效：间隔 M 需 >= 1，请重新输入。\n");
                while (getchar() != '\n')
                    ;
                continue;
            }
            if (X < 1 || X >= n) {
                printf("输入无效：剩余人数 X 需满足 1 <= X < N（N=%d），请重新输入。\n", n);
                while (getchar() != '\n')
                    ;
                continue;
            }
            if (findNode(head, S) == NULL) {
                printf("链表中不存在编号为 %d 的结点，请重新输入！\n", S);
                while (getchar() != '\n')
                    ;
                continue;
            }
            break;
        }

        josephus(head, S, M, X);
        printRest(head);
        destroyList(head);

        printf("\n是否再运行一组数据？(y/n)：");
        scanf(" %c", &again);
        printf("\n");

    } while (again == 'y' || again == 'Y');

    printf("程序结束，谢谢使用！\n");
    return 0;
}
