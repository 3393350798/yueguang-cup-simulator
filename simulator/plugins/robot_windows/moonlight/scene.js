export default {
  "step_ms": 32,
  "physics_step_ms": 4,
  "field_revision": "rules-2000",
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
    "step_ms": 32,
    "vision_link_port": 0,
    "imperfections": {
      "seed": 0,
      "motor_gain": [
        1,
        1
      ],
      "motor_deadzone": [
        0,
        0
      ],
      "ir_noise": 0,
      "sonar_noise_mm": 0,
      "light_gain": 1,
      "uart_delay_ms": 0,
      "uart_loss": 0
    },
    "body": {
      "length_mm": 150,
      "width_mm": 105,
      "height_mm": 45,
      "clearance_mm": 9.5,
      "mass_kg": 0.6,
      "center_of_mass_mm": [
        -18.35,
        0,
        0
      ]
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
        20,
        0,
        138
      ],
      "pitch_deg": 22,
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
      "kind": "parametric",
      "reach_mm": 120,
      "opening_mm": 56,
      "closed_gap_mm": 24,
      "finger_length_mm": 40,
      "finger_thickness_mm": 7,
      "finger_height_mm": 9,
      "jaw_center_height_mm": 70,
      "lift_travel_mm": 45,
      "jaw_speed_mm_s": 26,
      "lift_speed_mm_s": 60,
      "jaw_force_n": 2,
      "lift_force_n": 6,
      "finger_mass_kg": 0.014,
      "carriage_mass_kg": 0.045,
      "friction": 0.85
    },
    "block": {
      "mass_kg": 0.04,
      "floor_friction": 0.5
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
  "floor_z": -0.005,
  "gripper": {
    "reach_mm": 120.0,
    "opening_mm": 56.0,
    "closed_gap_mm": 24.0,
    "finger_length_mm": 40.0,
    "finger_thickness_mm": 7.0,
    "finger_height_mm": 9.0,
    "jaw_center_height_mm": 70.0,
    "lift_travel_mm": 45.0,
    "jaw_speed_mm_s": 26.0,
    "lift_speed_mm_s": 60.0,
    "jaw_force_n": 2.0,
    "lift_force_n": 6.0,
    "finger_mass_kg": 0.014,
    "carriage_mass_kg": 0.045,
    "friction": 0.85,
    "kind": "parametric",
    "jaws": [
      {
        "joint": "gripper_left",
        "open": 0,
        "closed": 0.016
      },
      {
        "joint": "gripper_right",
        "open": 0,
        "closed": 0.016
      }
    ],
    "lift": {
      "joint": "gripper_lift",
      "lower": 0,
      "raise": 0.045
    },
    "contact_links": [
      "gripper_left",
      "gripper_right"
    ],
    "contact_defs": [
      "GRIPPER_LEFT",
      "GRIPPER_RIGHT"
    ]
  },
  "start": [
    -0.796911,
    -0.8182775
  ],
  "finish": [
    -0.796911,
    0.712615
  ],
  "pickup": [
    0.5594970000000001,
    0.6817225
  ],
  "marker": [
    0.35940000000000016,
    0.6817225
  ],
  "tag": [
    0.8592679999999999,
    0.8716539999999999
  ],
  "finish_inner_size": [
    0.205,
    0.205
  ],
  "circles": [
    {
      "side": "right",
      "color": "red",
      "center": [
        0.20940000000000003,
        0.9317225
      ],
      "inner_radius": 0.02125
    },
    {
      "side": "right",
      "color": "yellow",
      "center": [
        0.35940000000000016,
        0.9317225
      ],
      "inner_radius": 0.02125
    },
    {
      "side": "right",
      "color": "blue",
      "center": [
        0.5094000000000001,
        0.9317225
      ],
      "inner_radius": 0.02125
    },
    {
      "side": "left",
      "color": "red",
      "center": [
        0.20940000000000003,
        0.4317225
      ],
      "inner_radius": 0.02125
    },
    {
      "side": "left",
      "color": "yellow",
      "center": [
        0.35940000000000016,
        0.4317225
      ],
      "inner_radius": 0.02125
    },
    {
      "side": "left",
      "color": "blue",
      "center": [
        0.5094000000000001,
        0.4317225
      ],
      "inner_radius": 0.02125
    }
  ],
  "channel": [
    [
      -0.266018,
      -0.19370699999999996
    ],
    [
      0.2339819999999999,
      -0.5687070000000001
    ]
  ],
  "walls": [
    [
      -0.266018,
      -0.19370699999999996,
      0.2339819999999999,
      -0.19370699999999996
    ],
    [
      -0.266018,
      -0.5687070000000001,
      0.2339819999999999,
      -0.5687070000000001
    ]
  ],
  "wall_thickness": 0.012,
  "wall_height": 0.14,
  "tag_family": "tag36h11",
  "tag_size": 0.08,
  "block": {
    "base_diameter_mm": 56,
    "top_diameter_mm": 36,
    "height_mm": 70,
    "base_height_mm": 8,
    "neck_height_mm": 10,
    "mass_kg": 0.04,
    "floor_friction": 0.5
  },
  "geometry_note": "2000 × 2000 mm 赛规定尺版；挡板沿超声波区黑框长边全长布置"
};
