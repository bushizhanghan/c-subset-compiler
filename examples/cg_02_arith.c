/* cg_02_arith.c
 * 后端验证用例。期望退出码 7（gcc 实测值）。
 */
int main() { int a = 2; int b = 3; return a * b + 1; }
