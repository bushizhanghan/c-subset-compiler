/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第十九份 ）
 * 场景：LeetCode 507. 完美数（判断 num 是否等于其所有真因子之和）
 * 已知问题：老师标注了 2 处，逐条如下：
 *   【错1★致命】只加了小的那个因子 i，漏掉了配对的 num/i。
 *          因子总是成对出现：28 找到 2 就必须同时加 14，找到 4
 *          必须同时加 7。所以 28 只算出 1+2+4=7，
 *          正确答案应为 1+2+4+7+14=28
 *   【错2】判断位置在循环外是对的，但因为上面漏加，
 *          这个比较永远比的是残缺的和，28 会返回 false
 * 提示：i*i<num 的边界思路对（枚举到 √num），改成 i<=num/i 更稳
 * 规模：约 12 行，1 个函数
 */
bool checkPerfectNumber(int num) {
    int ans=0;
    for(int i=1;i*i<num;i++){
        if(num%i==0){
            ans+=i;
        }
    }if(ans==num){
        return true;
    }else {

        return false;
    }
}
