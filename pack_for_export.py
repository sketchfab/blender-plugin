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
SKETCHFAB_EXPORT_DATA_FILE = os.path.join(
    SKETCHFAB_EXPORT_TEMP_DIR,
    "export-sketchfab.json",
)


def save_blend_copy():
    """Legacy export path for Blender versions before 4.0."""
    filename = time.strftime(
        "Sketchfab_%Y_%m_%d_%H_%M_%S.blend",
        time.localtime(time.time()),
    )
    filepath = os.path.join(SKETCHFAB_EXPORT_TEMP_DIR, filename)

    bpy.ops.wm.save_as_mainfile(
        filepath=filepath,
        compress=True,
        copy=True,
    )

    size = os.path.getsize(filepath)
    return filepath, filename, size


def restore_exact_selection(export_settings):
    """Restore the selection captured in the interactive Blender session."""
    if not export_settings.get("selection", False):
        return

    selected_names = set(export_settings.get("selected_objects", []))

    for obj in bpy.data.objects:
        try:
            obj.select_set(False)
        except RuntimeError:
            pass

    selected_objects = []
    for name in selected_names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue

        try:
            obj.select_set(True)
            selected_objects.append(obj)
        except RuntimeError:
            pass

    if selected_objects:
        try:
            bpy.context.view_layer.objects.active = selected_objects[0]
        except (AttributeError, RuntimeError):
            pass


def prepare_assets_legacy(export_settings):
    """Original preparation path for Blender versions before 4.0."""
    hidden = set()
    images = set()

    if export_settings["selection"]:
        for ob in bpy.data.objects:
            if ob.type == "MESH":
                for mat_slot in ob.material_slots:
                    if not mat_slot.material:
                        continue

                    if bpy.app.version < (2, 80, 0):
                        for tex_slot in mat_slot.material.texture_slots:
                            if not tex_slot:
                                continue

                            tex = tex_slot.texture
                            if tex.type == "IMAGE":
                                image = tex.image
                                if image is not None:
                                    images.add(image)

                    if mat_slot.material.use_nodes:
                        nodes = mat_slot.material.node_tree.nodes
                        for node in nodes:
                            if node.type == "TEX_IMAGE" and node.image is not None:
                                images.add(node.image)

            if ob.type == "MESH":
                try:
                    is_visible = ob.visible_get()
                    is_selected = ob.select_get()
                except RuntimeError:
                    hidden.add(ob)
                    continue

                if not is_visible:
                    hidden.add(ob)
                elif not is_selected:
                    ob.hide_set(True)
                    hidden.add(ob)

    for image in images:
        if not image.packed_file:
            try:
                image.pack()
            except Exception:
                import traceback
                traceback.print_exc()

    for ob in hidden:
        bpy.data.objects.remove(ob)

    for mesh in bpy.data.meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)

    for material in bpy.data.materials:
        if material.users == 0:
            bpy.data.materials.remove(material)

    for image in bpy.data.images:
        if image.users == 0:
            bpy.data.images.remove(image)


def export_glb(export_settings):
    """Export GLB through Blender's native glTF exporter on Blender 4.x+."""
    filename = time.strftime(
        "Sketchfab_%Y_%m_%d_%H_%M_%S.glb",
        time.localtime(time.time()),
    )
    filepath = os.path.join(SKETCHFAB_EXPORT_TEMP_DIR, filename)

    selection_only = export_settings.get("selection", False)

    result = bpy.ops.export_scene.gltf(
        filepath=filepath,
        export_format="GLB",
        use_selection=selection_only,
        export_apply=True,
    )

    if result != {"FINISHED"}:
        raise RuntimeError(
            "Blender glTF exporter did not finish successfully: %s" % result
        )

    if not os.path.isfile(filepath):
        raise RuntimeError("GLB export failed: output file was not created")

    size = os.path.getsize(filepath)
    return filepath, filename, size


def prepare_file(export_settings):
    # Blender 4.x uploads use GLB instead of the temporary .blend file.
    # Keep the full temporary scene so selected objects may still evaluate
    # dependencies such as unselected Boolean cutter objects.
    if bpy.app.version >= (4, 0, 0):
        restore_exact_selection(export_settings)
        return export_glb(export_settings)

    # Preserve the established behaviour on older Blender versions.
    prepare_assets_legacy(export_settings)
    return save_blend_copy()


def read_settings():
    with open(SKETCHFAB_EXPORT_DATA_FILE, "r") as settings_file:
        return json.load(settings_file)


def write_result(filepath, filename, size):
    with open(SKETCHFAB_EXPORT_DATA_FILE, "w") as settings_file:
        json.dump(
            {
                "filepath": filepath,
                "filename": filename,
                "size": size,
            },
            settings_file,
        )


if __name__ == "__main__":
    try:
        export_settings = read_settings()
        filepath, filename, size = prepare_file(export_settings)
        write_result(filepath, filename, size)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
