# 依赖与参考

- [Webots R2025a](https://github.com/cyberbotics/webots/releases/tag/R2025a)：外部运行时，主体采用 Apache 2.0；安装包与第三方组件遵循其各自许可证，仓库不重新打包。
- [OpenCV](https://opencv.org/) 与 [NumPy](https://numpy.org/)：由 Python 依赖安装。AprilTag 标签纹理由 OpenCV 的 `DICT_APRILTAG_36h11` 字典生成。
- [Pillow](https://python-pillow.org/)：仅用于场景资源生成。
- 接口依据：[DistanceSensor](https://github.com/cyberbotics/webots/blob/R2025a/docs/reference/distancesensor.md)、[Camera](https://github.com/cyberbotics/webots/blob/R2025a/docs/reference/camera.md)、[Supervisor](https://github.com/cyberbotics/webots/blob/R2025a/docs/reference/supervisor.md)。

场景、桥接控制器、面板与空白接口为该项目编写。
