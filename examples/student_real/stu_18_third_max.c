/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第十八份 ）
 * 场景：LeetCode 414. 第三大的数
 * 已知问题：老师标注了 8 处，其中 5 处★致命。逐条如下：
 *   【错1★致命】numsSize==1 时访问 nums[1] 直接越界
 *   【错2】缩进暴露问题：这行是 if(numsSize<3) 的右括号
 *   【错3★致命】这个 } 把整个函数关掉了！下面 int max1=nums[0]
 *          落在所有函数之外 → 全局区没有 nums → 报
 *          "nums undeclared here (not in a function)"
 *   【错4★致命】三个变量初值都是 nums[0]，导致"没找到第三大"和
 *          "找到了值 nums[0]"无法区分；[2,2,3,1] 全程 max3 恒 2，
 *          最后返回 2（正确答案 1）
 *   【错5★致命】max3=max2 顺序写错：此时 max2 已赋成旧 max1，
 *          应写 max3=temp（temp 才是旧 max2）
 *   【错6】应写 else if：刚更新完 max1 又用新 max1 判断，
 *          同一轮可能连续触发两个分支
 *   【错7】同样应为 else if
 *   【错8】无脑返回 max3：不同数字不足 3 个时应返回最大值。
 *          [1,1,2] 返回 1，正确答案 2
 * 规模：约 20 行，1 个函数
 */
int thirdMax(int* nums, int numsSize) {
    int  max=nums[0];
    if(numsSize<3){
        if(nums[1]>nums[0]){
            max=nums[1];
        }
        }
        return max;
    }
        int max1=nums[0],max2=nums[0],max3=nums[0];
        for(int i=1;i<numsSize;i++){
            if(nums[i]>max1){
                int temp=max2;
                max2=max1;
                max3=max2;
                max1=nums[i];
            }
            if(nums[i]<max1&&nums[i]>max2){
                int temp=max2;
                max2=nums[i];
                max3=temp;
            }
            if(nums[i]>max3&&nums[i]<max2){
                max3=nums[i];
            }
        }
    return max3;
}
