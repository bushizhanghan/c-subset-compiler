/*
 * 来源：GitHub（ALX low_level_programming，学生课程作业）
 * 场景：二级指针链表插入 + 递归打印 + 释放
 * 已知问题：未知
 * 规模：约 45 行，4 个函数
 * 特点：listint_t **head 二级指针解引用改头、
 *       malloc 后判 NULL、->next 链式赋值、递归打印、
 *       free 遍历释放
 * 价值：验证二级指针参数 + 链表惯用法零误报
 */
#include <stdio.h>
#include <stdlib.h>

typedef struct listint_s {
    int n;
    struct listint_s *next;
} listint_t;

/* 二级指针：改头节点 */
listint_t *add_nodeint(listint_t **head, const int n)
{
    listint_t *new_node;

    new_node = malloc(sizeof(listint_t));
    if (!new_node)
        return (NULL);

    new_node->n = n;
    new_node->next = *head;
    *head = new_node;
    return (new_node);
}

/* 遍历打印 */
void print_list(const listint_t *head)
{
    while (head != NULL) {
        printf("%d -> ", head->n);
        head = head->next;
    }
    printf("NULL\n");
}

/* 递归释放 */
void free_list(listint_t *head)
{
    listint_t *tmp;
    while (head != NULL) {
        tmp = head->next;
        free(head);
        head = tmp;
    }
}

int main(void)
{
    listint_t *head = NULL;
    int i;
    for (i = 0; i < 5; i++) {
        add_nodeint(&head, i * 10);
    }
    print_list(head);
    free_list(head);
    return 0;
}
