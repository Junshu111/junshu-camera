# Junshu 自制相机教程

先阅读 README.md 和 GETTING-STARTED.md。默认先完成当前电脑上的文档环境核对，不推断用户已授权连接或写入树莓派。

网页是静态 HTML，普通阅读无需安装依赖。生成器只使用 Python 3 标准库。优先运行 `tools/check_site.py` 做只读检查，不因缺少 Node.js 安装一套无关工具。

正文来源是 content/hardware.json、software.json、structure.json；tables.json 为核对表。修改正文后运行 tools/build_site.py 更新 docs。不要把数字几何检查、本地软件测试写成实机通过。

当前 PDF 由 Junshu 单独审阅，未经她后续确认，不放入此仓库或网页下载入口。频道视频制作资料始终不在本仓库范围内。

硬件、系统、材料与接线不同就重新核对。删除用户文件、重写存储卡、修改启动或实际连接设备前，先确认该具体动作确由用户要求。
