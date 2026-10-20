/* err_24_break_outside.c
 * ---------------------------------------------------------------
 * 错误：break 写在了循环外面。
 * 为什么错（教材概念）：break 的作用是"提前结束它所在的循环或
 *   switch"，教材明确写了"break 只能用于循环或 switch 语句中"。
 * 写在循环外时它没有任何东西可以 break。
 * 后果：编译报 error 'break' statement not in loop or switch。
 * 期望 csub 报什么：E11 break 只能用在循环内部。
 * ---------------------------------------------------------------
 */
int main() {
    int a = 1;
    if (a > 0) {
        break;                /* ← 错在这：不在任何循环里 */
    }
    printf("%d\n", a);
    return 0;
}
