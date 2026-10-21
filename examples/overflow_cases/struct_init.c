struct S{int a;}; int main(){ struct S s = {2147483648}; return s.a; }
