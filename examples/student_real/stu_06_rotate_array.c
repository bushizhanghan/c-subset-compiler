/*
 * 来源：学生写的（BUPT， captain 转交，第六份 ）
 * 场景：LeetCode 189. 轮转数组
 * 已知问题：未知（未批改）
 * 规模：约 22 行，1 个函数
 */
void rotate(int* nums, int numsSize, int k) {
    int* ans = (int*)malloc(sizeof(int) * numsSize);
    k = k % numsSize;
    if (k == 0) {
        return;
    } else {
        for (int i = 0; i < numsSize; i++) {
            int c = i + k;
            if (c >= numsSize) {
                c = c % numsSize;
                ans[c] = nums[i];
            } else {
                ans[c] = nums[i];
            }
        }
    }
    for (int i = 0; i < numsSize; i++) {
        nums[i] = ans[i];
    }
    free(ans);
}
