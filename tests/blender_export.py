import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading

import bpy

repo = Path(__file__).resolve().parents[1]
workspace = tempfile.TemporaryDirectory()
work = Path(workspace.name)
spec = importlib.util.spec_from_file_location('sketchfab_audit', repo / '__init__.py')
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)

def prepare(selection):
    from sketchfab_audit.transfers import pack_scene

    directory = Path(tempfile.mkdtemp(dir=work))
    command = addon.save_upload_scene(directory, selection)
    return pack_scene(command, directory, threading.Event())

def read_glb(path):
    data = Path(path).read_bytes()
    assert data[:4] == b'glTF'
    size = struct.unpack_from('<I', data, 12)[0]
    return json.loads(data[20:20 + size])

def print_probe(label, details):
    print('AUDIT ' + label + ' ' + json.dumps(details), flush=True)

cube = bpy.data.objects['Cube']
if bpy.app.version >= (4, 0, 0):
    bpy.context.scene.name = 'CurrentScene'
    cube.name = 'CurrentSceneCube'
    secondary = bpy.data.scenes.new('SecondaryScene')
    secondary_cube = bpy.data.objects.new('SecondarySceneCube', cube.data.copy())
    secondary.collection.objects.link(secondary_cube)
    prepared = prepare(False)
    glb = read_glb(prepared['filepath'])
    print_probe('inactive-scene', {'scenes': [scene.get('name') for scene in glb['scenes']],
                                   'nodes': [node.get('name') for node in glb['nodes']]})
    assert all(node.get('name') != 'SecondarySceneCube' for node in glb['nodes'])

    bpy.data.scenes.remove(secondary)
    bpy.data.objects.remove(secondary_cube, do_unlink=True)
    rig = bpy.data.objects.new('SharedRig', bpy.data.armatures.new('SharedRig'))
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = rig.data.edit_bones.new('RootBone')
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    second = bpy.data.objects.new('MorphCube', cube.data.copy())
    bpy.context.scene.collection.objects.link(second)
    second.location.x = 4
    for mesh in (cube, second):
        mesh.parent = rig
        group = mesh.vertex_groups.new(name='RootBone')
        group.add(list(range(len(mesh.data.vertices))), 1.0, 'REPLACE')
        modifier = mesh.modifiers.new(name='Rig', type='ARMATURE')
        modifier.object = rig
    second.shape_key_add(name='Basis')
    shape = second.shape_key_add(name='Change')
    shape.data[0].co.z += 1
    bpy.ops.export_scene.gltf(filepath=str(work / 'native.glb'), export_format='GLB',
                              use_visible=True, export_apply=False)
    native = read_glb(work / 'native.glb')
    split = read_glb(prepare(False)['filepath'])
    def rig_info(doc):
        return {'joint_sets': [skin['joints'] for skin in doc.get('skins', [])],
                'node_names': [node.get('name') for node in doc.get('nodes', [])],
                'morph_targets': sum(len(part.get('targets', [])) for mesh in doc.get('meshes', [])
                                     for part in mesh['primitives'])}
    print_probe('shared-armature', {'native': rig_info(native), 'split': rig_info(split)})

    rig.select_set(False)
    rig.hide_set(True)
    cube.select_set(True)
    second.select_set(True)
    for doc in (split, read_glb(prepare(True)['filepath'])):
        skins = {node['name']: node.get('skin') for node in doc['nodes'] if 'mesh' in node}
        assert skins.keys() == {cube.name, second.name}, skins
        assert skins[cube.name] is not None and skins[cube.name] == skins[second.name], skins
        assert rig_info(doc)['morph_targets'] == 1
    bpy.data.objects.remove(cube, do_unlink=True)
    second.modifiers.new(name='Subdivision', type='SUBSURF')
    shape_only = prepare(False)
    single = read_glb(shape_only['filepath'])
    assert shape_only['warnings'] and second.name in shape_only['warnings'][0]
    print_probe('shape-only-warnings', {'warnings': shape_only['warnings'],
                                        'morph_targets': rig_info(single)['morph_targets']})

    second.parent = None
    second.modifiers.clear()
    bpy.data.objects.remove(rig, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add()
    baked = bpy.context.object
    baked.name = 'BakedCube'
    bpy.ops.mesh.primitive_cube_add(location=(0.5, 0.5, 0.5))
    cutter = bpy.context.object
    cutter.hide_set(True)
    modifier = baked.modifiers.new(name='Cut', type='BOOLEAN')
    modifier.object = cutter
    doc = read_glb(prepare(False)['filepath'])
    mesh_by_name = {node['name']: doc['meshes'][node['mesh']] for node in doc['nodes'] if 'mesh' in node}
    assert mesh_by_name.keys() == {baked.name, second.name}
    primitive = mesh_by_name[baked.name]['primitives'][0]
    assert doc['accessors'][primitive['indices']]['count'] > 36
    assert mesh_by_name[second.name]['primitives'][0]['targets']

else:
    image = bpy.data.images.new('ExternalImage', width=8, height=8)
    source = work / 'external.png'
    image.filepath_raw, image.file_format = str(source), 'PNG'
    image.save()
    bpy.data.images.remove(image)
    image = bpy.data.images.load(str(source))
    material = bpy.data.materials.new('ImageMaterial')
    material.use_nodes = True
    texture = material.node_tree.nodes.new('ShaderNodeTexImage')
    texture.image = image
    shader = material.node_tree.nodes.get('Principled BSDF')
    material.node_tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
    cube.data.materials.append(material)
    curve = bpy.data.curves.new('UnselectedCurve', 'CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = 0.1
    spline = curve.splines.new('POLY')
    spline.points.add(1)
    spline.points[0].co = (0, 0, 0, 1)
    spline.points[1].co = (1, 1, 1, 1)
    bpy.context.scene.collection.objects.link(bpy.data.objects.new('UnselectedCurve', curve))

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1)
    source_object = bpy.context.object
    source_object.name = 'GeometrySource'
    source_object.select_set(False)
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube

    source_group = bpy.data.node_groups.new('Source Geometry', 'GeometryNodeTree')
    source_group.outputs.new('NodeSocketGeometry', 'Geometry')
    object_info = source_group.nodes.new('GeometryNodeObjectInfo')
    object_info.inputs['Object'].default_value = source_object
    output = source_group.nodes.new('NodeGroupOutput')
    source_group.links.new(object_info.outputs['Geometry'], output.inputs['Geometry'])
    geometry_group = bpy.data.node_groups.new('Export Geometry', 'GeometryNodeTree')
    geometry_group.outputs.new('NodeSocketGeometry', 'Geometry')
    nested = geometry_group.nodes.new('GeometryNodeGroup')
    nested.node_tree = source_group
    output = geometry_group.nodes.new('NodeGroupOutput')
    geometry_group.links.new(nested.outputs['Geometry'], output.inputs['Geometry'])
    cube.modifiers.new('Geometry Nodes', 'NODES').node_group = geometry_group

    all_visible = prepare(False)
    selection_only = prepare(True)
    for title, prepared in (('all-visible', all_visible), ('selection-only', selection_only)):
        bpy.ops.wm.open_mainfile(filepath=prepared['filepath'])
        texture_states = {image.name: bool(image.packed_file) for image in bpy.data.images if image.source == 'FILE'}
        assert any(texture_states.values()), texture_states
        assert ('UnselectedCurve' in bpy.data.objects) == (title == 'all-visible')
        exported_cube = bpy.data.objects['Cube'].evaluated_get(bpy.context.evaluated_depsgraph_get())
        assert len(exported_cube.data.vertices) == 12
        if title == 'selection-only':
            assert bpy.data.objects['GeometrySource'].hide_render
        print_probe(title, {'textures_packed': texture_states, 'objects': [obj.name for obj in bpy.data.objects]})
print_probe('complete', list(bpy.app.version))
workspace.cleanup()
