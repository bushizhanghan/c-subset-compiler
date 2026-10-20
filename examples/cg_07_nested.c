/* cg_07_nested.c
 * 后端验证用例。期望退出码 9（gcc 实测值）。
 */
int main() { int i=0; int j=0; int s=0; for(i=0;i<3;i=i+1){ for(j=0;j<3;j=j+1){ s=s+1; } } return s; }
