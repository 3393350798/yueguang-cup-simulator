# 月光杯 · 仿真练习台

用可配置的虚拟差速小车练习月光杯赛题。在车体组装前，可以获取摄像头、红外、超声波和编码器数据，编写自己的视觉与控制程序。公开版提供 Webots 场地、车模、传感器和空白程序接口。

## 第一次运行

1. 安装 [Webots R2025a](https://github.com/cyberbotics/webots/releases/tag/R2025a) 和 [Python 3.11～3.13](https://www.python.org/downloads/)。安装 Python 时选择加入 PATH。
2. 解压 [v0.16.0 ZIP](https://github.com/3393350798/yueguang-cup-simulator/releases/download/v0.16.0/yueguang-cup-simulator-v0.16.0.zip)，双击 `launcher.bat`，选择「排障与接入 → 安装项目依赖」，然后返回首页选择「开始练习」。
3. 在网页的「练习」里选「手动探索」。按住方向按钮驾驶，松开停车。也可在 Webots 窗口按 W/A/S/D 驾驶、E 切换夹爪开合、Q 切换升降、空格停车。
4. 选好组号、物块颜色和赛段后点击「摆位」，即可从全程起点、抓放、超声波通道或停车入口开始。「保存原图」会把完整分辨率的 PNG 写入项目的 `simulator/output/`。

启动前关闭旧的同项目 Webots 窗口，运行时请同时保持 Webots 和网页打开。依赖装好后也可用 `start.bat` 直接启动。浏览器没有自动打开时，可访问启动窗口显示的本机地址。若 Webots 安装在自定义目录，可设置 `WEBOTS_HOME`。启动遇到问题，可从「排障与接入」运行自检或导出诊断。[启动自检](docs/启动自检.md)

## 练习、对照和求助

| 你要做什么 | 从哪里开始 |
| --- | --- |
| 运行自己的 Python 程序 | 编辑 `simulator/controllers/moonlight/member_algorithm.py` 中的 `vision()` 和 `step()`。在「练习」选择「会员算法」，点击「重载算法」和「开始运行」。[算法接口](docs/算法接口.md) |
| 用自己的车模或固件 | 在 `launcher.bat` 的「配置小车」中调整尺寸与传感器，或导入 URDF 装配体、STEP/STL/OBJ 几何和嘉立创 EDA 网表。STM32F103C8T6 固件由 Renode 执行，外设接线和支持范围见说明。[自定义小车](docs/自定义小车.md) · [导入硬件](docs/导入自己的硬件.md) · [固件执行](docs/固件执行.md) |
| 批量验证自己的程序 | 在「批量练习」选好控制方式、赛段与每轮时长，然后选择要跑的工况。同条件多轮适合看稳定性，固定六轮覆盖组号奇偶 × 红黄蓝。需要测异常时，可给固定工况设置定时故障，或另跑赛场容差。[分赛段练习](docs/分赛段练习.md) · [故障工况](docs/故障工况回归.md) · [赛场容差](docs/赛场容差回归.md) |
| 查某一轮，或改完程序后再跑 | 到「练习结果」找该轮，查看失败片段或按原条件复跑。想比较两批结果，用「导出结果」保存旧报告，再用「导入旧报告对照」载入。[本机练习历史](docs/分赛段练习.md#本机练习历史) · [失败回放](docs/失败回放.md) |
| 联系负责人排查 | 从「练习结果」的对应轮次导出求助包，也可附上该轮失败片段。网页打不开时，到启动器的「排障与接入 → 导出启动与运行诊断」打包日志。[本轮求助包](docs/求助包.md) · [故障诊断](docs/故障诊断.md) |
| 备份、共享或升级队伍工程 | 在 `launcher.bat` 的「队伍工程」中打包、恢复或迁移。升级时先解压新版，再从新版入口把队伍配置和自编程序迁入独立目录。[队伍项目](docs/队伍项目.md) · [项目升级](docs/项目升级.md) |

## 需要更深入的联调时

- 检查机械装配、左右轮、传感器视野和夹爪动作，可使用装配体检或夹爪测试台。[装配体检](docs/装配体检.md) · [夹爪测试台](docs/夹爪测试台.md)
- 在网页「高级联调」查看波形和串口。也可以用同一组传感器输入、图像或串口记录检查前后两版程序。[调试与联调](docs/调试与联调.md) · [固件测试台](docs/固件测试台.md) · [视觉单帧](docs/视觉样本回放.md) · [视觉连续帧](docs/视觉连续帧回放.md) · [视觉固件联测](docs/视觉固件联测.md)
- 仿真程序接到实车前，按队伍方案检查 OpenMV，或手机串流、电脑视觉与无线串口。用实测数据标定，再将实车记录和仿真波形对齐。[实车串口自检](docs/实车串口自检.md) · [标定与实车迁移](docs/标定与实车迁移.md) · [实车录制与联调](docs/实车录制与联调.md) · [实车波形对照](docs/实车波形对照.md) · [真机验收](docs/真机验收.md)

## 场景与模型

![2000 × 2000 mm 赛规定尺版地图](simulator/worlds/textures/track.png)

- 场地采用 2000 × 2000 mm「赛规定尺版」地图。黑线宽 22.5 mm，抓取框外边长 80 mm，起终点外框 250 mm、内框 205 mm。超声波区没有贯穿循迹线，两侧实体挡板各长 500 mm，与黑线边框等长。
- 组号偶数对应 AprilTag ID 0 与左侧目标（图中下排），奇数对应 ID 1 与右侧目标（图中上排）。每轮摆放一个红、黄、蓝中的物块，按赛题尺寸建模，底径 56 mm、顶径 36 mm、全高 70 mm。夹爪通过接触、摩擦和升降完成抓取，物块可能滑落或翻倒。[物块摄像头视图](docs/images/block.png)
- 默认差速车底盘长 150 mm，含轮宽约 139 mm，轮径 54 mm，轮距 125 mm。默认摄像头分辨率为 320 × 240，另有七路红外和前、左、右超声波。停车、放置与通道的页面判定用于练习，正式计分以赛题为准。实体 STM32、OpenMV、手机串流和无线串口请按队伍实际配置验收。

<details>
<summary>命令行与工程目录</summary>

Windows 可在命令行指定 Webots 路径：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/launch.ps1 -WebotsHome "D:\Webots"
```

Linux/macOS 可用下列入口，需确认 `webots` 在 PATH 上，并有可用的 OpenGL 图形环境。对应系统上的运行仍待验证。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements/core.txt
sh start.sh
```

Windows 的 `setup.bat`、`start.bat` 和 `scripts/` 下的单项工具仍可单独运行。首次导入 STEP 或执行 STM32 固件时，按需到「排障与接入 → 安装可选组件」安装转换组件或 Renode。

小车、板卡和固件的配置分别为 `simulator/robot.json`、`simulator/board.json`、`simulator/firmware.json`。场地坐标集中在 `simulator/assets/competition_field.json`。改了车模、安装位置或接线后，重启 Webots 以加载新配置。

</details>

代码与原创教学资源按 MIT 发布。[依赖与参考](docs/THIRD_PARTY.md)
