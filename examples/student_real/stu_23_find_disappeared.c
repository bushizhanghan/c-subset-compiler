/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第二十三份 ）
 * 场景：LeetCode 448. 找到所有数组中消失的数字
 * 已知问题：老师标注了 1 处★致命，另有若干提示。逐条如下：
 *   【错★致命】范围错：数字范围是 1~n，必须从 i=1 扫到 i<=numsSize。
 *          现在扫 0~n-1：① i=0 时 b[0] 恒为 0（0 不在范围内，
 *          从没被标记），会被误当成"消失的数字 0"塞进答案；
 *          ② 漏掉 i=n，当 n 本身缺失时永远找不到
 * 提示：malloc 大小按 numsSize 开是对的；
 *       b[100005] 约 400KB 放栈上有风险，建议改 static 或 calloc；
 *       标记思路正确（b[值]=1）；
 *       len 和 qlen 功能重复，用一个即可；
 *       *returnSize=len 赋值正确
 * 规模：约 15 行，1 个函数
 */
int* findDisappearedNumbers(int* nums, int numsSize, int* returnSize) {
    int *ans=(int*)malloc(sizeof(int )*numsSize);
    int b[100005]={0};
    for(int i=0;i<numsSize;i++){
        b[nums[i]]=1;
    }int len=0,qlen=0;
    for(int i=0;i<numsSize;i++){
        if(b[i]==0){
            len++;
            ans[qlen++]=i;
        }
    }*returnSize=len;
    return ans;
}
