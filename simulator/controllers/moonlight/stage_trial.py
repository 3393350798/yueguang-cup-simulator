"""Measure practice runs without providing competition control logic."""
from math import pi

STAGES = {'full':'全程', 'pickup':'识别与抓放', 'channel':'超声波通道', 'parking':'终点停车'}


def starting_pose(config, stage):
    if stage == 'pickup':
        x,y = config['pickup']
        reach = config['gripper'].get('reach_mm', 0)/1000
        return [x+max(reach,config['body_size'][0]/2)+.09, y, pi]
    if stage == 'channel':
        a,b = config['channel']
        return [b[0]+config['body_size'][0]/2+.02, (a[1]+b[1])/2, pi]
    if stage == 'parking':
        x,y = config['finish']
        return [x, y-.25, pi/2]
    return [*config['start'],0]


class StageTrial:
    def __init__(self, stage, config):
        self.stage = stage
        self.config = config
        self.start = starting_pose(config,stage)
        self.crossed = False
        self.entered = False
        self.previous_x = self.start[0]
        self.wall_hits = 0
        self.touching_wall = False
        self.sonar_triggers = 0
        self.falls = 0
        self.holding = False
        self.max_clearance = 0.0

    def update(self, pose, grip, wall_contact, sonar_triggers):
        x,y,_ = pose
        a,b = self.config['channel']
        in_width = b[1] <= y <= a[1]
        inside = a[0] <= x <= b[0] and in_width
        if inside and self.previous_x > b[0]:
            self.entered = True
        if not inside:
            if self.entered and x < a[0] and in_width:
                self.crossed = True
            self.entered = False
        self.previous_x = x
        if not self.touching_wall and wall_contact:
            self.wall_hits += 1
        self.touching_wall = wall_contact
        if self.holding and not grip['holding'] and grip['closing'] and grip['raising']:
            self.falls += 1
        self.holding = grip['holding']
        if grip['holding']:
            self.max_clearance = max(self.max_clearance, grip['clearance_mm'])
        self.sonar_triggers = max(self.sonar_triggers,sonar_triggers)

    def report(self, state, pose):
        yaw = pose[2]
        alignment = abs(yaw-round(yaw/(pi/2))*(pi/2))
        return dict(stage=self.stage,mode=state['mode'],group=state['group'],color=state['color'],
                    elapsed=round(state['elapsed'],3),start=self.start,step_ms=self.config['step_ms'],
                    environment=self.config['robot'].get('imperfections',{}),
                    calibration=self.config['robot'].get('calibration',{}),
                    grasped=state['grasped'], placed=state['placed'], falls=self.falls,
                    max_clearance_mm=self.max_clearance, crossed=self.crossed, wall_hits=self.wall_hits,
                    sonar_triggers=self.sonar_triggers, parked=state['parked'], aligned=state['aligned'],
                    parking_angle_deg=round(alignment*180/pi,1) if state['parked'] else None)
