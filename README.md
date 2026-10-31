# 自制相机

从零件到成品的中文制作教程。用十六章讲清准备、连接、首次拍摄、控件、打印、装配与检验。

仓库：https://github.com/Junshu111/junshu-camera

网站阅读地址：https://junshu111.github.io/junshu-camera/

## 第一次来，先让 AI 帮你开始

先读 [START-HERE.md](START-HERE.md)。只需要两次复制：把仓库链接发给 Codex 等 AI，再把[网页两步入口](https://junshu111.github.io/junshu-camera/setup.html)中的完整提示词发到同一个对话。

AI 应先展示“00 总工作台＋A—F 专项”的文字分工图，让你选择“新项目＋七个对话”（推荐）或“当前项目＋七个对话”。选择后才创建或引导你创建，再核对交接。没有这些能力的应用会提供手动方式；也可以先用一个对话阅读。

创建好以后，始终先回到“相机 00｜总工作台”，一次做一步。详细执行顺序见 [AI-WORKFLOW.md](AI-WORKFLOW.md)；七角色启动文字在 [workflow/handoffs](workflow/handoffs)，空白进度模板在 [workflow/templates](workflow/templates)。不要把模板或创建成功当成已经读完交接。

已经下载、只想阅读：直接双击 `docs/index.html`，无需安装依赖。电脑端说明见 [GETTING-STARTED.md](GETTING-STARTED.md)。

## 这一版包含什么

20 个网页、16 章正文、关系示意图、AI 起步提示词、七对话协作交接、进度模板和可编辑内容源，以及本次相机的参考软件源码、冻结归档、STL／3MF、Blender装配、FreeCAD／STEP和需求记录。网站不依赖在线字体或后端服务。[离线 PDF 手册](docs/downloads/diy-camera-guide-zh.pdf) 已公开，亦可从网站最下面下载。

本次参考软件和模型已提供：[完整资料入口](RELEASE-CONTENTS.md)、[软件](software/README.md)、[模型与版本选择](models/README.md)。这些资料对应本项目的硬件与制作记录，观众可参考和改造，尚未验证在所有新设备上的通用安装。章节中明确区分已实测、本地检查和待验证内容；不要把特定设备的安装器当作通用新系统安装器。首页中央方盒来自项目外形，另两款是概念外观。

## 修改和更新

1. 编辑 `content/hardware.json`、`software.json`、`structure.json` 的对应章节。
2. 编辑 `content/tables.json` 更新购买或接线核对表。
3. 执行 `python3 tools/build_site.py`，再执行 `python3 tools/check_site.py`、`python3 tools/check_onboarding.py` 和 `python3 -B tools/check_reference.py`。
4. 样式在 `docs/assets/guide.css`、`home.css`、`ai-setup.css`，图片在 `docs/assets/`。
5. 观众自己的状态和对话登记保存在资料目录下 `.camera-work/`，默认不上传。

Windows 上可按本机 Python 安装方式使用 `py` 代替 `python3`。纯阅读不需要安装 Python。

网页与离线 PDF 共用正文。PDF 作者端生成文件单独保存；已下载的旧文件不会自动更新，请到网站底部获取新版。

## 项目资料与许可

事实核对到 2026-10-30；具体依据列在各章末尾。本次制作所需的当前技术参考已整理；逐文件来源和校验见 SOURCE-MANIFEST.json。历史失败草稿、设备日志和私人运行记录不作为通用启动资料。频道视频制作资料不在公开范围内。

字体来自 Fontsource 的 Noto Sans SC 和 Inter；随附原许可。自有源码采用 MIT，模型和图文采用 CC BY 4.0；第三方保留原许可。详见 [许可范围](LICENSES/README.md)。
