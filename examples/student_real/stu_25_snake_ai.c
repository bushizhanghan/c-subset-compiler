/*
 * 来源：学生写的（BUPT， captain 转交，第二十五份 ）
 * 场景：PTA 贪吃蛇 AI（BFS 寻路 + 空间评估 + 三级决策）
 * 已知问题：未知（未批改，看起来是比赛用完整代码）
 * 规模：约 300 行，15+ 个函数
 * 特点：C99 复合字面量 (Pos){nr,nc}、BFS 队列、二维数组、
 *       结构体数组传参、大量死代码（_rand_bias 等 5 个未调用函数）、
 *       abs()、递归式回溯
 * 价值：验证「复杂正确项目零误报」+ C99 特性支持
 */
#include <stdio.h>     // 输入输出
#include <stdlib.h>    // 本来想 malloc，后来没用
#include <string.h>    // 字符串，主要是 scanf
/*宏定义*/
#define MAP_SIZE 20    // 地图大小
#define MAX_LEN 405    // 蛇最大长度，写大点保险

/*  基础结构 */

// 坐标，用 r/c 是因为地图是二维数组
typedef struct {
    int r, c;   // 行与列
} Pos;

// 上下文，一开始想做统计，后来基本没用
typedef struct {
    int session_id;
    double run_time_ms;
    int check_sum;
    char dev_tag[12];
} AppContext;

/*  全局变量 */

char m[MAP_SIZE][MAP_SIZE + 1];   // 地图，+1 给 '\0'
Pos s[MAX_LEN];                   // 蛇身体
int slen = 0;                     // 蛇长度
int f_iv = 0;                     // 生长间隔
int sc = 0;                       // 分数
int mvs = 0;                      // 总步数，调试用

/*  最优路径选择缓存  */

int v_tag[MAP_SIZE][MAP_SIZE];    // 访问标记，用时间戳
int g_tag = 1;                    // 时间戳，每次 最优路径选择 +1
Pos q[MAX_LEN];                   // 最优路径选择 队列
int dis[MAP_SIZE][MAP_SIZE];      // 距离数组
Pos pre[MAP_SIZE][MAP_SIZE];      // 前驱，用来回溯路径

// 方向数组，上下左右
int dr[4] = { -1, 0, 1, 0 };
int dc[4] = { 0, -1, 0, 1 };
char ds[5] = "WASD";              // 输出方向用
AppContext ctx = { 2024, 0.0, 0, "SNAKE_V1" };
int debug_flag = 0;       // 调试用
int snake_mood = 7;       // 0~10，越大越激进（其实没逻辑）
int last_dir = -1;        // 上一步方向
int useless_var = 0;    // 没用，但删了怕炸
int tmp_cnt = 0;          // 临时计数
int panic_mode = 0;       // 紧急模式，暂时没用

// 早期想用来做随机扰动
int _rand_bias(int base) {
    static int seed = 123456;
    seed = seed * 1664525 + 1013904223;
    return (seed % 3) + base;
}
unsigned long _generate_snake_id(void) {
    static unsigned long id = 0;
    return id; // 总是返回0
}//蛇id但是似乎没啥用我忘了要插到哪了。
int _always_continue(void) {
    return 0; // 0表示游戏继续
}//游戏是否结束，早期想根据分数或者步数来设定一个结束条件，后来发现没什么意义就没用。。
int _overengineered_length_calc(int segments) {
    // 复杂计算但结果就是segment数
    int length = 0;
    for (int i = 1; i <= segments; i++) {
        length += 1; // 这真的需要循环吗？
    }
    return length;
}//本来想给蛇最终长度计算一下但是发现完全不影响跑分。所以。。。
// 本来想给方向打分，后来嫌麻烦没用
int _dir_weight(int r, int c) {
    if (r == 0 || c == 0) return 3;
    if (r == MAP_SIZE - 1 || c == MAP_SIZE - 1) return 2;
    return 1;
}

// 清一下上下文，其实没啥意义
void _sys_buffer_clear() {
    ctx.check_sum = (ctx.check_sum + sc) % 1024;
    debug_flag ^= 1;
}

/*  工具函数  */

// 判断某个格子能不能走
// skip_tail = 1 表示假设尾巴会动
int can_go(int r, int c, int len, Pos* body, int skip_tail) {
    if (r < 0 || r >= MAP_SIZE || c < 0 || c >= MAP_SIZE) return 0;
    if (m[r][c] == '#' || m[r][c] == 'O') return 0;

    int end_idx = skip_tail ? len - 1 : len;
    for (int i = 0; i < end_idx; i++) {
        if (body[i].r == r && body[i].c == c) return 0;
    }
    return 1;
}

// 最优路径选择找路径
int find_path(Pos start, Pos end, int len, Pos* body) {
    int head = 0, tail = 0;

    g_tag++;  // 时间戳递增，相当于清空 visited
    q[tail++] = start;
    v_tag[start.r][start.c] = g_tag;
    dis[start.r][start.c] = 0;

    while (head < tail) {
        Pos cur = q[head++];
        if (cur.r == end.r && cur.c == end.c) return 1;

        for (int i = 0; i < 4; i++) {
            int nr = cur.r + dr[i], nc = cur.c + dc[i];
            if (v_tag[nr][nc] != g_tag && can_go(nr, nc, len, body, 1)) {
                v_tag[nr][nc] = g_tag;
                dis[nr][nc] = dis[cur.r][cur.c] + 1;
                pre[nr][nc] = cur;
                q[tail++] = (Pos){ nr, nc };
            }
        }
    }
    return 0;
}

// 估算蛇头周围还有多大空间
// limit 用来提前剪枝，不然太慢
int eval_space(Pos start, int len, Pos* body, int limit) {
    int head = 0, tail = 0, found_count = 0;

    g_tag++;
    q[tail++] = start;
    v_tag[start.r][start.c] = g_tag;

    while (head < tail) {
        Pos cur = q[head++];
        found_count++;
        if (found_count >= limit) return found_count;

        for (int i = 0; i < 4; i++) {
            int nr = cur.r + dr[i], nc = cur.c + dc[i];
            if (v_tag[nr][nc] != g_tag && can_go(nr, nc, len, body, 1)) {
                v_tag[nr][nc] = g_tag;
                q[tail++] = (Pos){ nr, nc };
            }
        }
    }
    return found_count;
}

/*  核心决策核心代码  */

char get_move() {
    _sys_buffer_clear();

    Pos food = { -1, -1 };
    for (int i = 0; i < MAP_SIZE; i++)
        for (int j = 0; j < MAP_SIZE; j++)
            if (m[i][j] == 'F') { food.r = i; food.c = j; break; }

    // 第一优先级：能吃就吃
    if (food.r != -1 && find_path(s[0], food, slen, s)) {
        Pos target_step = food;
        while (!(pre[target_step.r][target_step.c].r == s[0].r &&
            pre[target_step.r][target_step.c].c == s[0].c)) {
            target_step = pre[target_step.r][target_step.c];
        }

        Pos tmp_body[MAX_LEN];
        tmp_body[0] = target_step;
        for (int i = 1; i < slen; i++) tmp_body[i] = s[i - 1];

        if (eval_space(tmp_body[0], slen, tmp_body, slen / 2) >= slen / 3) {
            if (find_path(tmp_body[0], tmp_body[slen - 1], slen, tmp_body)) {
                for (int i = 0; i < 4; i++)
                    if (s[0].r + dr[i] == target_step.r &&
                        s[0].c + dc[i] == target_step.c)
                        return ds[i];
            }
        }
    }

    // 第二优先级：追尾巴
    int best_dir_idx = -1, max_dist_val = -1;
    for (int i = 0; i < 4; i++) {
        int nr = s[0].r + dr[i], nc = s[0].c + dc[i];
        if (can_go(nr, nc, slen, s, 1)) {
            Pos sim_snake[MAX_LEN];
            sim_snake[0] = (Pos){ nr, nc };
            for (int k = 1; k < slen; k++) sim_snake[k] = s[k - 1];

            if (find_path(sim_snake[0], sim_snake[slen - 1], slen, sim_snake)) {
                int weight = abs(sim_snake[0].r - sim_snake[slen - 1].r) +
                    abs(sim_snake[0].c - sim_snake[slen - 1].c);
                if (weight > max_dist_val) {
                    max_dist_val = weight;
                    best_dir_idx = i;
                }
            }
        }
    }

    if (best_dir_idx != -1) return ds[best_dir_idx];

    // 第三优先级：哪儿空走哪儿
    int fallback_d = 0, most_space = -1;
    for (int i = 0; i < 4; i++) {
        Pos test = { s[0].r + dr[i], s[0].c + dc[i] };
        int temp_space = eval_space(test, slen, s, 50);
        if (temp_space > most_space) {
            most_space = temp_space;
            fallback_d = i;
        }
    }

    snake_mood = (snake_mood + mvs) % 11;
    return ds[fallback_d];
}

/*  初始化  */

void init_game_state() {
    Pos head_pos = { -1, -1 };
    for (int i = 0; i < MAP_SIZE; i++)
        for (int j = 0; j < MAP_SIZE; j++)
            if (m[i][j] == 'H') { head_pos.r = i; head_pos.c = j; }

    s[0] = head_pos;
    slen = 1;

    // 简单找身体，最多找两节
    while (slen < 3) {
        Pos cur = s[slen - 1];
        for (int d = 0; d < 4; d++) {
            int nr = cur.r + dr[d], nc = cur.c + dc[d];
            if (nr >= 0 && nr < MAP_SIZE && nc >= 0 && nc < MAP_SIZE &&
                m[nr][nc] == 'B') {
                int already_in = 0;
                for (int k = 0; k < slen; k++)
                    if (s[k].r == nr && s[k].c == nc) already_in = 1;
                if (!already_in) {
                    s[slen++] = (Pos){ nr, nc };
                    break;
                }
            }
        }
    }
}

/*  主循环  */

int main() {
    for (int i = 0; i < MAP_SIZE; i++)
        scanf("%s", m[i]);
    scanf("%d", &f_iv);

    init_game_state();

    while (1) {
        char next_m = get_move();
        printf("%c\n%d\n", next_m, sc);
        fflush(stdout);

        int n_fr, n_fc;
        if (scanf("%d %d", &n_fr, &n_fc) != 2) break;

        // 比赛结束
        if (n_fr == 100) {
            for (int i = 0; i < MAP_SIZE; i++) printf("%s\n", m[i]);
            printf("%d\n", sc);
            break;
        }

        mvs++;
        int dir_id = 0;
        for (int i = 0; i < 4; i++)
            if (ds[i] == next_m) dir_id = i;

        Pos new_h = { s[0].r + dr[dir_id], s[0].c + dc[dir_id] };
        int eat_flag = (m[new_h.r][new_h.c] == 'F');
        int grow_flag = (eat_flag || mvs % f_iv == 0);

        if (eat_flag) sc += 10;

        if (!grow_flag) {
            m[s[slen - 1].r][s[slen - 1].c] = '.';
            for (int i = slen - 1; i > 0; i--) s[i] = s[i - 1];
        }
        else {
            for (int i = slen; i > 0; i--) s[i] = s[i - 1];
            slen++;
        }

        s[0] = new_h;

        for (int i = 0; i < slen; i++)
            m[s[i].r][s[i].c] = (i == 0 ? 'H' : 'B');

        if (n_fr < MAP_SIZE) m[n_fr][n_fc] = 'F';
    }

    return 0;
}
