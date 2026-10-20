/* cg_11_cmp.c
 * 后端验证用例。期望退出码 1（gcc 实测值）。
 */
int main() { return (3 < 5) + (10 != 10) * 2; }
