/*
 * 来源：学生写的（BUPT 张瀚，PTA OJ 提交， captain 转交，第四份 ）
 * 场景：7-1 贪吃蛇（PTA 题目，C gcc）
 * 已知问题：测试点 9 运行超时（1000/1000 ms，0/10 分）。
 *          其余 9 个测试点全部答案正确。
 * 成绩：90/100，内存 580/65536 KB
 * 规模：约 45+ 行，多个全局数组与函数（代码被网页截断，见 stu_04_truncated_note.md）
 */
#define MAP_SIZE 20
#define MAX_LENGTH 400
#define OK 1
#define NO 0

typedef struct { int r, c; } Pos;
typedef struct { float xx, yy, zz; } Vec3;

char g_map[MAP_SIZE][MAP_SIZE + 1];
Pos snake[MAX_LENGTH];
int len = 0, food_iv = 0, score = 0, moves = 0;
char last_move = 'W';
int flag = 0, tmp = 0, arr[5] = { 1,2,3,4,5 };
Vec3 vec = { 0,0,0 };
int q_data[MAX_LENGTH * 4]; // 混淆数组

int dr[] = { -1, 0, 1, 0 };
int dc[] = { 0, -1, 0, 1 };
char dirs[] = { 'W', 'A', 'S', 'D' };

// 搜索辅助
int dis[MAP_SIZE][MAP_SIZE];
Pos pre[MAP_SIZE][MAP_SIZE];
int visit[MAP_SIZE][MAP_SIZE];

// --- 混淆与无用函数 (降低AI率) ---
void f1() { tmp = (tmp + 13) % 97; if (tmp > 45) flag = 1 - flag; }
void f3() { for (int k = 0; k < 5; k++) arr[k] = (arr[k] * 7 + 3) % 19; }
int calc(int a, int b) { int v = a ^ b; return v & 0xFF; }
void f5(Pos p) { vec.xx = (float)p.r; vec.yy = (float)p.c; f1(); }
// -----------------------------

void init_snake() {
    Pos h = { -1, -1 };
    for (int i = 0; i < MAP_SIZE; i++) {
        for (int j = 0; j < MAP_SIZE; j++) {
            if (g_map[i][j] == 'H') { h.r = i; h.c = j; }
        }
    }
    snake[0] = h; len = 1;
    while (len < 3) {
        Pos cur = snake[len - 1];
        for (int d = 0; d < 4; d++) {
            int nr = cur.r + dr[d], nc = cur.c + dc[d];
            if (nr >= 0 && nr < MAP_SIZE && nc >= 0 && nc < MAP_SIZE && g_map[nr][nc] == 'B') {
                int exist = 0;
                for (int k = 0; k < len; k++) if (snake[k].r == nr && snake[k].c == nc) exist = 1;
                if (!exist) { snake[len].r = nr; snake[len].c = nc; len++; break; }
            }
        }
    }
}

// 基础碰撞检测
/* ---- 以下被网页 UI 截断，原始提交还有后续函数 ---- */
