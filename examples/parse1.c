int add(int a, int b) { return a + b; }

int main() {
    int i;
    int sum = 0;
    for (i = 0; i < 10; i = i + 1) {
        sum += i;
    }
    if (sum > 20 && i != 0) {
        return add(sum, 2);
    } else {
        return 0;
    }
}
