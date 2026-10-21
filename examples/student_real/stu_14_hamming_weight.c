/*
 * 来源：学生写的（BUPT， captain 转交，第十四份 ）
 * 场景：LeetCode 191. 位 1 的个数（汉明重量）
 * 已知问题：无。**用户明确说明这份代码是正确的**
 * 规模：约 12 行，1 个函数
 */
int hammingWeight(int n) {
    int a[64],len=0,ans=0;
    while(n!=0){
        a[len++]=n%2;
        n=n/2;
    }
    for(int i=0;i<len;i++){
        if(a[i]==1){
            ans++;
        }
    }return ans;
}
