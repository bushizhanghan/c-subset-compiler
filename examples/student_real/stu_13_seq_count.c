/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第十三份 ）
 * 场景：数列计数（f(1)+f(2)+...+f(n/2) 型递推）
 * 已知问题：老师标注了 5 处错误，逐条如下：
 *   【错1】硬编码打表：f(1)=1、f(2)=2、f(3)=2 只堵了 3 个数，
 *          n>=4 全靠下面错的逻辑，治标不治本
 *   【错2】外层循环次数用 d=n/2 拍脑袋决定，与递推关系毫无对应
 *   【错3】内层循环纯属凑数：等价于 k += n/2，没累加各 f(i)；
 *          且 for 的初始化写成单独的 q 是无效果表达式
 *   【错4★致命】n=n/2 破坏了原始输入，循环边界跟着变，逻辑失控
 *   【错5】结果可能接近 2e9（n=1000 时 1981471878），
 *          建议用 long long + %lld
 * 规模：约 20 行，1 个函数
 */
#include<stdio.h>
int main(){
    int n;
    scanf("%d",&n);
    if(n==1){
        printf("1");
        return 0;
    }else if(n==2){
        printf("2");
        return 0;
    }else if(n==3){
        printf("2");
        return 0;
    }else {
    int d=n/2,k=1;
    for(int i=2;i<=d;i++){
         int q=1;
      for(q;q<=n/2;q++){
          k++;
      }
        n=n/2;
    }
printf("%d",k);
    }return 0;
}
