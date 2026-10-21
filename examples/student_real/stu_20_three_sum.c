/*
 * 来源：学生写的（BUPT， 老师批改过， captain 转交，第二十份 ）
 * 场景：LeetCode 15. 三数之和
 * 已知问题：老师标注了 7 处，其中 6 处★致命。逐条如下：
 *   【错1★致命】int a 在这里声明为普通 int
 *   【错2★致命】同名变量重复定义（同一作用域两个 a，编译报错）；
 *          且 sizeof(int)* 后面没写数字，语法错误
 *   【错3★致命】left/right 初始化后从未移动（循环体内没有任何
 *          left++ 或 right--）→ 死循环
 *   【错4★致命】结构错了：三数之和要固定一个数 i，再对剩下的
 *          区间用双指针。现在是"left/right 固定不动、中间扫 k"，
 *          这是两数之和的变体思路；且 i<right-1 会漏掉最后一个元素
 *   【错5★致命】输出的是下标 left/i/right 而不是值；
 *          且本题要求把结果放进返回的二维数组，不是 printf
 *   【错6】同样错：结果要写到返回数组，不是打印
 *   【错7★致命】a 是 int*，函数要求返回 int**，类型不匹配
 * 规模：约 20 行，1 个函数
 */
int** threeSum(int* nums, int numsSize, int* returnSize, int** returnColumnSizes) {
   int a;
   int *a=(int*)malloc(sizeof(int )*);
    int left=0,right=numsSize-1,found=0;
    while(left<right){
        int d=nums[left]+nums[right];
        int k=0-d;
        for(int i=left+1;i<right-1;i++){
            if(nums[i]==k){
                if(!found){
                printf("[%d,%d,%d]",left,i,right);
                }
                if(found){
        printf(",[%d,%d,%d]",left,i,right);
                }
                found=1;
            }
        }
    }if(!found){
        printf("[]");
    }return a;
}
