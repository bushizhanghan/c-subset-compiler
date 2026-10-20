/* cg_05_ifelse.c
 * 后端验证用例。期望退出码 10（gcc 实测值）。
 */
int main() { int a = 3; if (a > 2) { return 10; } else { return 20; } }
