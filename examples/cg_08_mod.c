/* cg_08_mod.c
 * 后端验证用例。期望退出码 2（gcc 实测值）。
 */
int main() { int a = 17; int b = 5; return a % b; }
