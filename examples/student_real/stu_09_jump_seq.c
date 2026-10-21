/*
 * 来源：学生写的（BUPT， captain 转交，第九份 ）
 * 场景：链表模拟/跳转序列（PTA 类题目）
 * 已知问题：两个 int[1000000] 放在 main 栈上共 8MB，Windows 栈默认 1MB 必崩（应用全局或 malloc）；a[b[i-1]] 用外部输入当下标，范围不可控
 * 规模：约 25 行，1 个函数
 */
#include<stdio.h>
int main(){
    int n;
    scanf("%d",&n);
    int a[1000000];
    for(int i=1;i<=n;i++){
        scanf("%d",&a[i]);
    }
    int d;
    scanf("%d",&d);
    int b[1000000];
    b[0]=d;
    for(int i=1;i<n;i++){
        b[i] = a[b[i - 1]];
    }
    for(int i=0;i<n;i++){
        if(i==n-1){
            printf("%d",b[i]);
        }else {
            printf("%d ",b[i]);
        }
    }return 0;
}
