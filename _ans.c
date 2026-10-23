#include <stdlib.h>
/**
 * Note: The returned array must be malloced, assume caller calls free().
 */
char** buildArray(int* target, int targetSize, int n, int* returnSize) {
    int* ans=(int *)malloc(sizeof(int)*300);
    int j=0,count=0,i=0,count1=0;
    while(1){
        while(1){i++;
            ans[j++]="Push",count++,count1++;
            if(target[j]!=i){
                ans[j++]="Pop";
                count--,count1++;
            }if(i==n){
                break;
            }
        }if(count==targetSize){
            break;
        }
    }*returnSize=count1;
    return ans;
}
