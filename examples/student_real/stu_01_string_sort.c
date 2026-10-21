/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第一份 / stu_01 批注版）
 * 场景：字符串排序（读入 n 个串后按字典序排序）
 * 已知问题：老师标注了 9 处，其中 5 处★致命。逐条如下：
 *   【错1】a 要用 int：getchar() 返回 int，还要能表示 EOF(-1)，
 *          char 装不下且可能把 0xFF 当成 EOF
 *   【错2】读完 n 后没吃掉换行，会残留成第一个字符串的内容，
 *          导致首个串变成空串、整体错位
 *   【错3】空间算少了：n 个串总长最多 100000，还要额外 n 个 '\0'
 *          （最多 100），至少要 100101；改成 static 更稳
 *   【错4★致命】b[i]=i 把整数赋给 char*：类型不对，
 *          而且指针应指向"本串在 str 里的起始位置"，即 &str[count]
 *   【错5★致命】a 从未被读入，是未初始化的垃圾值，循环次数随机
 *          （未定义行为）；且字符串以 '\n' 结束，不是 '\0'
 *   【错6★致命】a++ 是"字符值加 1"，不是"读下一个字符"。
 *          全程没有任何 getchar/scanf 去真正输入
 *   【错7】补 '\0' 写在循环体内部，每个字符后面都塞一个 '\0'，
 *          字符串被切成一堆单字符串；应在 while 结束后补一次
 *   【错8★致命】strcmp 要 char*，*b[j] 是 char（首字符），类型错；
 *          且判据写反：strcmp>0 才需要交换实现升序，写 <0 会排成降序
 *   【错9★致命】str[k] 是单个 char，却用 %s 打印（直接崩）；
 *          应打印排序后的指针数组 b[k]
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
