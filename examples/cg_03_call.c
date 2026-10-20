/* cg_03_call.c
 * 后端验证用例。期望退出码 42（gcc 实测值）。
 */
int add(int a, int b) { return a + b; }
int main() { return add(20, 22); }
