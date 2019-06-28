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

from .gltf2_blender_image import *

class BlenderTextureInfo():

    @staticmethod
    def create(gltf, pytextureinfo_idx):
        BlenderTexture.create(gltf, pytextureinfo_idx)

class BlenderTexture():

    @staticmethod
    def create(gltf, pytexture_idx):
        pytexture = gltf.data.textures[pytexture_idx]
        BlenderImage.create(gltf, pytexture.source)

class BlenderTextureNode():

	@staticmethod
	def create(gltf, texture_index, node_tree, label=None):
	    text_node = node_tree.nodes.new('ShaderNodeTexImage')
	    gltf_texture = gltf.data.textures[texture_index]
	    text_node.image = bpy.data.images[gltf.data.images[gltf_texture.source].blender_image_name]

	    if gltf_texture.sampler is None:
	        return

	    sampler = gltf.data.samplers[gltf_texture.sampler]
	    # Only linear and closest for the moment

	    # NEAREST: 9728)
	    # LINEAR: 9729)
	    # NEAREST_MIPMAP_NEAREST: 9984
	    # LINEAR_MIPMAP_NEAREST: 9985
	    # NEAREST_MIPMAP_LINEAR: 9986
	    # LINEAR_MIPMAP_LINEAR: 9987

	    # WRAP_RPEAT: 10497
	    # WRAP_CLAMP_TO_EDGE: 33071
	    # WRAP_MIRRORED_REPEAT: 33648
	    if sampler.min_filter in [9728, 9984, 9986] and sampler.mag_filter in [9728, 9984, 9986]:
	        text_node.interpolation = 'Closest'
	    else:
	        text_node.interpolation = 'Linear'

	    if sampler.wrap_s == 33071 or sampler.wrap_t == 33071:
	        text_node.extension = 'Extend'

	    if label:
	        text_node.label = label

	    return text_node
