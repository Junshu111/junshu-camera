# 本次制作资料入口 · 2026-10-31

这份仓库公开本次自制相机的制作教程、AI协作流程、软件源码与原设备发行参考、结构和打印文件、需求记录及必要版本说明。观众可以学习和修改，按自己的硬件一步步核对与验证。

| 要找什么 | 从这里打开 |
| --- | --- |
| 离线中文制作手册 | [下载 PDF](docs/downloads/diy-camera-guide-zh.pdf)，含第二页两步 AI 入口 |
| 新手开始／两步提示词 | [START-HERE](START-HERE.md)／[网站入口](https://junshu111.github.io/junshu-camera/setup.html) |
| 新建或复用项目、七对话分工 | [AI-WORKFLOW](AI-WORKFLOW.md)／[启动交接](workflow/handoffs) |
| 中文制作教程、采购和接线核对 | [完整路线](https://junshu111.github.io/junshu-camera/route.html)／[硬件入口](hardware/README.md) |
| New Cam 1.0源码、启动主题与冻结归档 | [软件参考说明](software/README.md) |
| 原主壳、后续电池与充电口、拨杆选档、遮光斗和补件 | [模型与版本选择](models/README.md) |
| 原项目目标、问答与决策 | [需求参考](project-reference/requirements/README.md) |
| 允许怎样使用和修改 | [许可范围](LICENSES/README.md) |
| 各文件来自哪里、是否完整 | SOURCE-MANIFEST.json／FILE-MANIFEST.json／tools/check_reference.py |

## 建议顺序

先选项目和分工 → 读零件与目标 → 按型号列购买清单 → 官方系统和相机工具建立基础拍照 → 判断软件参考如何适配 → 软件和控件逐项调试 → 结构尺寸与样件试配 → 重新切片打印 → 安装与检验。

已有 Python 时，可在仓库根目录运行：

```sh
python3 -B tools/check_site.py
python3 -B tools/check_onboarding.py
python3 -B tools/check_reference.py
```

这些检查只针对文档、参考源码／归档和模型文件的完整性，不接设备、不装依赖、不打印，不代表已经完成相机实测。没有 Python 时直接阅读即可。Windows 根据已有安装也可使用 `py -3 -B`。

## 使用参考资料的边界

软件保留原账户、旧版本升级链和已知硬件检查；它是本次实现的源码，不是任意新设备的一键安装器。模型保留原几何，V31电池与G2充电口需按替换表选件，V30是待选档附件。原数字检查、用户使用反馈和读者自己的验收分开记录。

原设备的未验收项目仍如实说明。外观概念CAD未完成内部适配；没有把概念图、广告画面或网格检查冒充完整装机成功。3MF示例的材料／机器参数需要读者重新确认。

历史失败草稿、缓存、运行凭据、原始设备日志及频道视频制作素材与工程不属于本公开发行。中文离线 PDF 已经用户确认公开。原本地项目均保留，本次只整理公开副本。
