/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第二十一份 ）
 * 场景：LeetCode 645. 错误的集合（找出重复的数 + 丢失的数）
 * 已知问题：老师标注了 4 处★致命，另有两条提示。逐条如下：
 *   【错1★致命】范围错：数字是 1~n，必须从 i=1 扫到 i<=numsSize。
 *          现在扫 0~n-1，会① 多判 i=0（0 不是集合里的数，cnt 恒为 0，
 *          但被 ans1 当成"丢失"误抓）；② 漏掉 i=n（当丢失的是 n 时
 *          永远找不到，比如 [1,2,3,4,5,5] 应返回 [5,6]，漏掉 6）
 *   【错2★致命】int c[2]={'ans''ans2'} 三处错：
 *          ① 单引号是字符常量，'ans' 这种多字符写法本身就是错的；
 *          ② 变量名写错，ans2 根本不存在；
 *          ③ c 是局部数组，函数返回后内存失效 → 野指针，必须 malloc
 *   【错3★致命】c 是 int* 但指向栈内存；内存非法
 *   【错4★致命】全程没有给 *returnSize 赋值！调用方不知道结果长度
 * 提示：long long 没必要（计数最多到 n，int 足够）；
 *       计数思路本身是对的
 * 规模：约 15 行，1 个函数
 */
int* findErrorNums(int* nums, int numsSize, int* returnSize) {
    long long a[10005]={0};
    for(int i=0;i<numsSize;i++){
        a[nums[i]]++;
    }
    int ans=0,ans1=0;
    for(int i=0;i<numsSize;i++){
        if(a[i]==2){
            ans=i;
        }
        if(a[i]==0){
            ans1=i;
        }if(ans&&ans1){
            break;
        }
    }int c[2]={'ans''ans2'};
    return c;
}
