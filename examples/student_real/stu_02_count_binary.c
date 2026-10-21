/*
 * 来源：学生写的（BUPT， captain 转交，第二份 ）
 * 场景：二进制转换/计数（递归）
 * 已知问题：rev+=rev+t*dight 把累加写成翻倍；rev 算完未返回；这是单个函数片段，无 main 属正常
 * 规模：约 15 行，1 个函数
 */
int countBinary(int n){
   if(n>=1){int t=0,rev=0,dight=1;
    int x=n;
    while(x!=0){
           t=x%2;
           rev+=rev+t*dight;
           x/=2;
           dight*=10;
       }    return countBinary( n/2)+1;
        }


   else {return 0;}
}
