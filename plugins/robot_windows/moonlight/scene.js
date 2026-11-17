export default {
  "floor_size": 2.0,
  "line_width": 0.0225,
  "wheel_radius": 0.027,
  "axle_length": 0.125,
  "body_size": [
    0.15,
    0.139
  ],
  "robot": {
    "name": "默认差速小车",
    "body": {
      "length_mm": 150,
      "width_mm": 105,
      "height_mm": 45,
      "clearance_mm": 9.5,
      "mass_kg": 0.6
    },
    "wheels": {
      "radius_mm": 27,
      "width_mm": 14,
      "track_mm": 125,
      "mass_kg": 0.06,
      "max_speed_m_s": 0.25,
      "max_torque_nm": 0.12,
      "response_s": 0.12
    },
    "caster": {
      "x_mm": -55,
      "radius_mm": 13
    },
    "camera": {
      "position_mm": [
        50,
        0,
        75
      ],
      "pitch_deg": 8.6,
      "yaw_deg": 0,
      "width": 320,
      "height": 240,
      "fov_deg": 71.6
    },
    "ir": {
      "positions_mm": [
        [
          62,
          36,
          -20
        ],
        [
          62,
          24,
          -20
        ],
        [
          62,
          12,
          -20
        ],
        [
          62,
          0,
          -20
        ],
        [
          62,
          -12,
          -20
        ],
        [
          62,
          -24,
          -20
        ],
        [
          62,
          -36,
          -20
        ]
      ]
    },
    "sonar": {
      "range_mm": 1500,
      "aperture_deg": 20,
      "sensors": [
        {
          "name": "front",
          "position_mm": [
            30,
            0,
            55
          ],
          "yaw_deg": 0
        },
        {
          "name": "left",
          "position_mm": [
            30,
            0,
            55
          ],
          "yaw_deg": 90
        },
        {
          "name": "right",
          "position_mm": [
            30,
            0,
            55
          ],
          "yaw_deg": -90
        }
      ]
    },
    "gripper": {
      "reach_mm": 120,
      "opening_mm": 56,
      "carry_height_mm": 85,
      "grab_distance_mm": 50
    }
  },
  "board": {
    "mcu": "STM32F103C8T6",
    "eda": "嘉立创EDA",
    "netlist": null,
    "mcu_reference": "",
    "bindings": {},
    "model": null,
    "position_mm": [
      0,
      0,
      35
    ]
  },
  "robot_origin_z": 0.032,
  "start": [
    -0.65475,
    -0.6885
  ],
  "finish": [
    -0.65475,
    0.6075
  ],
  "pickup": [
    0.52515,
    0.5751000000000001
  ],
  "marker": [
    0.34425,
    0.5751000000000001
  ],
  "tag": [
    0.7492500000000001,
    0.7398
  ],
  "circles": [
    {
      "side": "right",
      "color": "red",
      "center": [
        0.19425000000000003,
        0.8251000000000001
      ]
    },
    {
      "side": "right",
      "color": "yellow",
      "center": [
        0.34425,
        0.8251000000000001
      ]
    },
    {
      "side": "right",
      "color": "blue",
      "center": [
        0.49424999999999997,
        0.8251000000000001
      ]
    },
    {
      "side": "left",
      "color": "red",
      "center": [
        0.19425000000000003,
        0.32509999999999994
      ]
    },
    {
      "side": "left",
      "color": "yellow",
      "center": [
        0.34425,
        0.32509999999999994
      ]
    },
    {
      "side": "left",
      "color": "blue",
      "center": [
        0.49424999999999997,
        0.32509999999999994
      ]
    }
  ],
  "channel": [
    [
      -0.20115000000000002,
      -0.162
    ],
    [
      0.22005000000000002,
      -0.4833
    ]
  ],
  "tag_family": "tag36h11",
  "tag_size": 0.08,
  "block": {
    "base_diameter_mm": 56,
    "top_diameter_mm": 36,
    "height_mm": 70,
    "base_height_mm": 8,
    "neck_height_mm": 10,
    "mass_kg": 0.04
  },
  "geometry_note": "教学近似布局；以正式 DWG 更新坐标，标签/挡板布置为练习配置"
};
