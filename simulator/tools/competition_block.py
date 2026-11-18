"""赛事图2：底径56、顶径36、底座8、上颈10、总高70mm。"""
import math


def surface(rings, segments=64):
    vertices = [(radius*math.cos(2*math.pi*i/segments), radius*math.sin(2*math.pi*i/segments), z)
                for radius, z in rings for i in range(segments)]
    faces = []
    for ring in range(len(rings)-1):
        for i in range(segments):
            a = ring*segments+i
            b = ring*segments+(i+1)%segments
            faces.append([a, b, b+segments, a+segments])
    faces += [list(reversed(range(segments))), list(range((len(rings)-1)*segments, len(rings)*segments))]
    points = ', '.join(' '.join(f'{v:.8g}' for v in p) for p in vertices)
    indices = ', '.join(' '.join(map(str, face))+' -1' for face in faces)
    return f'IndexedFaceSet {{ coord Coordinate {{ point [ {points} ] }} coordIndex [ {indices} ] creaseAngle 0.5 }}'


def block_node(x, y, mass):
    visual = surface([(.028, -.035), (.028, -.027), (.018, .025), (.018, .035)])
    # 三段相邻的碰撞体分别保留底座、锥台和颈部尺寸。
    slope = surface([(.028, -.027), (.018, .025)],32)
    collision = f'''Group {{ children [
      Pose {{ translation 0 0 -0.031 children [ Cylinder {{ radius 0.028 height 0.008 subdivision 32 }} ] }}
      {slope}
      Pose {{ translation 0 0 0.03 children [ Cylinder {{ radius 0.018 height 0.01 subdivision 32 }} ] }}
    ] }}'''
    return f'''DEF BLOCK Solid {{ name "block" translation {x} {y} 0.035
contactMaterial "competition_block"
children [ Shape {{ appearance DEF BLOCK_MATERIAL PBRAppearance {{ baseColor 0.9 0.05 0.08 roughness 0.7 metalness 0 }} geometry {visual} }} ]
boundingObject {collision}
physics Physics {{ density -1 mass {mass:.7g} }}
}}'''
