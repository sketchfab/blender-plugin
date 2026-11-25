import importlib.util
from pathlib import Path
import sys
import tempfile

import bpy


repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sketchfab_import_test', repo / '__init__.py')
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)

cube = bpy.data.objects['Cube']
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'cube.glb'
    bpy.context.view_layer.objects.active = cube
    assert bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', use_selection=True) == {'FINISHED'}
    bpy.data.objects.remove(cube, do_unlink=True)
    addon.import_gltf(path, 'Imported Cube')
    cube = bpy.data.objects['Imported Cube']
    assert cube.type == 'MESH' and cube.parent is None and not cube.children
    assert 'sf_attributions' not in bpy.data.texts

root = bpy.data.objects.new('Root', None)
bpy.context.scene.collection.objects.link(root)
root.location.x = 3
cube.parent = root
bpy.context.view_layer.update()
world = cube.matrix_world.copy()
addon.Utils.clean_node_hierarchy([root, cube], 'Flattened Cube')
bpy.context.view_layer.update()
assert cube.parent is None and world == cube.matrix_world

root = bpy.data.objects.new('Scaled Root', None)
bpy.context.scene.collection.objects.link(root)
root.scale = (3, 1, 1)
cube.parent = root
cube.rotation_mode = 'XYZ'
cube.rotation_euler.z = 0.65
bpy.context.view_layer.update()
world = cube.matrix_world.copy()
addon.Utils.clean_node_hierarchy([root, cube], 'Preserved Scale')
bpy.context.view_layer.update()
assert cube.parent == root and cube.matrix_world == world
bpy.data.objects.remove(root, do_unlink=True)

root = bpy.data.objects.new('Animated Root', None)
bpy.context.scene.collection.objects.link(root)
cube.parent = root
root.keyframe_insert(data_path='location')
addon.Utils.clean_node_hierarchy([root, cube], 'Preserved Root')
assert cube.parent == root

uid = '0123456789abcdef0123456789abcdef'
addon.record_download({}, uid, 'First')
addon.record_download({}, uid, 'Second')
text = bpy.data.texts['sf_attributions'].as_string()
assert text.index('"First"') < text.index('"Second"')
print('Sketchfab import regression checks passed')
