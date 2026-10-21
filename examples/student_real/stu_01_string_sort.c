/*
 * 来源：学生写的（BUPT， captain 转交 ）
 * 场景：字符串排序（疑似冒泡排序 + 输入处理）
 * 已知问题：b[i]=i 把整数当指针；a 未初始化就用；*b[j] 多一层解引用；str[k] 当字符串传给 %s
 * 规模：约 30 行，1 个函数
 */
#include<string.h>
#include<stdio.h>
int main(){
int n,count=0;
    char a;
scanf("%d",&n);
char str[100000];
    char* b[101];
     for(int i=0;i<n;i++){
 b[i]=i;
  while(a!='\0')      {
  str[count++]=a;
a++;
        str[count++]='\0';}
    }
   for(int i=0;i<n-1;i++){
        for(int j=0;j<n-1-i;j++){
            if(strcmp(*b[j],*b[j+1])<0){
                char *temp=b[j+1];
                b[j+1]=b[j];
                b[j]=temp;
            }
        }
    }for(int k=0;k<n;k++){
        printf("%s\n",str[k]);
    }
    return 0;
}
