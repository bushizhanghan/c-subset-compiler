#include <stdio.h>
int main(){
    int a[2][3]; int i,j;
    for(i=0;i<2;i=i+1){ for(j=0;j<3;j=j+1){ a[i][j]=i*3+j; } }
    printf("%d %d %d %d %d %d\n", a[0][0],a[1][0],a[0][1],a[1][1],a[0][2],a[1][2]);
    return 0;
}
