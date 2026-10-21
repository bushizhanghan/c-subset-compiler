/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第二十二份 ）
 * 场景：LeetCode 1365. 有多少小于当前数字的数
 * 已知问题：老师标注了 1 处★致命，另有若干提示。逐条如下：
 *   【错1★致命】int *ans=(int*)malloc(sizeof(int)) 只申请了 1 个 int
 *          的空间！但需要存 numsSize 个。后面 ans[1]、ans[2]...
 *          全部越界写入，会破坏堆内存，结果随机甚至崩溃。
 *          必须写成 malloc(sizeof(int) * numsSize)
 * 提示：*returnSize=numsSize 正确，保留；
 *       j!=i 其实是多余的（nums[j]<nums[i] 本身就排除了 j==i）；
 *       双层循环 O(n²)：n≤500 时最多 25 万次，本题能过
 * 规模：约 12 行，1 个函数
 */
int* smallerNumbersThanCurrent(int* nums, int numsSize, int* returnSize) {
    *returnSize=numsSize;
    int *ans=(int* )malloc(sizeof(int));
    for(int i=0;i<numsSize;i++){
        int count=0;
        for(int j=0;j<numsSize;j++){
            if(nums[j]<nums[i]&&j!=i){
                count++;
            }
        } ans[i]=count;
    }return ans;
}
