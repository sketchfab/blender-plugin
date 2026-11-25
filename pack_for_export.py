"""
Copyright 2021 Sketchfab

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import os
import bpy
import json
import sys
import time

SKETCHFAB_EXPORT_TEMP_DIR = sys.argv[sys.argv.index('--') + 1]
SKETCHFAB_EXPORT_DATA_FILE = os.path.join(SKETCHFAB_EXPORT_TEMP_DIR, "export-sketchfab.json")

# save a copy of the current blendfile
def save_blend_copy():
    filepath = SKETCHFAB_EXPORT_TEMP_DIR
    filename = time.strftime("Sketchfab_%Y_%m_%d_%H_%M_%S.blend",
                             time.localtime(time.time()))
    filepath = os.path.join(filepath, filename)
    bpy.ops.wm.save_as_mainfile(filepath=filepath,
                                compress=True,
                                copy=True)
    size = os.path.getsize(filepath)
    return (filepath, filename, size, [])

def is_object_hidden(ob):
    if bpy.app.version < (2, 80, 0):
        return ob.hide
    try:
        return not ob.visible_get()
    except RuntimeError:
        # excluded from the view layer -- not part of the export either way
        return True

def is_object_selected(ob):
    if bpy.app.version < (2, 80, 0):
        return ob.select
    try:
        return ob.select_get()
    except RuntimeError:
        return False

# objects that will actually be exported, matching use_visible/use_selection
def exported_objects(export_settings):
    for ob in bpy.context.view_layer.objects:
        if is_object_hidden(ob):
            continue
        if export_settings['selection'] and not is_object_selected(ob):
            continue
        yield ob

def select_only(objs):
    keep = set(objs)
    for ob in bpy.context.view_layer.objects:
        ob.select_set(ob in keep)

def has_bakeable_modifier(ob):
    return any(m.type != 'ARMATURE' for m in ob.modifiers)

def export_gltf(filepath, use_selection, export_apply):
    result = bpy.ops.export_scene.gltf(filepath=filepath, export_format='GLB',
                                      use_selection=use_selection, use_visible=True,
                                      use_active_scene=True, export_apply=export_apply)
    if 'FINISHED' not in result:
        raise RuntimeError('Blender cancelled the glTF export')

# export a GLB using Blender's own glTF exporter. Sketchfab's backend cannot
# process .blend files saved by Blender 4.x, so 4.x uploads go out as GLB instead.
def export_glb(export_settings):
    filename = time.strftime("Sketchfab_%Y_%m_%d_%H_%M_%S.glb", time.localtime(time.time()))
    filepath = os.path.join(SKETCHFAB_EXPORT_TEMP_DIR, filename)

    objects = list(exported_objects(export_settings))
    selected_only = export_settings['selection']
    armatures = {ref for ob in objects for ref in get_object_refs(ob) if ref.type == 'ARMATURE'}
    for rig in armatures.intersection(bpy.context.view_layer.objects[:]):
        if rig not in objects:
            rig.hide_viewport = False
            rig.hide_set(False)
            if selected_only:
                rig.hide_select = False
                rig.select_set(True)
            objects.append(rig)
    shape_key_objs = [ob for ob in objects if ob.type == 'MESH' and ob.data.shape_keys]
    other_objs = [ob for ob in objects if ob not in shape_key_objs and ob.type not in {'LIGHT', 'CAMERA'}]
    # Separate glTF passes cannot share a skin, hierarchy, or animation.
    can_split = shape_key_objs and other_objs and all(
        ob.type == 'MESH' and not (ob.parent or ob.animation_data or ob.constraints
                                   or ob.data.shape_keys and ob.data.shape_keys.animation_data)
        and all(mod.type != 'ARMATURE' for mod in ob.modifiers)
        for ob in shape_key_objs + other_objs
    )
    unbaked = [ob.name for ob in (shape_key_objs if can_split else objects)
               if has_bakeable_modifier(ob)] if shape_key_objs else []
    warnings = []
    if unbaked:
        warnings.append("Preserving shape keys left modifiers unapplied on " + ", ".join(unbaked))
    if not can_split:
        export_gltf(filepath, selected_only, not shape_key_objs)
        return (filepath, filename, os.path.getsize(filepath), warnings)

    baked_path = os.path.join(SKETCHFAB_EXPORT_TEMP_DIR, "sketchfab_baked.glb")
    keys_path = os.path.join(SKETCHFAB_EXPORT_TEMP_DIR, "sketchfab_keys.glb")
    select_only(other_objs)
    export_gltf(baked_path, True, True)
    select_only(shape_key_objs)
    export_gltf(keys_path, True, False)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    for part in (baked_path, keys_path):
        if 'FINISHED' not in bpy.ops.import_scene.gltf(filepath=part):
            raise RuntimeError('Blender could not combine the glTF exports')
    export_gltf(filepath, False, False)

    os.remove(baked_path)
    os.remove(keys_path)
    return (filepath, filename, os.path.getsize(filepath), warnings)

# objects that `ob` depends on: modifier/constraint targets and its parent,
# via a generic bl_rna walk so every modifier/constraint type is covered
def get_object_refs(ob):
    refs = set()
    for stack in (ob.modifiers, ob.constraints):
        for entry in stack:
            for prop in entry.bl_rna.properties:
                if prop.type != 'POINTER':
                    continue
                target = getattr(entry, prop.identifier, None)
                if isinstance(target, bpy.types.Object):
                    refs.add(target)
                elif isinstance(target, bpy.types.Collection):
                    refs.update(target.all_objects)
    if ob.parent is not None:
        refs.add(ob.parent)
    if ob.instance_collection is not None:
        refs.update(ob.instance_collection.all_objects)
    return refs

def prepare_assets(export_settings):
    visible = set(exported_objects(export_settings))
    needed = set(visible)
    visit = list(visible)
    while visit:
        dependencies = get_object_refs(visit.pop()) - needed
        needed.update(dependencies)
        visit.extend(dependencies)
    for scene in list(bpy.data.scenes):
        if scene != bpy.context.scene:
            bpy.data.scenes.remove(scene)
    instances = {item for ob in needed if ob.instance_collection for item in ob.instance_collection.all_objects}
    dependencies = (needed - visible - instances).intersection(bpy.context.view_layer.objects[:])
    for ob in dependencies:
        if ob.type != 'ARMATURE':
            ob.hide_set(True)
            ob.hide_render = True
    for ob in list(bpy.data.objects):
        if ob not in needed:
            bpy.data.objects.remove(ob, do_unlink=True)

    # delete unused materials and associated textures (will remove unneeded packed images)
    for m in bpy.data.meshes:
        if m.users == 0:
            bpy.data.meshes.remove(m)
    for m in bpy.data.materials:
        if m.users == 0:
            bpy.data.materials.remove(m)
    for t in bpy.data.images:
        if t.users == 0:
            bpy.data.images.remove(t)
    bpy.ops.file.pack_all()

def prepare_file(export_settings):
    # Sketchfab can't process Blender 4.x .blend files, so those go out as GLB
    if bpy.app.version >= (4, 0, 0):
        return export_glb(export_settings)

    prepare_assets(export_settings)
    return save_blend_copy()

def read_settings():
    with open(SKETCHFAB_EXPORT_DATA_FILE, 'r') as s:
        return json.load(s)

def write_result(filepath, filename, size, warnings):
    with open(SKETCHFAB_EXPORT_DATA_FILE, 'w') as s:
        json.dump({
                'filepath': filepath,
                'filename': filename,
                'size': size,
                'warnings': warnings,
                }, s)


if __name__ == "__main__":
    try:
        export_settings = read_settings()
        filepath, filename, size, warnings = prepare_file(export_settings)
        write_result(filepath, filename, size, warnings)
    except:
        import traceback
        traceback.print_exc()
        sys.exit(1)
