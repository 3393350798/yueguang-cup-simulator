"""会员练习入口。保存后点击面板的“重载算法”即可使用。"""

class Algorithm:
    def vision(self, image_bgr):
        """图像为 高×宽×3 的 BGR numpy 数组，尺寸由 robot.json 决定。"""
        return {}

    def step(self, sensors, dt):
        """返回左右轮线速度（m/s）与 gripper，可取 'grab'/'release'。

        sensors:
          ir: 按 robot.json 中的位置列表顺序排列，黑线接近 0，白地接近 1
          sonar: 按 robot.json 中的传感器名称索引，距离单位 m
          encoder: left/right 轮角，单位 rad
          vision: 上面 vision() 返回的结果
          carrying: 是否夹持物块

        初始模板让小车停在原地。请自行实现控制与视觉算法。
        """
        return {'left': 0.0, 'right': 0.0}

