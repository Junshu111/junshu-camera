# 相机软件参考源码 · New Cam 1.0.0

这里公开 Junshu 本次相机实际使用的源码与冻结发行归档，供阅读、复现思路和继续修改。不是适用于所有新树莓派的一键安装器。先在总工作台核对自己的硬件、系统、图形环境和路径，再决定如何适配。

## 包含内容

- pi-camera-1.0/：完整应用源码、原 README、功能边界、测试源码与图片资源。
- pi-boot-logo-1.0/：Plymouth 启动画面和系统配置源码。
- tools/：原构建、包验证、启动器、权限兼容修复和远程安装支撑模块。
- dist-1.0/：冻结应用和 boot 两个 tar.gz，以及各自的 SHA256。它们与原交付版本一致。
- reference-mac-entries/：原 Mac 入口以 `.command.txt` 保存，仅供查看其调用方式；不是让新手双击执行的通用入口。

## 原设备条件

记录对应 Pi 3B+、Camera Module 2 / IMX219、480×800 DSI 触摸屏，以及既有 Linux 图形桌面、Picamera2/libcamera、PyQt5/Qt5、Pillow 和 GPIO 接口。原设备系统记录为 Debian 13 trixie / aarch64；具体核查以程序与教程为准，不把作者环境当成读者环境。

部分原代码有意固定 `/home/junshu`、旧 0.4.0 的启动链、已安装电源服务及 boot 0.1.1 的状态。`junshu`、`camera-test.local` 是原设备的账户／主机配置示例，不是读者应照抄的设备身份。程序保留这些检查是为了拒绝未知状态；修改后须重新验证，不能删掉检查来强行安装。

原安装器会影响用户会话、Plymouth、initramfs 与启动配置。下载源码不代表已授权执行这些动作。首次两步提示词只准备工作台和电脑端，不连接设备、不修改启动或安装相机系统。

## 可在电脑上做的只读核对

在整个仓库根目录，使用已有 Python 3：

```sh
python3 -B tools/check_reference.py
```

它核对复制清单、冻结归档和 SHA、源码语法、STL 与 3MF 的基本完整性，不导入 GUI、不打开 GPIO、不启动网络服务器、不连接树莓派、不安装依赖。没有 Python 时先阅读文件与教程。

不要在这份完整源码树上直接运行 `pi-camera-1.0/check_package.py` 的目录验证：它只接受精确的发行清单，会拒绝源码树额外的测试和支撑文件。该验证器应验证冻结归档或归档解出的干净发行目录。

## 运行与修改之前

树莓派图形应用还依赖 PyQt5、Pillow、Picamera2/libcamera、gpiod 和匹配的 Linux 桌面；Mac／Windows 的文档检查通过不意味着能运行相机预览。不要为了阅读源码先在电脑安装这些硬件依赖。

现有功能包括拍照、比例与设置保存、图库、L1 拉杆、已有按钮电源服务接口、同 Wi-Fi 手机传图及启动画面。无电量采样时不显示估算百分比；HDR、美颜等预留项和未验收事项以原 feature-status.md 为准。

应用 tests/ 保留了演进过程中的原测试源码。部分测试依赖旧版交付目录、特定夹具、Qt 或 Linux；不能把整目录 unittest discover 当作这份独立参考包的通用验收命令。当前独立核对入口是根目录 tools/check_reference.py。

pi-camera-1.0/verification.json 是原树中继承的旧 0.4.0 记录，保留便于追溯，不能作为 1.0 或读者设备的验收证明。本次发布只新增离线完整性检查，没有新做真实相机功能或开机验收。

权限兼容修复的背景见 [说明](reference-mac-entries/权限兼容修复说明.md)。原冻结实现未改；Mac 支撑层已包含限定范围的 umask／权限修复，不采用递归 chmod 或覆盖未知目录的方式。

自有代码用 MIT；第三方和素材许可见 [许可范围](../LICENSES/README.md)。
