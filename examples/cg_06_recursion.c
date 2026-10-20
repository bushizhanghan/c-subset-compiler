/* cg_06_recursion.c
 * 后端验证用例。期望退出码 55（gcc 实测值）。
 */
int fib(int n) { if (n < 2) { return n; } return fib(n-1) + fib(n-2); }
int main() { return fib(10); }
