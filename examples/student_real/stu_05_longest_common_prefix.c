/*
 * 来源：学生写的（BUPT， captain 转交，第五份 ）
 * 场景：LeetCode 14. 最长公共前缀
 * 已知问题：i < strlen(strs[0]) 有符号/无符号混合比较（gcc -Wsign-compare 报）；strs[0][i] 修改了入参内容；return "" 返回只读字面量地址
 * 规模：约 15 行，1 个函数
 */
char* longestCommonPrefix(char** strs, int strsSize) {
    if (strsSize == 0) {
	return "";
}
for (int i = 0; i < strlen(strs[0]); i++) {
	for (int j = 1; j < strsSize; j++) {
		if (strs[0][i] != strs[j][i])
		{
			strs[0][i] = '\0';
			return strs[0];
		}
	}
}
return strs[0];
}
