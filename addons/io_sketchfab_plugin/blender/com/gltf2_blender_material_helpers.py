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


def get_pbr_node(node_tree):
        pass

def get_output_node(node_tree):
    output = [node for node in node_tree.nodes if node.type == 'OUTPUT_MATERIAL'][0]
    return output

def get_output_surface_input(node_tree):
    output_node = get_output_node(node_tree)
    return output_node.inputs['Surface']

def get_diffuse_texture(node_tree):
    for node in node_tree.nodes:
        print(node.name)
        if node.label == 'BASE COLOR':
            return node

    return None

def get_preoutput_node(node_tree):
    output_node = get_output_node(node_tree)
    return output_node.inputs['Surface'].links[0].from_node

def get_preoutput_node_output(node_tree):
    output_node = get_output_node(node_tree)
    preoutput_node = output_node.inputs['Surface'].links[0].from_node

    # Pre output node is Principled BSDF or any BSDF => BSDF
    if 'BSDF' in preoutput_node.type:
        return preoutput_node.outputs['BSDF']
    elif 'SHADER' in preoutput_node.type:
        return preoutput_node.outputs['Shader']
    else:
        print(preoutput_node.type)


def get_base_color_node(node_tree):
    """ returns the last node of the diffuse block """
    for node in node_tree.nodes:
        if node.label == 'BASE COLOR':
            return node

    return None
