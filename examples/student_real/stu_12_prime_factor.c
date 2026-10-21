/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第十二份 ）
 * 场景：质因数分解（n 最大 10^12）
 * 已知问题：老师标注了 9 处错误，逐条如下：
 *   【错1】形参应为 long long：n 最大 10^12，int（约 2e9）存不下
 *   【错2】i*i<n 应为 i*i<=n，否则 i*i==n 时漏检（4/9/25 误判质数）
 *   【错3】n、j 必须是 long long；j*j 在 int 下会溢出
 *   【错4】scanf("%d",&n) 应为 %lld
 *   【错5】q 必须是 long long
 *   【错6】while(q!=0||j*j>n) 条件写反，应为 while(q>1 && j*j<=n)，会死循环
 *   【错7】printf("%d ",j) 应为 %lld
 *   【错8★致命】q=q%j 应为 q=q/j，取余导致 q 直接变 0，剩余数全丢
 *   【错9】while 后缺少 if(q>1) printf("%lld",q); 收尾
 * 规模：约 30 行，2 个函数
 */
#include<math.h>
#include<stdio.h>
int a(int n){
    if(n==1){return 0;

    }else if(n==2){return 1;}
    else {
        for(int i=2;i*i<n;i++){
           if (n%i==0){return 0;}
        }return 1;
    }
}
int main(){
    int t;
    scanf("%d",&t);
    for(int i=0;i<t;i++){
        if(i!=0){
            printf("\n");
        }
        int n,j=2;
        scanf("%d",&n);
        int q=n;
       while(q!=0||j*j>n){
            if(a(j)&&q%j==0){
                printf("%d ",j);
                q=q%j;
            }
             if(q%j!=0){
                    j++;
                }
        }
    }

       return 0;
}
