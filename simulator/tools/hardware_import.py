"""会员本地机械模型和嘉立创EDA网表导入。"""
from pathlib import Path
import argparse
import json
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


def build_urdf_model(model):
    from urdf2webots.importer import convertUrdfContent
    source = ROOT/model['source']
    doc = ET.parse(source)
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
