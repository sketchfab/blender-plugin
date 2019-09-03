"""
 * ***** BEGIN GPL LICENSE BLOCK *****
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation,
 * Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
 *
 * Contributor(s): Julien Duroure.
 *
 * ***** END GPL LICENSE BLOCK *****
 """

import bpy
from math import sqrt
from mathutils import Quaternion
from .gltf2_blender_node import *
from .gltf2_blender_skin import *
from .gltf2_blender_animation import *

# Version management
from ..blender_version import Version

class BlenderScene():

    @staticmethod
    def create(gltf, scene_idx, use_current=True, root_name=None):

        pyscene = gltf.data.scenes[scene_idx]

        # Create Yup2Zup empty
        name = root_name if root_name else "GLTF_model"
        obj_rotation = bpy.data.objects.new(name, None)
        obj_rotation.rotation_mode = 'QUATERNION'
        obj_rotation.rotation_quaternion = Quaternion((sqrt(2)/2, sqrt(2)/2,0.0,0.0))

        # Create collection and link it to scene.
        # Assuming that py.context.scene.collection.children[-1] will always return this collection
        if bpy.app.version >= (2, 80, 0):
            import_collection = bpy.data.collections.new(root_name if root_name else 'GLTF_Collection')
            bpy.context.scene.collection.children.link(import_collection)
            import_collection.objects.link(obj_rotation)

        # Create a new scene only if not already exists in .blend file
        # TODO : put in current scene instead ?
        if pyscene.name not in [scene.name for scene in bpy.data.scenes]:
            if pyscene.name and not use_current:
                scene = bpy.data.scenes.new(pyscene.name)
            else:
                scene = bpy.context.scene

            scene.render.engine = Version.ENGINE

            gltf.blender_scene = scene.name
        else:
            gltf.blender_scene = pyscene.name

        bpy.ops.object.select_all(action='DESELECT')

        for node_idx in pyscene.nodes:
            BlenderNode.create(gltf, node_idx, None) # None => No parent


        # Now that all mesh / bones are created, create vertex groups on mesh
        if gltf.data.skins:
            for skin_id, skin in enumerate(gltf.data.skins):
                if hasattr(skin, "node_ids"):
                    BlenderSkin.create_vertex_groups(gltf, skin_id)

            for skin_id, skin in enumerate(gltf.data.skins):
                if hasattr(skin, "node_ids"):
                    BlenderSkin.assign_vertex_groups(gltf, skin_id)

            for skin_id, skin in enumerate(gltf.data.skins):
                if hasattr(skin, "node_ids"):
                    BlenderSkin.create_armature_modifiers(gltf, skin_id)

        if gltf.data.animations:
            for anim_idx, anim in enumerate(gltf.data.animations):
                for node_idx, node in enumerate(pyscene.nodes):
                    BlenderAnimation.anim(gltf, anim_idx, node_idx)


        # Parent root node to rotation object
        Version.link(gltf.blender_scene, obj_rotation)

        for node_idx in pyscene.nodes:
            bpy.data.objects[gltf.data.nodes[node_idx].blender_object].parent = obj_rotation

        # Place imported model on cursor
        obj_rotation.location = bpy.context.scene.cursor_location if bpy.app.version == (2, 79, 0) else bpy.context.scene.cursor.location

        # Make object selected to allow to transform it directly after import
        bpy.ops.object.select_all(action='DESELECT')
        for o in obj_rotation.children:
            Version.select(o)
