# 在电脑上开始

第一次来先读 [START-HERE.md](START-HERE.md)。要用 AI 分工，先按 [AI-WORKFLOW.md](AI-WORKFLOW.md)选择新项目或当前项目、准备 00＋A—F 七个对话并核对交接，再回到 00 执行这里的电脑端步骤。没有自动创建能力时可手动；也可以明确选择先用一个对话。

## 直接阅读

解压仓库 ZIP，打开 `docs/index.html`。目录、章节、图片和字体均可离线读取。无需先安装 Node.js、npm 或相机依赖。

网页勾选记录只保存在当前浏览器。浏览器阻止本地存储时，阅读仍可继续，但勾选记录可能不会保留。

## 需要修改教程时

本项目的网页生成脚本只使用 Python 3 标准库。先确认本机是否已有 Python，再运行：

```sh
python3 tools/check_site.py
```

预期输出：20 个网页、16 章、0 个 PDF、错误列表为空。引导资料再运行 `python3 tools/check_onboarding.py`，预期七个角色齐全、两处网页和下载文本的提示词一致。Windows 有 Python Launcher 时可用 `py -3`。

没有 Python 时不必先安装：先打开网页、核对目录和链接，告诉 AI“未运行自动自检”。需要编辑教程时再决定是否安装。云端检查只说明云端副本的情况，不能代替你电脑上的检查。

修改章节内容后重新生成：

```sh
python3 tools/build_site.py
python3 tools/check_site.py
```

如果希望通过本机网址预览，在仓库目录运行：

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory docs
```

打开 `http://127.0.0.1:8000/`；结束预览后在终端按 Ctrl+C。Windows 可根据 Python 安装情况把 `python3` 换成 `py`。

## 交给 Codex 等 AI 工具

先把 GitHub 仓库链接发给 AI，再把 `docs/setup.html` 中的完整提示词发到同一个对话。提示词也可从 `docs/downloads/AI搭建环境提示词.txt` 复制；仅发送网址不保证 AI 会自行执行仓库中的引导。

AI 应实际读取 README、本文件、AGENTS.md 和 AI-WORKFLOW.md，再按你选择的项目方式准备对话。若工具不能直接读取仓库或操作电脑，请它一次给一个简单中文步骤，说明在哪里操作、完成后应看到什么。不要给它 GitHub 写权限来解决公开资料的阅读问题。

自己的进度放在 `.camera-work/STATUS.md`，对话核对放在 `.camera-work/THREADS.md`；未核对项保留待确认。不同聊天的消息不会自动全部共享，交接要带事实、证据和下一步。

## 电脑与树莓派分开

以上命令仅阅读或构建教程网页。它们不会安装相机程序，也不会连接树莓派。真实相机需要匹配的 Linux 图形环境、摄像头、屏幕、供电和软件版本；相关步骤见第06至11章。匹配的公开软件安装包未发布前，先用官方工具验证基础相机功能。
