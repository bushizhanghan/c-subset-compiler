int fib(int n) {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}

int sum_array(int *a, int len) {
    int s = 0;
    int i = 0;
    while (i < len) {
        s = s + a[i];
        i = i + 1;
    }
    return s;
}

int main() {
    int n = 10;
    int r = fib(n);
    int data[5];
    int i;
    for (i = 0; i < 5; i = i + 1) { data[i] = i * i; }
    int t = sum_array(data, 5);
    if (r > 0 && t > 0) { return r + t; }
    return 0;
}
