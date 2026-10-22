/*
 * 来源：构造样本（覆盖剩余未验证语法）
 * 场景：位字段 + 匿名结构体 + typedef 链 + 复杂宏
 * 已知问题：未知
 * 规模：约 65 行
 * 特点：unsigned int bitfield:3 位字段、
 *       匿名 struct/union 嵌套、typedef struct Node *NodePtr、
 *       多级 typedef 链、条件编译 #if/#elif/#else、
 *       宏函数式定义 #define MAX(a,b)
 * 价值：验证位字段 + 匿名结构体 + typedef 链零误报
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* 函数式宏 */
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define SQUARE(x) ((x) * (x))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

/* 位字段 */
struct Flags {
    unsigned int visible : 1;
    unsigned int enabled : 1;
    unsigned int priority : 3;
    unsigned int reserved : 3;
};

/* 匿名结构体嵌套 */
struct Shape {
    enum { CIRCLE, RECT } kind;
    union {
        struct { int x, y, r; };       /* 匿名结构体 */
        struct { int x1, y1, x2, y2; }; /* 匿名结构体 */
    };
};

/* typedef 链：struct Node 和 NodePtr */
typedef struct Node {
    int val;
    struct Node *next;
} Node, *NodePtr;

/* 条件编译 */
#if defined(DEBUG)
#define LOG(...) printf(__VA_ARGS__)
#else
#define LOG(...) ((void)0)
#endif

int main(void)
{
    struct Flags f = {1, 0, 5, 0};
    struct Shape s;
    NodePtr head = NULL;
    int arr[10];
    int i;

    s.kind = CIRCLE;
    s.x = 0;
    s.y = 0;
    s.r = 5;

    f.priority = 2;
    printf("flags: v=%d e=%d p=%d\n", f.visible, f.enabled, f.priority);

    /* 链表构建 */
    for (i = 0; i < 3; i++) {
        NodePtr n = malloc(sizeof(Node));
        if (n == NULL) break;
        n->val = SQUARE(i);
        n->next = head;
        head = n;
    }

    /* 遍历 */
    {
        NodePtr p;
        for (p = head; p != NULL; p = p->next) {
            LOG("val=%d\n", p->val);
        }
    }

    printf("max=%d square=%d count=%d\n",
           MAX(3, 7), SQUARE(4), (int)ARRAY_SIZE(arr));
    printf("shape r=%d\n", s.r);

    /* 释放 */
    while (head != NULL) {
        NodePtr tmp = head->next;
        free(head);
        head = tmp;
    }

    return 0;
}
