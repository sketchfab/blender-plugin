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

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

SKETCHFAB_EXPORT_TEMP_DIR = sys.argv[7]
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
    for ob in bpy.data.objects:
        if is_object_hidden(ob):
            continue
        if export_settings['selection'] and not is_object_selected(ob):
            continue
        yield ob

def select_only(objs):
    keep = set(objs)
    for ob in bpy.data.objects:
        try:
            ob.select_set(ob in keep)
        except RuntimeError:
            pass

def has_bakeable_modifier(ob):
    return any(m.type != 'ARMATURE' for m in ob.modifiers)

def export_gltf(filepath, use_selection, export_apply):
    bpy.ops.export_scene.gltf(filepath=filepath,
                              export_format='GLB',
                              use_selection=use_selection,
                              use_visible=True,
                              export_apply=export_apply)

# export a GLB using Blender's own glTF exporter. Sketchfab's backend cannot
# process .blend files saved by Blender 4.x, so 4.x uploads go out as GLB instead.
def export_glb(export_settings):
    filename = time.strftime("Sketchfab_%Y_%m_%d_%H_%M_%S.glb", time.localtime(time.time()))
    filepath = os.path.join(SKETCHFAB_EXPORT_TEMP_DIR, filename)

    objects = list(exported_objects(export_settings))
    shape_key_objs = [ob for ob in objects if ob.type == 'MESH' and ob.data.shape_keys]
    other_objs = [ob for ob in objects if ob not in shape_key_objs]

    # export_apply bakes modifiers (needed for a hidden Boolean cutter's cut
    # to show up) but drops shape keys. Split shape-keyed objects into their
    # own pass, then merge the two exports back into one file.
    if not shape_key_objs or not other_objs:
        export_gltf(filepath, export_settings['selection'], not shape_key_objs)
        return (filepath, filename, os.path.getsize(filepath), [])

    warnings = []
    unbaked = [ob.name for ob in shape_key_objs if has_bakeable_modifier(ob)]
    if unbaked:
        warnings.append(
            "Modifiers on these shape-keyed objects were not applied: %s" % ", ".join(unbaked))

    baked_path = os.path.join(SKETCHFAB_EXPORT_TEMP_DIR, "sketchfab_baked.glb")
    keys_path = os.path.join(SKETCHFAB_EXPORT_TEMP_DIR, "sketchfab_keys.glb")
    select_only(other_objs)
    export_gltf(baked_path, True, True)
    select_only(shape_key_objs)
    export_gltf(keys_path, True, False)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=baked_path)
    bpy.ops.import_scene.gltf(filepath=keys_path)
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
                if prop.type != 'POINTER' or getattr(prop.fixed_type, 'identifier', None) != 'Object':
                    continue
                target = getattr(entry, prop.identifier, None)
                if target is not None:
                    refs.add(target)
    if ob.parent is not None:
        refs.add(ob.parent)
    return refs

# keep any removal candidate a surviving object still depends on, so deleting
# a hidden layer never breaks a Boolean cutter, Mirror target, etc.
def resolve_removal_set(candidates):
    removal = set(candidates)
    changed = True
    while changed:
        changed = False
        needed = set()
        for ob in bpy.data.objects:
            if ob not in removal:
                needed |= get_object_refs(ob)
        still_needed = removal & needed
        if still_needed:
            removal -= still_needed
            changed = True
    return removal

# change visibility statuses and pack images
def prepare_assets(export_settings):
    hidden_candidates = set()
    images = set()

    for ob in bpy.data.objects:
        if ob.type != 'MESH':
            continue

        # If we did not ask to export all models, do some cleanup
        if export_settings['selection']:
            for mat_slot in ob.material_slots:
                if not mat_slot.material:
                    continue

                if bpy.app.version < (2, 80, 0):
                    for tex_slot in mat_slot.material.texture_slots:
                        if not tex_slot:
                            continue
                        tex = tex_slot.texture
                        if tex.type == 'IMAGE':
                            image = tex.image
                            if image is not None:
                                images.add(image)

                if mat_slot.material.use_nodes:
                    nodes = mat_slot.material.node_tree.nodes
                    for n in nodes:
                        if n.type == "TEX_IMAGE":
                            if n.image is not None:
                                images.add(n.image)

        # hidden objects are always excluded; unselected-but-visible ones
        # only drop out for a selection-only export
        if is_object_hidden(ob):
            hidden_candidates.add(ob)
        elif export_settings['selection'] and not is_object_selected(ob):
            hidden_candidates.add(ob)

    hidden = resolve_removal_set(hidden_candidates)

    for img in images:
        if not img.packed_file:
            try:
                img.pack()
            except:
                # can fail in rare cases
                import traceback
                traceback.print_exc()

    for ob in hidden:
        bpy.data.objects.remove(ob)

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
