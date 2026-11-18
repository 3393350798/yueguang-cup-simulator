"""Set joint targets and observe the block's physical contacts."""
import math


class PhysicalGripper:
    def __init__(self, config, motors, sim, car, block, floor_z, dt):
        self.config = config
        self.motors = motors
        self.block = block
        self.floor_z = floor_z
        self.dt = dt
        defs = config.get('contact_defs', [name.upper() for name in config['contact_links']])
        self.pads = [(car.getFromProtoDef(name) if config['kind'] == 'urdf' else sim.getFromDef(name))
                     for name in defs]
        if not all(self.pads):
            raise ValueError('场景里找不到夹爪接触连杆，请重新生成场景。')
        for node in [block, *self.pads]:
            node.enableContactPointsTracking(round(dt * 1000))
        self.reset()

    def reset(self):
        self.closing = False
        self.raised = False
        self.holding = False
        self.contact_time = 0.0
        self.missing_time = 0.0
        self.command('release')
        self.command('lower')

    def command(self, action):
        if action in ('grab', 'release') and self.config['jaws']:
            self.closing = action == 'grab'
            target = 'closed' if self.closing else 'open'
            for jaw in self.config['jaws']:
                self.motors[jaw['joint']].setPosition(jaw[target])
        elif action in ('lift', 'lower') and self.config['lift']:
            self.raised = action == 'lift'
            lift = self.config['lift']
            self.motors[lift['joint']].setPosition(lift['raise' if self.raised else 'lower'])
        else:
            return False
        return True

    def observe(self):
        contacts = self.block.getContactPoints()
        position = self.block.getPosition()
        orientation = self.block.getOrientation()
        block_id = self.block.getId()
        block_points = [point.point for point in contacts]
        contact_ids = {point.node_id for point in contacts}
        # Some Webots contacts report the owning Solid ID. Match the physical
        # contact coordinates too, including contacts between PROTO children.
        def touches_block(pad):
            if pad.getId() in contact_ids:
                return True
            return any(point.node_id == block_id or
                       any(math.dist(point.point, other) < .0015 for other in block_points)
                       for point in pad.getContactPoints())
        touching = sum(touches_block(pad) for pad in self.pads)
        upright_z = orientation[8]
        tilt = math.hypot(orientation[6], orientation[7])
        lowest_z = position[2] + min(z*upright_z-radius*tilt for z,radius in
                                     [(-.035,.028),(-.027,.028),(.025,.018),(.035,.018)])
        clearance = lowest_z-self.floor_z
        airborne = clearance > .006
        if airborne and touching >= 2:
            self.contact_time += self.dt
            self.missing_time = 0
            if self.contact_time >= .096:
                self.holding = True
        else:
            self.contact_time = 0
            self.missing_time += self.dt
            if not airborne or self.missing_time >= .128:
                self.holding = False
        velocity = self.block.getVelocity()
        settled = (clearance <= .003 and any(point[2] < self.floor_z+.003 for point in block_points)
                   and math.hypot(*velocity[:3]) < .015 and math.hypot(*velocity[3:]) < .12)
        upright = upright_z >= math.cos(math.radians(15))
        return dict(touching=touching, holding=self.holding, airborne=airborne,
                    clearance_mm=round(clearance*1000,1),
                    raising=self.raised, closing=self.closing, settled=settled, upright=upright)
