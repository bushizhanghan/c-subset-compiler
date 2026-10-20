/* cg_10_compound.c
 * 后端验证用例。期望退出码 10（gcc 实测值）。
 */
int main() { int a = 2; a += 3; a *= 2; return a; }
