# 依赖与参考

- [Webots R2025a](https://github.com/cyberbotics/webots/releases/tag/R2025a)：外部运行时，主体采用 Apache 2.0；安装包与第三方组件遵循其各自许可证，仓库不重新打包。
- [OpenCV](https://opencv.org/) 与 [NumPy](https://numpy.org/)：由 Python 依赖安装。AprilTag 标签纹理由 OpenCV 的 `DICT_APRILTAG_36h11` 字典生成。
- [Pillow](https://python-pillow.org/)：仅用于场景资源生成。
- [urdf2webots](https://github.com/cyberbotics/urdf2webots)：Apache 2.0，使用Python依赖将会员URDF转换为Webots PROTO；生成模型保留转换器的许可标记。
- [trimesh](https://github.com/mikedh/trimesh)：MIT，用于几何网格转换；[pycollada](https://github.com/pycollada/pycollada)：BSD，用于读取DAE网格。
- [cadquery-ocp](https://github.com/CadQuery/OCP)：可选STEP转换依赖，基于Open CASCADE；遵循其LGPL 2.1及附带例外等许可，依赖通过pip安装，仓库不重新打包其二进制组件。
- [Renode 1.17.0](https://github.com/renode/renode/releases/tag/v1.17.0)：可选固件执行后端，主体MIT，依赖遵循各自许可证；安装脚本从官方获取运行时。`tools/firmware/f103.repl`参考其F103平台，定时器扩展继承其STM32模型，Antmicro许可保留在 [RENODE-LICENSE.txt](tools/firmware/RENODE-LICENSE.txt)。
- 接口依据：[DistanceSensor](https://github.com/cyberbotics/webots/blob/R2025a/docs/reference/distancesensor.md)、[Camera](https://github.com/cyberbotics/webots/blob/R2025a/docs/reference/camera.md)、[Supervisor](https://github.com/cyberbotics/webots/blob/R2025a/docs/reference/supervisor.md)。

场景、桥接控制器、面板与空白接口为该项目编写。
