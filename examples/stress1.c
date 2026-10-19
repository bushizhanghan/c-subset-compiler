int main() {
    int a = 1, b = 2;
    int *p;
    p = &a;
    *p = 5;
    int arr[10];
    for (int i = 0; i < 10; i++) {
        arr[i] = i;
    }
    a = a << 2;
    a = a >> 1;
    a = ~a;
    a = a ^ b;
    a = a | b;
    if (!a) { a = 1; }
    sizeof(int);
    sizeof(a);
    while (a-- > 0) { a = a; }
    return 0;
}
