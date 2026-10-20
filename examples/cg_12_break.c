/* cg_12_break.c
 * 后端验证用例。期望退出码 10（gcc 实测值）。
 */
int main() { int i = 0; int s = 0; while (i < 10) { if (i == 5) { break; } s = s + i; i = i + 1; } return s; }
