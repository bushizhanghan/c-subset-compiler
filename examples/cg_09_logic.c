/* cg_09_logic.c
 * 后端验证用例。期望退出码 1（gcc 实测值）。
 */
int main() { int a = 1; int b = 0; if (a && !b) { return 1; } return 0; }
