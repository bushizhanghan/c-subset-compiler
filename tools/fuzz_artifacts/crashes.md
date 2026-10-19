# csub 模糊测试缺陷清单

- 随机种子: `20250101`
- ⚠ 运行期间 src/ 被改动过: `src/ir.py`、`src/semantic.py`
- 用例总数: 764
- 缺陷类别: 8，缺陷用例: 18
- 源码快照: `src/ir.py`@4da3f5c0b853、`src/lexer.py`@e1278e41286a、`src/parser.py`@211d33375a3c、`src/preprocess.py`@f5ee87d62254、`src/semantic.py`@cc8e0fe3beb8

## 1. `ValueError` @ `parser.py:1104` (parse 阶段)

- 命中用例数: 4
- 出错函数: `parse_primary()`
- 出错代码: `return Num(line=t.line, col=t.col, value=int(v, 16))`
- 异常信息: `invalid literal for int() with base 16: '0x'`
- 最小复现（8 字节，见 `crash_01_ValueError.c`）:

```python
't n(){0x'
```

<details><summary>traceback</summary>

```
Traceback (most recent call last):
  File "E:\dsh\work\csub\tools\fuzz_test.py", line 181, in run_pipeline
    prog = parse(pr.text)
  File "E:\dsh\work\csub\src\parser.py", line 1191, in parse
    return Parser(tokenize(src)).parse()
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  ... [省略 7 行 traceback 帧] ...
    fn.body = self.parse_block()
              ~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 827, in parse_block
    blk.stmts.append(self.parse_stmt())
                     ~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 947, in parse_stmt
    e = self.parse_expr()
  File "E:\dsh\work\csub\src\parser.py", line 957, in parse_expr
    e = self.parse_assign()
  File "E:\dsh\work\csub\src\parser.py", line 965, in parse_assign
    left = self.parse_ternary()
  File "E:\dsh\work\csub\src\parser.py", line 975, in parse_ternary
    cond = self.parse_binary(1)
  File "E:\dsh\work\csub\src\parser.py", line 986, in parse_binary
    left = self.parse_unary()
  File "E:\dsh\work\csub\src\parser.py", line 1023, in parse_unary
    return self.parse_postfix()
           ~~~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 1026, in parse_postfix
    e = self.parse_primary()
  File "E:\dsh\work\csub\src\parser.py", line 1104, in parse_primary
    return Num(line=t.line, col=t.col, value=int(v, 16))
                                             ~~~^^^^^^^
ValueError: invalid literal for int() with base 16: '0x'
```

</details>

## 2. `RecursionError` @ `parser.py:975` (parse 阶段)

- 命中用例数: 3
- 出错函数: `parse_ternary()`
- 出错代码: `cond = self.parse_binary(1)`
- 异常信息: `maximum recursion depth exceeded`
- 最小复现（423 字节，见 `crash_02_RecursionError.c`）:

```python
'int main(){ return ((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((1)))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))); }'
```

<details><summary>traceback</summary>

```
Traceback (most recent call last):
  File "E:\dsh\work\csub\tools\fuzz_test.py", line 181, in run_pipeline
    prog = parse(pr.text)
  File "E:\dsh\work\csub\src\parser.py", line 1191, in parse
    return Parser(tokenize(src)).parse()
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  ... [省略 2113 行 traceback 帧] ...
  File "E:\dsh\work\csub\src\parser.py", line 1132, in parse_primary
    e = self.parse_expr()
  File "E:\dsh\work\csub\src\parser.py", line 957, in parse_expr
    e = self.parse_assign()
  File "E:\dsh\work\csub\src\parser.py", line 965, in parse_assign
    left = self.parse_ternary()
  File "E:\dsh\work\csub\src\parser.py", line 975, in parse_ternary
    cond = self.parse_binary(1)
  File "E:\dsh\work\csub\src\parser.py", line 986, in parse_binary
    left = self.parse_unary()
  File "E:\dsh\work\csub\src\parser.py", line 1023, in parse_unary
    return self.parse_postfix()
           ~~~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 1026, in parse_postfix
    e = self.parse_primary()
  File "E:\dsh\work\csub\src\parser.py", line 1132, in parse_primary
    e = self.parse_expr()
  File "E:\dsh\work\csub\src\parser.py", line 957, in parse_expr
    e = self.parse_assign()
  File "E:\dsh\work\csub\src\parser.py", line 965, in parse_assign
    left = self.parse_ternary()
  File "E:\dsh\work\csub\src\parser.py", line 975, in parse_ternary
    cond = self.parse_binary(1)
RecursionError: maximum recursion depth exceeded
```

</details>

## 3. `RecursionError` @ `parser.py:1023` (parse 阶段)

- 命中用例数: 3
- 出错函数: `parse_unary()`
- 出错代码: `return self.parse_postfix()`
- 异常信息: `maximum recursion depth exceeded`
- 最小复现（1223 字节，见 `crash_03_RecursionError.c`）:

```python
'int main(){ return f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(f(1)))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))); }'
```

<details><summary>traceback</summary>

```
Traceback (most recent call last):
  File "E:\dsh\work\csub\tools\fuzz_test.py", line 181, in run_pipeline
    prog = parse(pr.text)
  File "E:\dsh\work\csub\src\parser.py", line 1191, in parse
    return Parser(tokenize(src)).parse()
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  ... [省略 2367 行 traceback 帧] ...
    args.append(self.parse_assign())
                ~~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 965, in parse_assign
    left = self.parse_ternary()
  File "E:\dsh\work\csub\src\parser.py", line 975, in parse_ternary
    cond = self.parse_binary(1)
  File "E:\dsh\work\csub\src\parser.py", line 986, in parse_binary
    left = self.parse_unary()
  File "E:\dsh\work\csub\src\parser.py", line 1023, in parse_unary
    return self.parse_postfix()
           ~~~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 1047, in parse_postfix
    args.append(self.parse_assign())
                ~~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 965, in parse_assign
    left = self.parse_ternary()
  File "E:\dsh\work\csub\src\parser.py", line 975, in parse_ternary
    cond = self.parse_binary(1)
  File "E:\dsh\work\csub\src\parser.py", line 986, in parse_binary
    left = self.parse_unary()
  File "E:\dsh\work\csub\src\parser.py", line 1023, in parse_unary
    return self.parse_postfix()
           ~~~~~~~~~~~~~~~~~~^^
RecursionError: maximum recursion depth exceeded
```

</details>

## 4. `RecursionError` @ `semantic.py:605` (semantic 阶段)

- 命中用例数: 3
- 出错函数: `check_expr()`
- 出错代码: `self.check_expr(e.base)`
- 异常信息: `maximum recursion depth exceeded`
- 最小复现（2976 字节，见 `crash_04_RecursionError.c`）:

```python
't n(){t[0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0][0]...[截断，共 2976 字节]
```

<details><summary>traceback</summary>

```
Traceback (most recent call last):
  File "E:\dsh\work\csub\tools\fuzz_test.py", line 184, in run_pipeline
    analyze(prog)
    ~~~~~~~^^^^^^
  File "E:\dsh\work\csub\src\semantic.py", line 939, in analyze
    return Analyzer().analyze(prog)
           ~~~~~~~~~~~~~~~~~~^^^^^^
  File "E:\dsh\work\csub\src\semantic.py", line 288, in analyze
    self.check_func(d)
    ~~~~~~~~~~~~~~~^^^
  File "E:\dsh\work\csub\src\semantic.py", line 331, in check_func
    self.check_block(fn.body, new_scope=False)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "E:\dsh\work\csub\src\semantic.py", line 404, in check_block
    self.check_stmt(s)
    ~~~~~~~~~~~~~~~^^^
  File "E:\dsh\work\csub\src\semantic.py", line 461, in check_stmt
    self.check_expr(s.value)
    ~~~~~~~~~~~~~~~^^^^^^^^^
  File "E:\dsh\work\csub\src\semantic.py", line 605, in check_expr
    self.check_expr(e.base)
    ~~~~~~~~~~~~~~~^^^^^^^^
  File "E:\dsh\work\csub\src\semantic.py", line 605, in check_expr
    self.check_expr(e.base)
    ~~~~~~~~~~~~~~~^^^^^^^^
  File "E:\dsh\work\csub\src\semantic.py", line 605, in check_expr
    self.check_expr(e.base)
    ~~~~~~~~~~~~~~~^^^^^^^^
  [Previous line repeated 988 more times]
RecursionError: maximum recursion depth exceeded
```

</details>

## 5. `RecursionError` @ `parser.py:297` (parse 阶段)

- 命中用例数: 2
- 出错函数: `expect()`
- 出错代码: `if not self.at(value):`
- 异常信息: `maximum recursion depth exceeded`
- 最小复现（1012 字节，见 `crash_05_RecursionError.c`）:

```python
'int main(){{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{{}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}'
```

<details><summary>traceback</summary>

```
Traceback (most recent call last):
  File "E:\dsh\work\csub\tools\fuzz_test.py", line 181, in run_pipeline
    prog = parse(pr.text)
  File "E:\dsh\work\csub\src\parser.py", line 1191, in parse
    return Parser(tokenize(src)).parse()
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  ... [省略 2961 行 traceback 帧] ...
  File "E:\dsh\work\csub\src\parser.py", line 827, in parse_block
    blk.stmts.append(self.parse_stmt())
                     ~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 845, in parse_stmt
    return self.parse_block()
           ~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 827, in parse_block
    blk.stmts.append(self.parse_stmt())
                     ~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 845, in parse_stmt
    return self.parse_block()
           ~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 827, in parse_block
    blk.stmts.append(self.parse_stmt())
                     ~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 845, in parse_stmt
    return self.parse_block()
           ~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 822, in parse_block
    lb = self.expect("{")
  File "E:\dsh\work\csub\src\parser.py", line 297, in expect
    if not self.at(value):
           ~~~~~~~^^^^^^^
RecursionError: maximum recursion depth exceeded
```

</details>

## 6. `RecursionError` @ `ir.py:305` (ir 阶段)

- 命中用例数: 1
- 出错函数: `gen_expr()`
- 出错代码: `base = self.gen_expr(e.base)`
- 异常信息: `maximum recursion depth exceeded`
- 最小复现（1991 字节，见 `crash_06_RecursionError.c`）:

```python
't n(){b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b.b;}'
```

<details><summary>traceback</summary>

```
Traceback (most recent call last):
  File "E:\dsh\work\csub\tools\fuzz_test.py", line 187, in run_pipeline
    generate(prog)
    ~~~~~~~~^^^^^^
  File "E:\dsh\work\csub\src\ir.py", line 591, in generate
    raw = IRGen().gen_program(prog)
  File "E:\dsh\work\csub\src\ir.py", line 99, in gen_program
    self.gen_block(d.body, new_scope=False)
    ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^
  File "E:\dsh\work\csub\src\ir.py", line 105, in gen_block
    self.gen_stmt(s)
    ~~~~~~~~~~~~~^^^
  File "E:\dsh\work\csub\src\ir.py", line 173, in gen_stmt
    v = self.gen_expr(s.value)
  File "E:\dsh\work\csub\src\ir.py", line 305, in gen_expr
    base = self.gen_expr(e.base)
  File "E:\dsh\work\csub\src\ir.py", line 305, in gen_expr
    base = self.gen_expr(e.base)
  File "E:\dsh\work\csub\src\ir.py", line 305, in gen_expr
    base = self.gen_expr(e.base)
  [Previous line repeated 989 more times]
RecursionError: maximum recursion depth exceeded
```

</details>

## 7. `RecursionError` @ `parser.py:1026` (parse 阶段)

- 命中用例数: 1
- 出错函数: `parse_postfix()`
- 出错代码: `e = self.parse_primary()`
- 异常信息: `maximum recursion depth exceeded`
- 最小复现（4023 字节，见 `crash_07_RecursionError.c`）:

```python
'int main(){ return 1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?1?...[截断，共 4023 字节]
```

<details><summary>traceback</summary>

```
Traceback (most recent call last):
  File "E:\dsh\work\csub\tools\fuzz_test.py", line 181, in run_pipeline
    prog = parse(pr.text)
  File "E:\dsh\work\csub\src\parser.py", line 1191, in parse
    return Parser(tokenize(src)).parse()
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  ... [省略 1973 行 traceback 帧] ...
  File "E:\dsh\work\csub\src\parser.py", line 965, in parse_assign
    left = self.parse_ternary()
  File "E:\dsh\work\csub\src\parser.py", line 978, in parse_ternary
    then = self.parse_assign()
  File "E:\dsh\work\csub\src\parser.py", line 965, in parse_assign
    left = self.parse_ternary()
  File "E:\dsh\work\csub\src\parser.py", line 978, in parse_ternary
    then = self.parse_assign()
  File "E:\dsh\work\csub\src\parser.py", line 965, in parse_assign
    left = self.parse_ternary()
  File "E:\dsh\work\csub\src\parser.py", line 978, in parse_ternary
    then = self.parse_assign()
  File "E:\dsh\work\csub\src\parser.py", line 965, in parse_assign
    left = self.parse_ternary()
  File "E:\dsh\work\csub\src\parser.py", line 975, in parse_ternary
    cond = self.parse_binary(1)
  File "E:\dsh\work\csub\src\parser.py", line 986, in parse_binary
    left = self.parse_unary()
  File "E:\dsh\work\csub\src\parser.py", line 1023, in parse_unary
    return self.parse_postfix()
           ~~~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 1026, in parse_postfix
    e = self.parse_primary()
RecursionError: maximum recursion depth exceeded
```

</details>

## 8. `RecursionError` @ `parser.py:1073` (parse 阶段)

- 命中用例数: 1
- 出错函数: `parse_primary()`
- 出错代码: `if self.at("{"):`
- 异常信息: `maximum recursion depth exceeded`
- 最小复现（4896 字节，见 `crash_08_RecursionError.c`）:

```python
't in(){ if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)if(1)i...[截断，共 4896 字节]
```

<details><summary>traceback</summary>

```
Traceback (most recent call last):
  File "E:\dsh\work\csub\tools\fuzz_test.py", line 181, in run_pipeline
    prog = parse(pr.text)
  File "E:\dsh\work\csub\src\parser.py", line 1191, in parse
    return Parser(tokenize(src)).parse()
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  ... [省略 14 行 traceback 帧] ...
  File "E:\dsh\work\csub\src\parser.py", line 858, in parse_stmt
    then = self.parse_stmt()
  File "E:\dsh\work\csub\src\parser.py", line 858, in parse_stmt
    then = self.parse_stmt()
  [Previous line repeated 980 more times]
  File "E:\dsh\work\csub\src\parser.py", line 856, in parse_stmt
    cond = self.parse_expr()
  File "E:\dsh\work\csub\src\parser.py", line 957, in parse_expr
    e = self.parse_assign()
  File "E:\dsh\work\csub\src\parser.py", line 965, in parse_assign
    left = self.parse_ternary()
  File "E:\dsh\work\csub\src\parser.py", line 975, in parse_ternary
    cond = self.parse_binary(1)
  File "E:\dsh\work\csub\src\parser.py", line 986, in parse_binary
    left = self.parse_unary()
  File "E:\dsh\work\csub\src\parser.py", line 1023, in parse_unary
    return self.parse_postfix()
           ~~~~~~~~~~~~~~~~~~^^
  File "E:\dsh\work\csub\src\parser.py", line 1026, in parse_postfix
    e = self.parse_primary()
  File "E:\dsh\work\csub\src\parser.py", line 1073, in parse_primary
    if self.at("{"):
       ~~~~~~~^^^^^
RecursionError: maximum recursion depth exceeded
```

</details>
