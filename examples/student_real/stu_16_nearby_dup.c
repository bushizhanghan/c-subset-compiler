/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第十六份 ）
 * 场景：LeetCode 219. 存在重复元素 II（值相等且下标距离 ≤ k）
 * 已知问题：老师标注了 3 处，逐条如下：
 *   【错1★致命·超时】双层循环 O(n²)。n=10^5 时约 5×10^9 次比较，
 *          1 秒时限下必然 TLE
 *   【错2】abs() 需要 #include <stdlib.h>，漏头文件在部分编译器下
 *          会警告/隐式声明错误
 *   【错3】内层没有提前 break：一旦 j-i>k，后面的 j 只会更远，
 *          不可能再满足条件，可以直接跳出
 * 规模：约 9 行，1 个函数
 */
bool containsNearbyDuplicate(int* nums, int numsSize, int k) {
    for(int i=0;i<numsSize;i++){
        for(int j=i+1;j<numsSize;j++){
            if(nums[i]==nums[j]&&abs(i-j)<=k){
                return true;
            }
        }
    }return false;
}
