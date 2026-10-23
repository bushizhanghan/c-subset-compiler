#include <stdio.h>
#define BUFSIZE (1 << 20)
static char obuf[BUFSIZE];

int main(void) {
    long long N, M;
    if (scanf("%lld %lld", &N, &M) != 2) return 0;

    long long q = N / M;
    long long r = N % M;

    int pos = 0;

    for (long long i = 0; i < M; ++i) {
        long long v = (i < M - r) ? q : q + 1;
        pos += sprintf(obuf + pos, "%lld%c", v, (i + 1 == M) ? '\n' : ' ');
        if (pos > BUFSIZE - 64) {
            fwrite(obuf, 1, pos, stdout);
            pos = 0;
        }
    }
    fwrite(obuf, 1, pos, stdout);
    return 0;
}
