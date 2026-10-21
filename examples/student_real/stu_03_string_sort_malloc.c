/*
 * 来源：学生写的（BUPT， captain 转交，第三份 ）
 * 场景：字符串排序（malloc + 冒泡）
 * 已知问题：char *arr 想存字符串数组但少一层指针；arr[d]=&a[0] 地址塞进 char；int *temp 类型不匹配
 * 规模：约 45 行，1 个函数
 */
#include<stdio.h>
#include<stdlib.h>
#include<string.h>
int main(){int n;
scanf("%d",&n);
getchar();
           int count=0;
       char x;
char a[1000];
char *arr;
arr=(char *)malloc(sizeof(char)*n);
           for(int i=0;i<n;i++){ int len=0,d=0;
               while(1){scanf("%c",&x);if(x=='\n'){
                  arr[d]=&a[0];
                   d++;
                   count=0;
                   break;
               }else{
                                            a[count]=x;
                                            count++;
                                            len++;
               }
           }int *b;
                                b=(int*)malloc(sizeof(int )* len);
                                for(int i=0;i<len;i++){
                                    b[i]=a[i];
                                }
           }
for(int i=0;i<n-1;i++){
    for(int j=0;j<n-1-i;j++){
        if(strcmp(arr[j],arr[j+1])<0){
            int *temp=arr[j];
            arr[j]=arr[j+1];
            arr[j+1]=temp;
        }
    }}
for(int j=0;j<n;j++){
    printf("%s",arr[j]);
}

    return 0;
}
