/*
 * 来源：学生写的（BUPT， captain 转交，第二十七份 ）
 * 场景：俄罗斯方块（Windows Console API，goto/label 控制）
 * 已知问题：未知（未批改，看起来是能跑的完整项目）
 * 规模：约 480 行，9 个函数，19 种方块的 switch-case
 * 特点：goto/label（label: + goto label）、struct Tetris 指针
 *       全程传参、HANDLE 句柄传递、二维数组 [80][80]、
 *       system("cls")、19-case switch、嵌套循环多层
 * 价值：验证 goto 支持 + 大型 switch + W30 稳定性
 */
#include <stdio.h>
#include <windows.h>
#include <conio.h>
#include <time.h>

//游戏窗口
#define FrameX 4   //游戏窗口左上角的X轴坐标
#define FrameY 4   //游戏窗口左上角的Y轴坐标
#define Frame_height  20 //游戏窗口的高度
#define Frame_width   18 //游戏窗口的宽度

//定义全局变量
int i,j,temp,temp1,temp2; //temp,temp1,temp2用于记住和转换方块变量的值
int a[80][80]={0};   //标记游戏屏幕的图案：2,1,0分别表示该位置为游戏边框、方块、无图案;初始化为无图案
int b[4];     //标记4个"口"方块：1表示有方块，0表示无方块

//声明俄罗斯方块的结构体
struct Tetris
{
 int x;     //中心方块的x轴坐标
 int y;     //中心方块的y轴坐标
 int flag;    //标记方块类型的序号
 int next;    //下一个俄罗斯方块类型的序号
 int speed;    //俄罗斯方块移动的速度
 int count;    //产生俄罗斯方块的个数
 int score;    //游戏的分数
 int level;    //游戏的等级
};

//函数原型声明
void gotoxy(HANDLE hOut, int x, int y);
void make_frame();
void get_flag(struct Tetris *);
void make_tetris(struct Tetris *);
void print_tetris(HANDLE hOut,struct Tetris *);
void clear_tetris(HANDLE hOut,struct Tetris *);
int if_moveable(struct Tetris *);
void del_full(HANDLE hOut,struct Tetris *);
void start_game();


void main()
{
 make_frame();
 start_game();
}

void gotoxy(HANDLE hOut, int x, int y)
{
 COORD pos;
 pos.X = x;
 pos.Y = y;
 SetConsoleCursorPosition(hOut, pos);
}

void make_frame()
{
 HANDLE hOut = GetStdHandle(STD_OUTPUT_HANDLE);

 gotoxy(hOut,FrameX+Frame_width-5,FrameY-2);
 printf("俄罗斯方块");
 gotoxy(hOut,FrameX+2*Frame_width+3,FrameY+7);
 printf("**********下一个方块：");
 gotoxy(hOut,FrameX+2*Frame_width+3,FrameY+13);
 printf("**********");
 gotoxy(hOut,FrameX+2*Frame_width+3,FrameY+17);
 printf("↑键：变体");
 gotoxy(hOut,FrameX+2*Frame_width+3,FrameY+19);
 printf("空格：暂停游戏");
 gotoxy(hOut,FrameX+2*Frame_width+3,FrameY+15);
 printf("Esc ：退出游戏");

 gotoxy(hOut,FrameX,FrameY);
 printf("╔");
 gotoxy(hOut,FrameX+2*Frame_width-2,FrameY);
 printf("╗");
 gotoxy(hOut,FrameX,FrameY+Frame_height);
 printf("╚");
 gotoxy(hOut,FrameX+2*Frame_width-2,FrameY+Frame_height);
 printf("╝");
 a[FrameX][FrameY+Frame_height]=2;
 a[FrameX+2*Frame_width-2][FrameY+Frame_height]=2;

 for(i=2;i<2*Frame_width-2;i+=2)
 {
  gotoxy(hOut,FrameX+i,FrameY);
  printf("═");
 }
 for(i=2;i<2*Frame_width-2;i+=2)
 {
  gotoxy(hOut,FrameX+i,FrameY+Frame_height);
  printf("═");
  a[FrameX+i][FrameY+Frame_height]=2;
 }
 for(i=1;i<Frame_height;i++)
 {
  gotoxy(hOut,FrameX,FrameY+i);
  printf("║");
  a[FrameX][FrameY+i]=2;
 }
 for(i=1;i<Frame_height;i++)
 {
  gotoxy(hOut,FrameX+2*Frame_width-2,FrameY+i);
  printf("║");
  a[FrameX+2*Frame_width-2][FrameY+i]=2;
 }
}

void make_tetris(struct Tetris *tetris)
{
 a[tetris->x][tetris->y]=b[0];
 switch(tetris->flag)
 {
  case 1:
   {
    a[tetris->x][tetris->y-1]=b[1];
    a[tetris->x+2][tetris->y-1]=b[2];
    a[tetris->x+2][tetris->y]=b[3];
    break;
   }
  case 2:
   {
    a[tetris->x-2][tetris->y]=b[1];
    a[tetris->x+2][tetris->y]=b[2];
    a[tetris->x+4][tetris->y]=b[3];
    break;
   }
  case 3:
   {
    a[tetris->x][tetris->y-1]=b[1];
    a[tetris->x][tetris->y-2]=b[2];
    a[tetris->x][tetris->y+1]=b[3];
    break;
   }
  case 4:
   {
    a[tetris->x-2][tetris->y]=b[1];
    a[tetris->x+2][tetris->y]=b[2];
    a[tetris->x][tetris->y+1]=b[3];
    break;
   }
  case 5:
   {
    a[tetris->x][tetris->y-1]=b[1];
    a[tetris->x][tetris->y+1]=b[2];
    a[tetris->x-2][tetris->y]=b[3];
    break;
   }
  case 6:
   {
    a[tetris->x][tetris->y-1]=b[1];
    a[tetris->x-2][tetris->y]=b[2];
    a[tetris->x+2][tetris->y]=b[3];
    break;
   }
  case 7:
   {
    a[tetris->x][tetris->y-1]=b[1];
    a[tetris->x][tetris->y+1]=b[2];
    a[tetris->x+2][tetris->y]=b[3];
    break;
   }
  case 8:
   {
    a[tetris->x][tetris->y+1]=b[1];
    a[tetris->x-2][tetris->y]=b[2];
    a[tetris->x+2][tetris->y+1]=b[3];
    break;
   }
  case 9:
   {
    a[tetris->x][tetris->y-1]=b[1];
    a[tetris->x-2][tetris->y]=b[2];
    a[tetris->x-2][tetris->y+1]=b[3];
    break;
   }
  case 10:
   {
    a[tetris->x][tetris->y-1]=b[1];
    a[tetris->x-2][tetris->y-1]=b[2];
    a[tetris->x+2][tetris->y]=b[3];
    break;
   }
  case 11:
   {
    a[tetris->x][tetris->y+1]=b[1];
    a[tetris->x+2][tetris->y-1]=b[2];
    a[tetris->x+2][tetris->y]=b[3];
    break;
   }
  case 12:
   {
    a[tetris->x][tetris->y-1]=b[1];
    a[tetris->x][tetris->y+1]=b[2];
    a[tetris->x-2][tetris->y-1]=b[3];
    break;
   }
  case 13:
   {
    a[tetris->x-2][tetris->y]=b[1];
    a[tetris->x-2][tetris->y+1]=b[2];
    a[tetris->x+2][tetris->y]=b[3];
    break;
   }
  case 14:
   {
    a[tetris->x][tetris->y-1]=b[1];
    a[tetris->x][tetris->y+1]=b[2];
    a[tetris->x+2][tetris->y+1]=b[3];
    break;
   }
  case 15:
   {
    a[tetris->x-2][tetris->y]=b[1];
    a[tetris->x+2][tetris->y-1]=b[2];
    a[tetris->x+2][tetris->y]=b[3];
    break;
   }
  case 16:
   {
    a[tetris->x][tetris->y+1]=b[1];
    a[tetris->x][tetris->y-1]=b[2];
    a[tetris->x+2][tetris->y-1]=b[3];
    break;
   }
  case 17:
   {
    a[tetris->x-2][tetris->y]=b[1];
    a[tetris->x-2][tetris->y-1]=b[2];
    a[tetris->x+2][tetris->y]=b[3];
    break;
   }
  case 18:
   {
    a[tetris->x][tetris->y-1]=b[1];
    a[tetris->x][tetris->y+1]=b[2];
    a[tetris->x-2][tetris->y+1]=b[3];
    break;
   }
  case 19:
   {
    a[tetris->x-2][tetris->y]=b[1];
    a[tetris->x+2][tetris->y+1]=b[2];
    a[tetris->x+2][tetris->y]=b[3];
    break;
   }
 }
}

int if_moveable(struct Tetris *tetris)
{
 if(a[tetris->x][tetris->y]!=0)
 {
  return 0;
 }
 else
 {
  if(
   ( tetris->flag==1  && ( a[tetris->x][tetris->y-1]==0   &&
    a[tetris->x+2][tetris->y-1]==0 && a[tetris->x+2][tetris->y]==0 ) ) ||
   ( tetris->flag==2  && ( a[tetris->x-2][tetris->y]==0   &&
    a[tetris->x+2][tetris->y]==0 && a[tetris->x+4][tetris->y]==0 ) )   ||

   ( tetris->flag==3  && ( a[tetris->x][tetris->y-1]==0   &&
    a[tetris->x][tetris->y-2]==0 && a[tetris->x][tetris->y+1]==0 ) )   ||

   ( tetris->flag==4  && ( a[tetris->x-2][tetris->y]==0   &&
    a[tetris->x+2][tetris->y]==0 && a[tetris->x][tetris->y+1]==0 ) )   ||

   ( tetris->flag==5  && ( a[tetris->x][tetris->y-1]==0   &&
    a[tetris->x][tetris->y+1]==0 && a[tetris->x-2][tetris->y]==0 ) )   ||

   ( tetris->flag==6  && ( a[tetris->x][tetris->y-1]==0   &&
    a[tetris->x-2][tetris->y]==0 && a[tetris->x+2][tetris->y]==0 ) )   ||

   ( tetris->flag==7  && ( a[tetris->x][tetris->y-1]==0   &&
    a[tetris->x][tetris->y+1]==0 && a[tetris->x+2][tetris->y]==0 ) )   ||

   ( tetris->flag==8  && ( a[tetris->x][tetris->y+1]==0   &&
    a[tetris->x-2][tetris->y]==0 && a[tetris->x+2][tetris->y+1]==0 ) ) ||

   ( tetris->flag==9  && ( a[tetris->x][tetris->y-1]==0   &&
    a[tetris->x-2][tetris->y]==0 && a[tetris->x-2][tetris->y+1]==0 ) ) ||

   ( tetris->flag==10 && ( a[tetris->x][tetris->y-1]==0   &&
    a[tetris->x-2][tetris->y-1]==0 && a[tetris->x+2][tetris->y]==0 ) ) ||

   ( tetris->flag==11 && ( a[tetris->x][tetris->y+1]==0   &&
    a[tetris->x+2][tetris->y-1]==0 && a[tetris->x+2][tetris->y]==0 ) ) ||

   ( tetris->flag==12 && ( a[tetris->x][tetris->y-1]==0   &&
    a[tetris->x][tetris->y+1]==0 && a[tetris->x-2][tetris->y-1]==0 ) ) ||

   ( tetris->flag==13 && ( a[tetris->x-2][tetris->y]==0   &&
    a[tetris->x-2][tetris->y+1]==0 && a[tetris->x+2][tetris->y]==0 ) ) ||

   ( tetris->flag==14 && ( a[tetris->x][tetris->y-1]==0   &&
    a[tetris->x][tetris->y+1]==0 && a[tetris->x+2][tetris->y+1]==0 ) ) ||

   ( tetris->flag==15 && ( a[tetris->x-2][tetris->y]==0   &&
    a[tetris->x+2][tetris->y-1]==0 && a[tetris->x+2][tetris->y]==0 ) ) ||

   ( tetris->flag==16 && ( a[tetris->x][tetris->y+1]==0   &&
    a[tetris->x][tetris->y-1]==0 && a[tetris->x+2][tetris->y-1]==0 ) ) ||

   ( tetris->flag==17 && ( a[tetris->x-2][tetris->y]==0   &&
    a[tetris->x-2][tetris->y-1]==0 && a[tetris->x+2][tetris->y]==0 ) ) ||

   ( tetris->flag==18 && ( a[tetris->x][tetris->y-1]==0   &&
    a[tetris->x][tetris->y+1]==0 && a[tetris->x-2][tetris->y+1]==0 ) ) ||

   ( tetris->flag==19 && ( a[tetris->x-2][tetris->y]==0   &&
    a[tetris->x+2][tetris->y+1]==0 && a[tetris->x+2][tetris->y]==0 ) ) )

   {
    return 1;
   }
 }
 return 0;
}

void get_flag(struct Tetris *tetris)
{
 tetris->count++;
 srand((unsigned)time(NULL));
 if(tetris->count==1)
 {
  tetris->flag = rand()%19+1;
 }
 tetris->next = rand()%19+1;
}

void print_tetris(HANDLE hOut,struct Tetris *tetris)
{
 for(i=0;i<4;i++)
 {
  b[i]=1;
 }
 make_tetris(tetris);
 for( i=tetris->x-2; i<=tetris->x+4; i+=2 )
 {
  for(j=tetris->y-2;j<=tetris->y+1;j++)
  {
   if( a[i][j]==1 && j>FrameY )
   {
    gotoxy(hOut,i,j);
    printf("□");
   }
  }
 }
 gotoxy(hOut,FrameX+2*Frame_width+3,FrameY+1);
 printf("level : %d",tetris->level);
 gotoxy(hOut,FrameX+2*Frame_width+3,FrameY+3);
 printf("score : %d",tetris->score);
 gotoxy(hOut,FrameX+2*Frame_width+3,FrameY+5);
 printf("speed : %dms",tetris->speed);
}

void clear_tetris(HANDLE hOut,struct Tetris *tetris)
{
 for(i=0;i<4;i++)
 {
  b[i]=0;
 }
 make_tetris(tetris);
 for( i=tetris->x-2; i<=tetris->x+4; i+=2 )
 {
  for(j=tetris->y-2;j<=tetris->y+1;j++)
  {
   if( a[i][j]==0 && j>FrameY )
   {
    gotoxy(hOut,i,j);
    printf("  ");
   }
  }
 }
}

void del_full(HANDLE hOut,struct Tetris *tetris)
{
 int k,del_count=0;
 for(j=FrameY+Frame_height-1;j>=FrameY+1;j--)
 {
  k=0;
  for(i=FrameX+2;i<FrameX+2*Frame_width-2;i+=2)
  {
   if(a[i][j]==1)
   {
    k++;
    if(k==Frame_width-2)
    {
     for(k=FrameX+2;k<FrameX+2*Frame_width-2;k+=2)
     {
      a[k][j]=0;
      gotoxy(hOut,k,j);
      printf("  ");
      Sleep(1);
     }
     for(k=j-1;k>FrameY;k--)
     {
      for(i=FrameX+2;i<FrameX+2*Frame_width-2;i+=2)
      {
       if(a[i][k]==1)
       {
        a[i][k]=0;
        gotoxy(hOut,i,k);
        printf("  ");
        a[i][k+1]=1;
        gotoxy(hOut,i,k+1);
        printf("□");
       }
      }
     }
     j++;
     del_count++;
    }
   }
  }
 }
 tetris->score+=100*del_count;
 if( del_count>0 && ( tetris->score%1000==0 || tetris->score/1000>tetris->level-1 ) )
 {
  tetris->speed-=20;
  tetris->level++;
 }
}

void start_game()
{
 HANDLE hOut = GetStdHandle(STD_OUTPUT_HANDLE);
 struct Tetris t,*tetris=&t;
 unsigned char ch;

 tetris->count=0;
 tetris->speed=300;
 tetris->score=0;
 tetris->level=1;

 while(1)
 {
  get_flag(tetris);
  temp=tetris->flag;

  tetris->x=FrameX+2*Frame_width+6;
  tetris->y=FrameY+10;
  tetris->flag = tetris->next;
  print_tetris(hOut,tetris);

  tetris->x=FrameX+Frame_width;
  tetris->y=FrameY-1;
  tetris->flag=temp;

  while(1)
  {
   label:print_tetris(hOut,tetris);
   Sleep(tetris->speed);
   clear_tetris(hOut,tetris);
   temp1=tetris->x;
   temp2=tetris->flag;
   if(kbhit())
   {
    ch=getch();
    if(ch==75)
    {
     tetris->x-=2;
    }
    if(ch==77)
    {
     tetris->x+=2;
    }
    if(ch==72)
    {
     if( tetris->flag>=2 && tetris->flag<=3 )
     {
      tetris->flag++;
      tetris->flag%=2;
      tetris->flag+=2;
     }
     if( tetris->flag>=4 && tetris->flag<=7 )
     {
      tetris->flag++;
      tetris->flag%=4;
      tetris->flag+=4;
     }
     if( tetris->flag>=8 && tetris->flag<=11 )
     {
      tetris->flag++;
      tetris->flag%=4;
      tetris->flag+=8;
     }
     if( tetris->flag>=12 && tetris->flag<=15 )
     {
      tetris->flag++;
      tetris->flag%=4;
      tetris->flag+=12;
     }
     if( tetris->flag>=16 && tetris->flag<=19 )
     {
      tetris->flag++;
      tetris->flag%=4;
      tetris->flag+=16;
     }
    }
    if(ch==32)
    {
     print_tetris(hOut,tetris);
     while(1)
     {
      if(kbhit())
      {
       ch=getch();
       if(ch==32)
       {
        goto label;
       }
      }
     }
    }
    if(if_moveable(tetris)==0)
    {
     tetris->x=temp1;
     tetris->flag=temp2;
    }
    else
    {
     goto label;
    }
   }
   tetris->y++;
   if(if_moveable(tetris)==0)
   {
    tetris->y--;
    print_tetris(hOut,tetris);
    del_full(hOut,tetris);
    break;
   }
  }

  for(i=tetris->y-2;i<tetris->y+2;i++)
  {
   if(i==FrameY)
   {
    j=0;
   }
  }
  if(j==0)
  {
   system("cls");
   getch();
   break;
  }
  tetris->flag = tetris->next;
  tetris->x=FrameX+2*Frame_width+6;
  tetris->y=FrameY+10;
  clear_tetris(hOut,tetris);
 }
}
