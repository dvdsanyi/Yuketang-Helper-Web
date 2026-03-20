# THU Yuketang Helper Web

A web-based automation tool for Tsinghua University's Yuketang online learning platform. It monitors active lessons in real time and automatically handles sign-ins, quizzes, bullet chats, and roll call notifications.
Based on [RainClassroomAssitant](https://github.com/TrickyDeath/RainClassroomAssitant) and [THU-Yuketang-Helper](https://github.com/zhangchi2004/THU-Yuketang-Helper)

## Features

- **Auto Sign-in** — Automatically checks in when a lesson starts
- **Auto Quiz Answering** — Handles single/multiple choice, voting, and short-answer questions with configurable strategies (random or AI)
- **Auto Danmu** — Sends bullet chat messages automatically
- **Roll Call Notifications** — Alerts you when roll call happens
- **Per-Course Settings** — Fine-grained control over automation for each course
- **Bilingual UI** — English and Chinese interface
- **Real-time Dashboard** — Live activity feed of all lesson events

## Quick Start

### Prerequisites

- Python 3.7+
- Node.js + npm

### Install

```bash
# Backend
cd backend
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
```

### Run

```bash
# From project root — starts both frontend and backend
python start.py
```

- Frontend: <http://localhost:5173>
- Backend: <http://localhost:8000>

### Stop

```bash
python stop.py
```

## TODO

- [ ] Add AI quiz answering strategy
- [ ] Implement Fill-in-the-blank question support

---

# THU 雨课堂助手 Web 版

基于 Web 的清华大学雨课堂自动化工具。实时监控进行中的课程，自动处理签到、答题、弹幕和点名通知。
基于 [RainClassroomAssitant](https://github.com/TrickyDeath/RainClassroomAssitant) 和 [THU-Yuketang-Helper](https://github.com/zhangchi2004/THU-Yuketang-Helper)

## 功能

- **自动签到** — 课程开始时自动签到
- **自动答题** — 支持单选、多选、投票和简答题，可配置答题策略（随机或 AI）
- **自动弹幕** — 自动发送弹幕消息
- **点名提醒** — 点名时发送通知提醒
- **分课程设置** — 对每门课程进行精细化的自动化控制
- **双语界面** — 支持中英文切换
- **实时面板** — 实时展示所有课程事件动态

## 快速开始

### 环境要求

- Python 3.7+
- Node.js + npm

### 安装

```bash
# 后端
cd backend
pip install -r requirements.txt

# 前端
cd ../frontend
npm install
```

### 启动

```bash
# 在项目根目录下运行 — 同时启动前端和后端
python start.py
```

- 前端：<http://localhost:5173>
- 后端：<http://localhost:8000>

### 停止

```bash
python stop.py
```

## 待办

- [ ] 添加 AI 答题策略
- [ ] 支持填空题
