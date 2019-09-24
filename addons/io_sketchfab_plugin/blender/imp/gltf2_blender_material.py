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
from .gltf2_blender_pbrMetallicRoughness import *
from .gltf2_blender_KHR_materials_pbrSpecularGlossiness import *
from .gltf2_blender_map_emissive import *
from .gltf2_blender_map_normal import *
from .gltf2_blender_map_occlusion import *
from ..com.gltf2_blender_material_helpers import *

class BlenderMaterial():

    @staticmethod
    def create(gltf, material_idx, vertex_color):

        pymaterial = gltf.data.materials[material_idx]

        if pymaterial.name is not None:
            name = pymaterial.name
        else:
            name = "Material_" + str(material_idx)

        mat = bpy.data.materials.new(name)
        pymaterial.blender_material = mat.name

        if bpy.app.version >= (2, 80, 0):
            mat.use_backface_culling = (pymaterial.double_sided != True)

        if pymaterial.extensions is not None and 'KHR_materials_pbrSpecularGlossiness' in pymaterial.extensions.keys():
            BlenderKHR_materials_pbrSpecularGlossiness.create(gltf, pymaterial.extensions['KHR_materials_pbrSpecularGlossiness'], mat.name, vertex_color)
        else:
            # create pbr material
            BlenderPbr.create(gltf, pymaterial.pbr_metallic_roughness, mat.name, vertex_color)

        # add emission map if needed
        if pymaterial.emissive_texture is not None:
            BlenderEmissiveMap.create(gltf, material_idx)

        # add normal map if needed
        if pymaterial.normal_texture is not None:
            BlenderNormalMap.create(gltf, material_idx)

        # add occlusion map if needed
        # will be pack, but not used
        if pymaterial.occlusion_texture is not None:
            BlenderOcclusionMap.create(gltf, material_idx)

        if pymaterial.alpha_mode != None and pymaterial.alpha_mode != 'OPAQUE':
            BlenderMaterial.blender_alpha(gltf, material_idx)

    @staticmethod
    def set_uvmap(gltf, material_idx, prim, obj):
        pymaterial = gltf.data.materials[material_idx]

        node_tree = bpy.data.materials[pymaterial.blender_material].node_tree
        uvmap_nodes =  [node for node in node_tree.nodes if node.type in ['UVMAP', 'NORMAL_MAP']]
        for uvmap_node in uvmap_nodes:
            if uvmap_node["gltf2_texcoord"] in prim.blender_texcoord.keys():
                uvmap_node.uv_map = prim.blender_texcoord[uvmap_node["gltf2_texcoord"]]

    @staticmethod
    def blender_alpha(gltf, material_idx):
        pymaterial = gltf.data.materials[material_idx]
        material = bpy.data.materials[pymaterial.blender_material]

        node_tree = material.node_tree

        #Fix alphas for 2.8
        if bpy.app.version >= (2, 80, 0):
            if pymaterial.alpha_mode == 'BLEND':
                material.blend_method = 'BLEND'
            else:
                material.blend_method = 'CLIP'

         # Add nodes for basic transparency
        # Add mix shader between output and Principled BSDF
        trans = node_tree.nodes.new('ShaderNodeBsdfTransparent')
        trans.location = 750, -500
        mix = node_tree.nodes.new('ShaderNodeMixShader')
        mix.location = 1000, 0

        output_surface_input = get_output_surface_input(node_tree)
        preoutput_node_output = get_preoutput_node_output(node_tree)
        pre_output_node = output_surface_input.links[0].from_node

        link = output_surface_input.links[0]
        node_tree.links.remove(link)

         # PBR => Mix input 1
        node_tree.links.new(preoutput_node_output, mix.inputs[1])

         # Trans => Mix input 2
        node_tree.links.new(trans.outputs['BSDF'], mix.inputs[2])

         # Mix => Output
        node_tree.links.new(mix.outputs['Shader'], output_surface_input)

         # alpha blend factor
        add = node_tree.nodes.new('ShaderNodeMath')
        add.operation = 'ADD'
        add.location = 750, -250

        diffuse_factor = 1.0
        if pymaterial.extensions is not None and 'KHR_materials_pbrSpecularGlossiness' in pymaterial.extensions:
            diffuse_factor = pymaterial.extensions['KHR_materials_pbrSpecularGlossiness']['diffuseFactor'][3]
        elif pymaterial.pbr_metallic_roughness:
            diffuse_factor = pymaterial.pbr_metallic_roughness.base_color_factor[3]

        add.inputs[0].default_value = abs(1.0 - diffuse_factor)
        add.inputs[1].default_value = 0.0
        node_tree.links.new(add.outputs['Value'], mix.inputs[0])

         # Take diffuse texture alpha into account if any
        diffuse_texture = get_base_color_node(node_tree)
        if diffuse_texture:
            inverter = node_tree.nodes.new('ShaderNodeInvert')
            inverter.location = 250, -250
            inverter.inputs[1].default_value = (1.0, 1.0, 1.0, 1.0)
            node_tree.links.new(diffuse_texture.outputs['Alpha'], inverter.inputs[0])

            mult = node_tree.nodes.new('ShaderNodeMath')
            mult.operation = 'MULTIPLY' if pymaterial.alpha_mode == 'BLEND' else 'GREATER_THAN'
            mult.location = 500, -250
            alpha_cutoff = 1.0 if pymaterial.alpha_mode == 'BLEND' else 1.0 - pymaterial.alpha_cutoff if pymaterial.alpha_cutoff is not None else 0.5
            mult.inputs[1].default_value = alpha_cutoff
            node_tree.links.new(inverter.outputs['Color'], mult.inputs[0])
            node_tree.links.new(mult.outputs['Value'], add.inputs[0])
