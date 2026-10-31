# R4 0.4.0 同 Share 图标与开机启动

Mac [导入R4整合版.command](../导入R4整合版.command) 验证包后，使用可信 `tools/create_pi_next_launcher.py` 更新：

- `~/Desktop/Junshu-Camera-R4-Share.desktop`（实际 Desktop 路径先由 xdg-user-dir 核对）
- `~/.local/share/applications/junshu-camera-r4-share.desktop`

保持 `Name=Junshu Camera R4 Share`、既有 Adwaita camera-photo Icon、`#!/usr/bin/gio launch` 与 `Terminal=true`。只改变 Comment/Exec 中版本。缺失入口允许新建；已有入口仅接受精确 Share1/Share2/0.4.0可信字节，未知或符号链接两处共同拒绝。完整可信包核验与共享实例锁通过后才写；先保存原字节/模式/SHA，不强杀GUI、不增加编号图标。

独立开机入口由 [启用R4新版开机启动.command](../启用R4新版开机启动.command) 调用可信 Mac system helper，核对环境后在 XDG 或 labwc 选择一个支持路径。固定调用 `/bin/sh /home/junshu/junshu-camera-pi-0.4.0/start-autostart.sh`，`Terminal=false`，已有图形用户会话内系统 Python `-B`调用一次。日志 `~/.local/state/junshu-camera/autostart-r4.log`。退出或失败留桌面，没有循环、不改登录政策。

如已启用启动，回退先运行 [停用R4新版开机启动.command](../停用R4新版开机启动.command)，再触屏退出，并双击 [回退R4同图标到Share2.command](../回退R4同图标到Share2.command)。工具用 Mac 留存可信 Share2 archive/manifest 核对Pi已保留的 Share2 包，把同两处入口改回Share2；当前入口仍须是已知字节，备份保留。不会覆盖未知入口或改变Power/白灯配置。

完整步骤、GPIO22审批与安全断电顺序见 [安装与测试](../交付/20261026-R4整合版/安装与测试.md)。
