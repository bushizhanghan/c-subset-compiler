int main() {
    int i = 0;
    int s = 0;
    while (i < 5) {
        if (i == 3) { break; }
        s = s + i;
        i = i + 1;
    }
    return s;
}
