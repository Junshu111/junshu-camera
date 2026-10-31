# 在电脑上开始

## 直接阅读

解压仓库 ZIP，打开 `docs/index.html`。目录、章节、图片和字体均可离线读取。无需先安装 Node.js、npm 或相机依赖。

网页勾选记录只保存在当前浏览器。浏览器阻止本地存储时，阅读仍可继续，但勾选记录可能不会保留。

## 需要修改教程时

本项目的网页生成脚本只使用 Python 3 标准库。先确认本机是否已有 Python，再运行：

```sh
python3 tools/check_site.py
```

预期输出：20 个网页、16 章、0 个 PDF、错误列表为空。

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

先把 GitHub 仓库链接发给 AI，再把 `docs/setup.html` 中的完整提示词发到同一个对话框。AI 应先获取并阅读 README、本文件和 AGENTS.md，核对操作系统与依赖，并先跑只读自检。若工具不能直接读取仓库或操作电脑，请它用简单中文说明需要怎样配合。

## 电脑与树莓派分开

以上命令仅阅读或构建教程网页。它们不会安装相机程序，也不会连接树莓派。真实相机需要匹配的 Linux 图形环境、摄像头、屏幕、供电和软件版本；相关步骤见第06至11章。匹配的公开软件安装包未发布前，先用官方工具验证基础相机功能。
