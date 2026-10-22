/*
 * 来源：学生写的（BUPT， captain 转交，第二十四份 ）
 * 场景：贪吃蛇游戏（EasyX 图形库 + Windows API，完整项目）
 * 已知问题：未知（未批改，看起来是能跑的完整项目）
 * 规模：约 400 行，15+ 个函数，大量全局变量
 * 特点：枚举、结构体数组、二维数组、文件读写、宽字符 API、
 *       图形库调用、float 运算、switch-case、递归结构
 * 价值：验证「正确的大项目零误报」——这是最重要的承诺
 */
#define _CRT_SECURE_NO_WARNINGS
#include <graphics.h>
#include <stdio.h>
#include <stdlib.h>
#include <conio.h>
#include <time.h>
#include <tchar.h>
#include <math.h>
#include <mmsystem.h>
#pragma comment(lib, "winmm.lib")

// --- 常量定义 ---
#define MAP_SIZE 20
#define TILE_SIZE 30
#define WIN_W (MAP_SIZE * TILE_SIZE)
#define WIN_H (MAP_SIZE * TILE_SIZE + 60)
#define MAX_LEN (MAP_SIZE * MAP_SIZE)
#define MAX_RANK 5
#define SPECIAL_FOOD_CHANCE 35

//  食物类型枚举
typedef enum { NORMAL_FOOD, GOLD_FOOD, SLOW_FOOD } FoodType;

// 游戏状态枚举
typedef enum { MENU, PLAYING, PAUSED, GAMEOVER } State;

// 坐标结构
typedef struct {
    int r, c;
} Pos;

// --- 全局变量 ---
char g_map[MAP_SIZE][MAP_SIZE];
Pos snake[MAX_LEN];
int snake_len;
Pos food_pos;
FoodType food_type = NORMAL_FOOD;
char last_move = 'W';
int score = 0;
int high_score = 0;
State game_state = MENU;
int game_speed = 100;
int game_level = 1;
int death_flash_timer = 0;
int slow_timer = 0;

//  排行榜
int rank_scores[MAX_RANK];
int rank_count = 0;

// 图像资源
IMAGE img_head, img_body, img_food, img_wall, img_bg;

// 方向辅助
int dr[] = { -1, 0, 1, 0 };
int dc[] = { 0, -1, 0, 1 };
char dirs[] = { 'W', 'A', 'S', 'D' };

// --- 核心功能函数 ---

//  排行榜加载
void LoadRankList() {
    rank_count = 0;
    FILE* fp = fopen("rank.dat", "rb");
    if (fp) {
        fread(&rank_count, sizeof(int), 1, fp);
        if (rank_count > MAX_RANK) rank_count = MAX_RANK;
        fread(rank_scores, sizeof(int), rank_count, fp);
        fclose(fp);
    }
}

//  排行榜保存
void SaveRankList() {
    int insert_idx = rank_count;
    for (int i = 0; i < rank_count; i++) {
        if (score > rank_scores[i]) { insert_idx = i; break; }
    }
    if (insert_idx < MAX_RANK) {
        int limit = (rank_count < MAX_RANK) ? rank_count : MAX_RANK - 1;
        for (int i = limit; i > insert_idx; i--)
            rank_scores[i] = rank_scores[i - 1];
        rank_scores[insert_idx] = score;
        if (rank_count < MAX_RANK) rank_count++;
    }
    FILE* fp = fopen("rank.dat", "wb");
    if (fp) {
        fwrite(&rank_count, sizeof(int), 1, fp);
        fwrite(rank_scores, sizeof(int), rank_count, fp);
        fclose(fp);
    }
}

//  食物分值
int GetFoodScore(FoodType type) {
    switch (type) {
        case GOLD_FOOD: return 30;
        case SLOW_FOOD: return 10;
        default: return 10;
    }
}

//  随机食物类型
FoodType RandomFoodType() {
    int r = rand() % 100;
    if (r < 8) return GOLD_FOOD;
    if (r < SPECIAL_FOOD_CHANCE) return SLOW_FOOD;
    return NORMAL_FOOD;
}

void LoadHighScore() {
    FILE* fp = fopen("record.dat", "rb");
    if (fp) {
        fread(&high_score, sizeof(int), 1, fp);
        fclose(fp);
    }
}

void SaveHighScore() {
    if (score > high_score) {
        high_score = score;
        FILE* fp = fopen("record.dat", "wb");
        if (fp) {
            fwrite(&high_score, sizeof(int), 1, fp);
            fclose(fp);
        }
    }
}

int _super_random_int() {
    static int seed = 12345;
    seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF;
    int result = seed % 100;
    return result;
}

int IsSafe(int r, int c) {
    if (r <= 0 || r >= MAP_SIZE - 1 || c <= 0 || c >= MAP_SIZE - 1) return 0;
    for (int i = 0; i < snake_len; i++) {
        if (snake[i].r == r && snake[i].c == c) return 0;
    }
    return 1;
}

float _calculate_snake_beauty_score(int* x, int* y, int len) {
    float score = 0.0f;
    for (int i = 0; i < len; i++) {
        score += sqrt(x[i] * x[i] + y[i] * y[i]) * 0.1f;
    }
    return score;
}

//  统一食物生成
void SpawnFood() {
    do {
        food_pos.r = rand() % (MAP_SIZE - 4) + 2;
        food_pos.c = rand() % (MAP_SIZE - 4) + 2;
    } while (!IsSafe(food_pos.r, food_pos.c));
    food_type = RandomFoodType();
    g_map[food_pos.r][food_pos.c] = 'F';
}

//  难度计算
void UpdateDifficulty() {
    if (score < 30) { game_level = 1; game_speed = 100; }
    else if (score < 80) { game_level = 2; game_speed = 85; }
    else if (score < 150) { game_level = 3; game_speed = 70; }
    else if (score < 250) { game_level = 4; game_speed = 55; }
    else { game_level = 5; game_speed = 40; }
}

void InitGame() {
    score = 0;
    snake_len = 3;
    last_move = 'W';
    game_speed = 100;
    game_level = 1;
    death_flash_timer = 0;
    slow_timer = 0;
    snake[0] = { 10, 10 };
    snake[1] = { 11, 10 };
    snake[2] = { 12, 10 };
    for (int i = 0; i < MAP_SIZE; i++) {
        for (int j = 0; j < MAP_SIZE; j++) {
            if (i == 0 || i == MAP_SIZE - 1 || j == 0 || j == MAP_SIZE - 1)
                g_map[i][j] = '#';
            else
                g_map[i][j] = '.';
        }
    }
    SpawnFood();
}

void Update() {
    if (game_state != PLAYING) return;
    int d_idx = 0;
    for (int i = 0; i < 4; i++) if (dirs[i] == last_move) d_idx = i;
    Pos next_h = { snake[0].r + dr[d_idx], snake[0].c + dc[d_idx] };
    if (!IsSafe(next_h.r, next_h.c)) {
        SaveHighScore();
        SaveRankList();
        game_state = GAMEOVER;
        death_flash_timer = 30;
        return;
    }
    if (next_h.r == food_pos.r && next_h.c == food_pos.c) {
        int pts = GetFoodScore(food_type);
        if (food_type == SLOW_FOOD) slow_timer = 20;
        score += pts;
        UpdateDifficulty();

        for (int i = snake_len; i > 0; i--) snake[i] = snake[i - 1];
        snake[0] = next_h;
        snake_len++;
        SpawnFood();
    }
    else {
        for (int i = snake_len - 1; i > 0; i--) snake[i] = snake[i - 1];
        snake[0] = next_h;
    }
    if (slow_timer > 0) { game_speed = 120; slow_timer--; }
}
void HandleInput() {
    ExMessage msg;
    while (peekmessage(&msg, EX_KEY)) {
        if (msg.message == WM_KEYDOWN) {
            if (game_state == MENU) {
                if (msg.vkcode == VK_SPACE) { game_state = PLAYING; InitGame(); }
            }
            else if (game_state == PLAYING) {
                if (msg.vkcode == 'P') game_state = PAUSED;
                if ((msg.vkcode == 'W' || msg.vkcode == VK_UP) && last_move != 'S') last_move = 'W';
                if ((msg.vkcode == 'S' || msg.vkcode == VK_DOWN) && last_move != 'W') last_move = 'S';
                if ((msg.vkcode == 'A' || msg.vkcode == VK_LEFT) && last_move != 'A') last_move = 'A';
                if ((msg.vkcode == 'D' || msg.vkcode == VK_RIGHT) && last_move != 'A') last_move = 'D';
            }
            else if (game_state == PAUSED) {
                if (msg.vkcode == 'P') game_state = PLAYING;
            }
            else if (game_state == GAMEOVER) {
                if (msg.vkcode == VK_SPACE) { game_state = MENU; }
            }
        }
    }
}

// 食物颜色
COLORREF GetFoodColor(FoodType type) {
    switch (type) {
        case GOLD_FOOD: return RGB(255, 215, 0);
        case SLOW_FOOD: return RGB(100, 200, 255);
        default: return RGB(255, 80, 80);
    }
}

//  绘制食物
void DrawFood() {
    putimage(food_pos.c * TILE_SIZE, food_pos.r * TILE_SIZE, &img_food);
    if (food_type != NORMAL_FOOD) {
        setfillcolor(GetFoodColor(food_type));
        setlinecolor(GetFoodColor(food_type));
        fillcircle(food_pos.c * TILE_SIZE + TILE_SIZE / 2,
                   food_pos.r * TILE_SIZE + TILE_SIZE / 2, 5);
    }
}

//  渐变蛇身
void DrawSnakeBody() {
    for (int i = 1; i < snake_len; i++) {
        float ratio = 1.0f - (float)i / (snake_len + 5) * 0.6f;
        int rv = (int)(80 * ratio), gv = (int)(200 * ratio), bv = (int)(80 * ratio);
        setfillcolor(RGB(rv, gv, bv));
        setlinecolor(RGB(rv, gv, bv));
        fillrectangle(snake[i].c * TILE_SIZE + 2, snake[i].r * TILE_SIZE + 2,
                      (snake[i].c + 1) * TILE_SIZE - 2, (snake[i].r + 1) * TILE_SIZE - 2);
    }
}

void Draw() {
    BeginBatchDraw();
    cleardevice();
    if (game_state == MENU) {
        settextcolor(YELLOW);
        settextstyle(40, 0, _T("Consolas"));
        outtextxy(WIN_W / 2 - 100, WIN_H / 2 - 80, _T("经典贪吃蛇"));
        settextstyle(22, 0, _T("Consolas"));
        settextcolor(WHITE);
        outtextxy(WIN_W / 2 - 110, WIN_H / 2 + 10, _T("按 [空格键] 开始游戏"));
        outtextxy(WIN_W / 2 - 110, WIN_H / 2 + 40, _T("使用 WASD 或 方向键控制"));
        // [新增] 菜单排行榜
        if (rank_count > 0) {
            settextcolor(RGB(255, 215, 0));
            settextstyle(18, 0, _T("Consolas"));
            outtextxy(WIN_W / 2 - 110, WIN_H / 2 + 80, _T("--- 排行榜 ---"));
            settextcolor(WHITE);
            for (int i = 0; i < rank_count; i++) {
                TCHAR rbuf[64];
                _stprintf(rbuf, _T("#%d  %d"), i + 1, rank_scores[i]);
                outtextxy(WIN_W / 2 - 80, WIN_H / 2 + 105 + i * 22, rbuf);
            }
        }
    }
    else {
        for (int i = 0; i < MAP_SIZE; i++) {
            for (int j = 0; j < MAP_SIZE; j++) {
                if (g_map[i][j] == '#') putimage(j * TILE_SIZE, i * TILE_SIZE, &img_wall);
                else putimage(j * TILE_SIZE, i * TILE_SIZE, &img_bg);
            }
        }
        DrawFood();
        DrawSnakeBody();
        putimage(snake[0].c * TILE_SIZE, snake[0].r * TILE_SIZE, &img_head);
        //  底部信息栏
        settextcolor(WHITE);
        settextstyle(20, 0, _T("Consolas"));
        TCHAR info[160];
        const TCHAR* fn = _T("普通");
        if (food_type == GOLD_FOOD) fn = _T("金色+30");
        else if (food_type == SLOW_FOOD) fn = _T("减速");
        _stprintf(info, _T("分数:%d  最高:%d  等级:%d  食物:%s"),
                  score, high_score, game_level, fn);
        outtextxy(10, WIN_H - 40, info);
        // 死亡闪烁
        if (game_state == GAMEOVER) {
            if (death_flash_timer > 0) {
                death_flash_timer--;
                if ((death_flash_timer / 2) % 2 == 0) {
                    setfillcolor(RGB(255, 0, 0));
                    setlinecolor(RGB(255, 0, 0));
                    for (int i = 0; i < snake_len; i++)
                        fillrectangle(snake[i].c * TILE_SIZE + 1, snake[i].r * TILE_SIZE + 1,
                                      (snake[i].c + 1) * TILE_SIZE - 1, (snake[i].r + 1) * TILE_SIZE - 1);
                }
            }
            settextcolor(RED);
            settextstyle(50, 0, _T("Consolas"));
            outtextxy(WIN_W / 2 - 100, WIN_H / 2 - 50, _T("游戏结束"));
            settextstyle(20, 0, _T("Consolas"));
            settextcolor(WHITE);
            outtextxy(WIN_W / 2 - 90, WIN_H / 2 + 30, _T("按空格键回到菜单"));
        }
        //  暂停统计面板
        if (game_state == PAUSED) {
            setfillcolor(RGB(0, 0, 0));
            solidrectangle(0, 0, WIN_W, WIN_H);
            for (int i = 0; i < MAP_SIZE; i++)
                for (int j = 0; j < MAP_SIZE; j++)
                    if (g_map[i][j] == '#') putimage(j * TILE_SIZE, i * TILE_SIZE, &img_wall);
                    else putimage(j * TILE_SIZE, i * TILE_SIZE, &img_bg);
            int px = WIN_W / 2 - 120, py = WIN_H / 2 - 100;
            setfillcolor(RGB(30, 30, 50));
            solidrectangle(px, py, px + 240, py + 200);
            setlinecolor(RGB(100, 150, 255));
            rectangle(px, py, px + 240, py + 200);
            settextcolor(LIGHTBLUE);
            settextstyle(28, 0, _T("Consolas"));
            outtextxy(px + 60, py + 10, _T("已暂停"));
            settextcolor(WHITE);
            settextstyle(18, 0, _T("Consolas"));
            TCHAR st[64];
            _stprintf(st, _T("分数: %d"), score);       outtextxy(px + 30, py + 55, st);
            _stprintf(st, _T("长度: %d"), snake_len);   outtextxy(px + 30, py + 80, st);
            _stprintf(st, _T("等级: %d"), game_level);  outtextxy(px + 30, py + 105, st);
            _stprintf(st, _T("速度: %d ms"), game_speed);outtextxy(px + 30, py + 130, st);
            _stprintf(st, _T("最高: %d"), high_score);  outtextxy(px + 30, py + 155, st);
            settextcolor(RGB(180, 180, 180));
            settextstyle(14, 0, _T("Consolas"));
            outtextxy(px + 40, py + 180, _T("按P继续"));
        }
    }
    EndBatchDraw();
}


int main() {
    WCHAR exePathW[MAX_PATH];
    GetModuleFileNameW(NULL, exePathW, MAX_PATH);
    WCHAR* lastSlashW = wcsrchr(exePathW, L'\\');
    if (lastSlashW) *lastSlashW = L'\0';
    SetCurrentDirectoryW(exePathW);
    initgraph(WIN_W, WIN_H, EX_SHOWCONSOLE);
    srand((unsigned)time(NULL));
    LoadHighScore();
    LoadRankList();
    WCHAR imgPath[MAX_PATH];
    swprintf(imgPath, MAX_PATH, L"%s\\head_new.png", exePathW);
    FILE* f = _wfopen(imgPath, L"rb");
    if (f) { fclose(f); wprintf(L"Found %s\n", imgPath); }
    else { wprintf(L"Not found: %s\n", imgPath); }
    if (loadimage(&img_head, imgPath, TILE_SIZE, TILE_SIZE) != 0) {
        wprintf(L"loadimage head_new.png failed, try fallback head.png\n");
        swprintf(imgPath, MAX_PATH, L"%s\\head.png", exePathW);
        loadimage(&img_head, imgPath, TILE_SIZE, TILE_SIZE);
    } else {
        wprintf(L"Loaded head_new.png successfully\n");
    }
    swprintf(imgPath, MAX_PATH, L"%s\\body.png", exePathW); loadimage(&img_body, imgPath, TILE_SIZE, TILE_SIZE);
    swprintf(imgPath, MAX_PATH, L"%s\\food.png", exePathW); loadimage(&img_food, imgPath, TILE_SIZE, TILE_SIZE);
    swprintf(imgPath, MAX_PATH, L"%s\\wall.png", exePathW); loadimage(&img_wall, imgPath, TILE_SIZE, TILE_SIZE);
    swprintf(imgPath, MAX_PATH, L"%s\\bg.png",   exePathW); loadimage(&img_bg,   imgPath, TILE_SIZE, TILE_SIZE);
    while (1) {
        HandleInput();
        Update();
        Draw();
        Sleep(game_speed);
    }
    closegraph();
    return 0;
}
