#include <stdlib.h>
char** buildArray(int* target, int targetSize, int n, int* returnSize) {
    char* ans=(char *)malloc(sizeof(char)*300);
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
