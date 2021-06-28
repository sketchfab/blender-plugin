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

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from blender.blender_version import Version

SKETCHFAB_EXPORT_TEMP_DIR = sys.argv[7]
SKETCHFAB_EXPORT_DATA_FILE = os.path.join(SKETCHFAB_EXPORT_TEMP_DIR, "export-sketchfab.json")

# save a copy of the current blendfile
def save_blend_copy():
    import time

    filepath = SKETCHFAB_EXPORT_TEMP_DIR
    filename = time.strftime("Sketchfab_%Y_%m_%d_%H_%M_%S.blend",
                             time.localtime(time.time()))
    filepath = os.path.join(filepath, filename)
    bpy.ops.wm.save_as_mainfile(filepath=filepath,
                                compress=True,
                                copy=True)
    size = os.path.getsize(filepath)
    return (filepath, filename, size)

# change visibility statuses and pack images
def prepare_assets(export_settings):
    hidden = set()
    images = set()

    # If we did not ask to export all models, do some cleanup
    if export_settings['selection']:

        for ob in bpy.data.objects:
            if ob.type == 'MESH':
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

            if export_settings['selection'] and ob.type == 'MESH':
                # Add relevant objects to the list of objects to remove
                if not Version.get_visible(ob): # Not visible
                    hidden.add(ob)
                elif not Version.get_selected(ob): # Visible but not selected
                    Version.set_visible(ob, False)
                    hidden.add(ob)

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
    prepare_assets(export_settings)
    return save_blend_copy()

def read_settings():
    with open(SKETCHFAB_EXPORT_DATA_FILE, 'r') as s:
        return json.load(s)

def write_result(filepath, filename, size):
    with open(SKETCHFAB_EXPORT_DATA_FILE, 'w') as s:
        json.dump({
                'filepath': filepath,
                'filename': filename,
                'size': size,
                }, s)

        
if __name__ == "__main__":
    try:
        export_settings = read_settings()
        filepath, filename, size = prepare_file(export_settings)
        write_result(filepath, filename, size)
    except:
        import traceback
        traceback.print_exc()
        sys.exit(1)
