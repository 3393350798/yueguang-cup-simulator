"""会员本地机械模型和嘉立创EDA网表导入。"""
from pathlib import Path
import argparse
import json
import math
import re
import shlex
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def destination(prefix):
    folder = ROOT/'hardware'/'imported'/(prefix+'-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    return folder


def relative(path):
    return Path(path).relative_to(ROOT).as_posix()


def unpack(path, folder):
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            target = (folder/entry.filename).resolve()
            if not target.is_relative_to(folder.resolve()):
                raise ValueError('压缩包条目超出导入目录：'+entry.filename)
        archive.extractall(folder)


def mesh_file(uri, source):
    uri = uri.replace('\\', '/')
    if uri.startswith('package://'):
        rest = uri[len('package://'):]
        package, local = rest.split('/', 1)
        for parent in [source.parent, *source.parents]:
            for candidate in (parent/rest, parent/local if parent.name == package else parent/package/local):
                if candidate.is_file():
                    return candidate
    else:
        candidate = source.parent/uri.removeprefix('file://')
        if candidate.is_file():
            return candidate
    raise FileNotFoundError('找不到模型引用：'+uri+'；请将引用零件一起打包。')


def import_urdf(path):
    """将网格路径归一化并保留URDF的关节、质量、惯量和碰撞描述。"""
    path = Path(path)
    folder = destination('robot')
    if path.suffix.lower() == '.zip':
        unpack(path, folder/'source')
        files = list((folder/'source').rglob('*.urdf'))
        if len(files) != 1:
            raise ValueError('压缩包需包含一个 URDF；多个模型请解压后选择其中的 URDF。')
        path = files[0]
    doc = ET.parse(path)
    root = doc.getroot()
    for joint in root.findall('joint'):
        if joint.attrib['type'] not in ('fixed', 'continuous', 'revolute', 'prismatic'):
            raise ValueError('当前支持fixed/continuous/revolute/prismatic关节：'+joint.attrib['name'])
    copied = {}
    for mesh in root.iter('mesh'):
        source = mesh_file(mesh.attrib['filename'], path).resolve()
        if source not in copied:
            # 转成独立网格，避免OBJ/DAE材质引用依赖原电脑路径。
            import trimesh
            target = folder/f'mesh-{len(copied)}.stl'
            geometry = trimesh.load(source, force='scene').to_mesh()
            geometry.export(target)
            copied[source] = target.name
        mesh.set('filename', copied[source])
    for gazebo in list(root.findall('gazebo')):
        root.remove(gazebo)  # 使用robot.json配置的比赛传感器。
    doc.write(folder/'model.urdf', encoding='utf-8', xml_declaration=True)
    joints = [dict(name=j.attrib['name'], type=j.attrib['type'],
                   limits=dict(j.find('limit').attrib) if j.find('limit') is not None else {})
              for j in root.findall('joint') if j.attrib['type'] != 'fixed']
    links = [link.attrib['name'] for link in root.findall('link')]
    children = {j.find('child').attrib['link'] for j in root.findall('joint')}
    base = next(name for name in links if name not in children)
    result = dict(kind='urdf', source=relative(folder/'model.urdf'), base_link=base, joints=joints)
    write_json(folder/'assembly.json', result)
    return result


def import_geometry(path, unit='mm'):
    """STEP与网格转成以米为单位、包围盒中心为原点的独立STL。"""
    path = Path(path)
    folder = destination('mesh')
    if path.suffix.lower() in ('.step', '.stp'):
        try:
            from OCP.STEPControl import STEPControl_Reader
            from OCP.BRepMesh import BRepMesh_IncrementalMesh
            from OCP.StlAPI import StlAPI_Writer
            from OCP.IFSelect import IFSelect_RetDone
        except ImportError as error:
            raise RuntimeError('STEP导入需要先运行 scripts/setup-cad.bat 安装转换组件。') from error
        # OpenCascade文件接口使用英文中间路径。
        with tempfile.TemporaryDirectory(prefix='moonlight-step-') as temp:
            temporary = Path(temp)
            shutil.copy2(path, temporary/'model.step')
            reader = STEPControl_Reader()
            if reader.ReadFile(str(temporary/'model.step')) != IFSelect_RetDone:
                raise ValueError('无法读取STEP文件')
            reader.TransferRoots()
            shape = reader.OneShape()
            BRepMesh_IncrementalMesh(shape, 0.15)
            writer = StlAPI_Writer()
            writer.Write(shape, str(temporary/'model.stl'))
            shutil.copy2(temporary/'model.stl', folder/'original.stl')
        source = folder/'original.stl'
        unit = 'mm'  # STEP读取器将长度转换为mm。
    else:
        source = path
    import trimesh
    mesh = trimesh.load(source, force='scene').to_mesh()
    mesh.apply_scale({'mm': .001, 'm': 1, 'cm': .01}[unit])
    size = mesh.extents.tolist()
    mesh.apply_translation(-mesh.bounds.mean(axis=0))
    mesh.export(folder/'model.stl')
    result = dict(kind='mesh', source=relative(folder/'model.stl'), size_mm=[round(v*1000, 3) for v in size])
    write_json(folder/'geometry.json', result)
    return result


def import_netlist(path):
    """读取嘉立创EDA标准版/专业版导出的PADS网表。"""
    path = Path(path)
    raw = path.read_bytes()
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = raw.decode('gb18030')
    parts, nets = {}, {}
    section, current = '', None
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('!'):
            continue
        if line.startswith('*SIGNAL*'):
            current = line[len('*SIGNAL*'):].strip().strip('"')
            nets[current] = []
            section = 'nets'
        elif line.startswith('*PART*'):
            section = 'parts'
        elif line.startswith('*NET*'):
            section = 'nets'
        elif line.startswith('*'):
            section = ''
        elif section == 'parts':
            fields = shlex.split(line)
            if len(fields) >= 2:
                parts[fields[0]] = ' '.join(fields[1:])
        elif section == 'nets' and current:
            for node in line.split():
                if '.' in node:
                    ref, pin = node.strip('"').rsplit('.', 1)
                    nets[current].append(dict(ref=ref, pin=pin))
    if not nets:
        raise ValueError('没有读到PADS网表。请在嘉立创EDA中导出 PADS 9.5 网表。')
    folder = destination('board')
    result = dict(format='pads', components=parts, nets=nets)
    write_json(folder/'netlist.json', result)
    return dict(source=relative(folder/'netlist.json'), **result)


def urdf_gripper_elements(model, doc=None):
    if doc is None:
        doc = ET.parse(ROOT/model['source'])
    joints = {joint.get('name'): joint for joint in doc.findall('joint')}
    links = {link.get('name'): link for link in doc.findall('link')}
    collision_links = [
        name for name, link in links.items() if name != model['base_link']
        and any(geometry.tag in ('box', 'cylinder', 'sphere', 'mesh')
                for collision in link.findall('collision')
                for geometry in collision.findall('geometry/*'))
    ]
    return joints, links, collision_links


def gripper_joint_limits(joint, links):
    if joint.get('type') not in ('revolute', 'prismatic') or joint.find('mimic') is not None:
        raise ValueError('夹爪执行关节须为独立的 revolute 或 prismatic 关节：'+joint.get('name'))
    limit = joint.find('limit')
    values = tuple(float(limit.get(key, 'nan')) if limit is not None else math.nan
                   for key in ('lower', 'upper', 'effort', 'velocity'))
    lower, upper, effort, velocity = values
    if not all(map(math.isfinite, values)) or lower >= upper or min(effort, velocity) <= 0:
        raise ValueError('夹爪关节应在 URDF 中提供有效的 lower、upper、effort、velocity：'+joint.get('name'))
    child = links[joint.find('child').get('link')]
    mass = child.find('inertial/mass')
    if mass is None or not 0 < float(mass.get('value', 'nan')) < math.inf:
        raise ValueError('夹爪执行关节的子连杆须提供实际质量：'+child.get('name'))
    return lower, upper, effort, velocity


def validated_urdf_gripper(model, doc=None):
    """Resolve the member's targets against the assembly, retaining the original URDF units."""
    settings = model.get('gripper')
    if not settings:
        return dict(kind='none', jaws=[], lift=None, contact_links=[], contact_defs=[], reach_mm=0, friction=0.9)
    joints, links, collision_links = urdf_gripper_elements(model, doc)
    wheel_names = {model.get('left_joint'), model.get('right_joint')}
    actuated = set()

    def targets(spec, names):
        name = spec['joint']
        if name in actuated or name in wheel_names or name not in joints:
            raise ValueError('请选择不同于车轮且未重复的夹爪关节：'+name)
        lower, upper, _, _ = gripper_joint_limits(joints[name], links)
        first, second = (float(spec[key]) for key in names)
        if not lower <= first <= upper or not lower <= second <= upper or first == second:
            raise ValueError(f'{name} 的两个目标须不同且位于 URDF 行程 [{lower:g}, {upper:g}] 内。')
        actuated.add(name)
        return dict(zip(('joint', *names), (name, first, second)))

    jaws = [targets(jaw, ('open', 'closed')) for jaw in settings.get('jaws', [])]
    if not jaws:
        raise ValueError('请至少配置一个开合关节。')
    jaw_names = actuated.copy()
    lift_setting = settings.get('lift')
    lift = targets(lift_setting, ('lower', 'raise')) if lift_setting else None
    contacts = list(settings.get('contact_links', []))
    if len(set(contacts)) != len(contacts) or len(contacts) < 2 or any(name not in collision_links for name in contacts):
        raise ValueError('请选择至少两个不同且包含 URDF 碰撞几何的夹持面连杆。')
    joints_by_child = {joint.find('child').get('link'): joint for joint in joints.values()}
    for link in contacts:
        while link in joints_by_child:
            joint = joints_by_child[link]
            jaw_names.discard(joint.get('name'))
            link = joint.find('parent').get('link')
    if jaw_names:
        raise ValueError('每个开合关节须带动至少一个所选夹持面：'+', '.join(sorted(jaw_names)))
    friction = float(settings.get('friction', 0.9))
    if not 0 <= friction < math.inf:
        raise ValueError('夹持摩擦系数须为有限的非负数。')
    defs = [f'MOONLIGHT_GRIP_FINGER_{i}' for i in range(len(contacts))]
    return dict(kind='urdf', jaws=jaws, lift=lift, contact_links=contacts, contact_defs=defs, friction=friction, reach_mm=0)


def physical_gripper_proto(content, config, joints, links):
    """Keep the converter's geometry and add pad materials and physical stops."""
    pad_names = dict(zip(config['contact_links'], config['contact_defs']))
    # The converter emits the Solid name after its children. Match at
    # the Solid's field indentation to avoid naming a nested body.
    material = r'(?m)^( +)name "([^"]+)"\n(?= +boundingObject\b)'
    marks = {m[2]: m for m in re.finditer(material, content) if m[2] in pad_names}
    if marks.keys() != pad_names.keys():
        missing = next(iter(pad_names.keys()-marks.keys()))
        raise ValueError('导出模型中无法确认夹持面碰撞几何：'+missing)
    additions = []
    for name, match in marks.items():
        indent = ' '*(len(match[1])-2)
        solid = re.compile(rf'(?m)^{indent}(?:endPoint )?(Solid) \{{\n')
        openings = list(solid.finditer(content, endpos=match.start()))
        if not openings:
            raise ValueError('导出模型中无法确认夹持面碰撞几何：'+name)
        additions += [(openings[-1].start(1), 'DEF '+pad_names[name]+' '),
                      (match.end(), match[1]+'contactMaterial "grip_pad"\n')]
    for pos, text in sorted(additions, reverse=True):
        content = content[:pos]+text+content[pos:]

    for item in (*config['jaws'], *([config['lift']] if config['lift'] else [])):
        name = item['joint']
        joint = joints[name]
        lower, upper, _, _ = gripper_joint_limits(joint, links)
        joint_type = joint.get('type')
        pattern = (r'(?m)^( +)(jointParameters \w+ \{\n)((?:\1 +[^\n]*\n)*)'
                   r'\1\}\n(\1device \[\n\1  (?:Rotational|Linear)Motor \{\n'
                   rf'\1    name "{re.escape(name)}"\n(?:\1 +[^\n]*\n)*'
                   r'\1\]\n\1endPoint (?:DEF \w+ )?Solid \{\n)')
        match = re.search(pattern, content)
        if not match:
            raise ValueError('导出模型中无法确认夹爪执行器：'+name)
        indent, header, params, tail = match.groups()
        params += f'{indent}  minStop {lower:.10g}\n{indent}  maxStop {upper:.10g}\n'
        # urdf2webots incorrectly displaces non-X prismatic joints whose lower bound is > 0.
        displacement = None
        if joint_type == 'prismatic' and (lower > 0 or upper < 0):
            position = re.search(r'(?m)^ +position ([^\n]+)\n', params)
            initial = (lower+upper)/2
            axis_line = re.search(r'(?m)^ +axis ([^\n]+)\n', params)
            axis = list(map(float, axis_line[1].split())) if axis_line else [1., 0., 0.]
            axis_norm = math.sqrt(sum(component**2 for component in axis))
            axis = [component/axis_norm for component in axis]
            previous = float(position[1]) if position else 0
            displacement = [initial*component for component in axis]
            displacement[0] -= previous*sum(axis)
            line = f'{indent}  position {initial:.10g}\n'
            params = params[:position.start()]+line+params[position.end():] if position else line+params
        from_index = match.start()
        to_index = match.end()
        if displacement is not None:
            translation = re.match(r' +translation ([^\n]+)\n', content[to_index:])
            xyz = list(map(float, translation[1].split())) if translation else [0., 0., 0.]
            tail += indent+'  translation '+' '.join(f'{a+b:.10g}' for a,b in zip(xyz, displacement))+'\n'
            if translation:
                to_index += translation.end()
        content = content[:from_index]+indent+header+params+indent+'}\n'+tail+content[to_index:]
    return content


def build_urdf_model(model):
    from urdf2webots.importer import convertUrdfContent
    source = ROOT/model['source']
    doc = ET.parse(source)
    grip = validated_urdf_gripper(model, doc)
    for mesh in doc.iter('mesh'):
        mesh.set('filename', str((source.parent/mesh.attrib['filename']).resolve()).replace('\\', '/'))
    # 默认无上限的continuous关节也要具有可用的执行器参数。
    robot = read_json(ROOT/'robot.json')
    for joint in doc.findall('joint'):
        if joint.attrib['type'] in ('continuous', 'revolute'):
            limit = joint.find('limit')
            if limit is None:
                limit = ET.SubElement(joint, 'limit')
            limit.attrib.setdefault('effort', str(robot['wheels']['max_torque_nm']))
            limit.attrib.setdefault('velocity', str(robot['wheels']['max_speed_m_s']/(robot['wheels']['radius_mm']/1000)))
    output = ROOT/'protos'/'imported'/'MemberRobot.proto'
    output.parent.mkdir(parents=True, exist_ok=True)
    convertUrdfContent(ET.tostring(doc.getroot(), encoding='unicode'), output=str(output),
                       toolSlot=model['base_link'])
    content = output.read_text()
    if grip['kind'] == 'urdf':
        joints, links, _ = urdf_gripper_elements(model, doc)
        content = physical_gripper_proto(content, grip, joints, links)
    content = content.replace('field  SFString    controller ', 'field  SFString    window "moonlight"\n  field  SFString    controller ', 1)
    content = content.replace('controller IS controller', 'window IS window\n    controller IS controller', 1)
    output.write_text(content, encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['urdf', 'mesh', 'netlist'])
    parser.add_argument('file')
    parser.add_argument('--unit', default='mm', choices=['mm', 'cm', 'm'])
    args = parser.parse_args()
    result = {'urdf': import_urdf, 'mesh': lambda p: import_geometry(p, args.unit), 'netlist': import_netlist}[args.kind](args.file)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
