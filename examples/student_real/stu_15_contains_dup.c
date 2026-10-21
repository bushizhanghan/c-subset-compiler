/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第十五份 ）
 * 场景：LeetCode 217. 存在重复元素
 * 已知问题：老师标注了 5 处错误，逐条如下：
 *   【错1★致命】数组大小写死 9，但 nums.length 最大 10^5。
 *          只要 numsSize>9 就越界写，直接内存破坏
 *   【错2★致命】拿"元素值"当数组下标。nums[i] ∈ [-10^9, 10^9]：
 *          ① 负数就是负下标，越界崩溃
 *          ② 值动辄几十亿，远超数组长度
 *   【错3】第二轮用"位置 i"查 a[i]，第一轮却用"值 nums[i]"存，
 *          两套下标体系对不上；i 最大到 numsSize-1，对长度 9 的
 *          数组又是越界读
 *   【错4】冗余写法：计数只可能是 0,1,2...，直接判 >1 即可
 *   【错5】整体思路不可行：-10^9~10^9 的值域开不出计数数组
 *          （要 20 亿个 int = 8GB），计数法必须换掉
 * 规模：约 12 行，1 个函数
 */
bool containsDuplicate(int* nums, int numsSize) {
    int a[9]={0};
    for(int i=0;i<numsSize;i++){
        a[nums[i]]++;
    }for(int i=0;i<numsSize;i++){
        if(a[i]!=0&&a[i]!=1){
            return true;
        }
    }return false;
}
