/*
 * 来源：学生写的（BUPT， captain 转交，第七份 ）
 * 场景：进制转换（十进制转 m 进制）
 * 已知问题：未知（未批改）
 * 规模：约 15 行，1 个函数
 */
#include<stdio.h>
int main(){
    int x,m;
    scanf("%d%d",&x,&m);
    int rev=0,temp=1;
    while(x!=0){
        int y=x%m;
        rev+=temp*y;
        temp*=10;
        x/=10;
    }printf("%d",rev);
return 0;
}
