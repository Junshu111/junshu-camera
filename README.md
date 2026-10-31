# Junshu 自制相机

从零件到成品的中文制作教程。用十六章讲清准备、连接、首次拍摄、控件、打印、装配与检验。

仓库：https://github.com/Junshu111/junshu-camera

网站阅读地址（部署完成后生效）：https://junshu111.github.io/junshu-camera/

## 先从哪里开始

- 直接双击 `docs/index.html`，可以离线阅读。
- 初次接触，先到首页底部的 AI 起步区：复制仓库链接给 AI，再复制提示词。
- 完成电脑端准备后，从 `docs/route.html` 的第01章开始。
- 让 AI 帮忙时，先读 `GETTING-STARTED.md`；提示词在 `docs/setup.html`。
- 购买与接线对照表可在对应章节下载 CSV。

## 这一版包含什么

20 个网页、16 章正文、关系示意图、项目状态、AI 起步提示词和可编辑内容源。网站不依赖在线字体或后端服务。PDF 暂不随仓库发布。

相机软件与打印模型的通用发行包尚未随这一版提供。章节中明确区分已实测、本地检查和待验证内容；不要把特定设备的安装器当作通用新系统安装器。首页中央方盒来自项目外形，另两款是概念外观。

## 修改和更新

1. 编辑 `content/hardware.json`、`software.json`、`structure.json` 的对应章节。
2. 编辑 `content/tables.json` 更新购买或接线核对表。
3. 执行 `python3 tools/build_site.py`，再执行 `python3 tools/check_site.py`。
4. 样式在 `docs/assets/guide.css`，图片在 `docs/assets/`。

Windows 上可按本机 Python 安装方式使用 `py` 代替 `python3`。纯阅读不需要安装 Python。

网页与本地 PDF 共用正文。PDF 的作者端生成文件单独保存，确认后再决定是否纳入仓库。

## 项目资料与许可

事实核对到 2026-10-30；具体依据列在各章末尾。原项目的全部工程、日志与源文件尚未随教程全部发布。频道视频制作资料不在公开范围内。

字体来自 Fontsource 的 Noto Sans SC 和 Inter；随附原许可。项目自身的软件、模型、图文开源许可将在对应发行包中明确，不以仓库可见性代替许可说明。
