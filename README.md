# Sketchfab Blender Addon

**Directly import and export models from and to Sketchfab in Blender (2.79 and 2.80)**

* [Installation](#installation)
* [Login](#login)
* [Import from Sketchfab](#import-a-model-from-sketchfab)
* [Export to Sketchfab](#export-a-model-to-sketchfab)
* [Known issues](#known-issues)
* [Report an issue](#report-an-issue)
* [Addon development](#addon-development)

*Based on [Blender glTF 2.0 Importer and Exporter](https://github.com/KhronosGroup/glTF-Blender-IO) from [Khronos Group](https://github.com/KhronosGroup)*


## Installation

To install the addon, just download the **sketchfab-x-y-z.zip** file attached to the [latest release](https://github.com/sketchfab/blender-plugin/releases/latest), and install it as a regular blender addon (User Preferences -> Addons -> Install from file).

The addon should then be available in the 3D view:

* Blender 2.79: under the tab 'Sketchfab' in the Tools panel (shortcut **T**).
* Blender 2.80 to 3.1.0: under the tab 'Sketchfab' in the Properties panel (shortcut **N**).

**⚠️ Note to Blender 2.79 OSX/Linux users:** The addon uses its own version of the SSL library. It is embedded within the plugin and should work correctly, but do not hesitate to [report an issue](#report-an-issue) if you encounter any issue related to SSL.


## Login

The login process (mandatory to import or export models) should be straightforward: type in the email adress associated with your Sketchfab account as well as your password in the login form:

![Screenshot from 2019-09-03 10-25-22](https://user-images.githubusercontent.com/52042414/64157665-66849980-ce37-11e9-9806-c74bb1476987.jpg)

Your Sketchfab username should then be displayed upon successful login, and you will gain access to the full import and export capabilities of the addon. 

Please note that your login credentials are stored in a temporary file on your local machine (to automatically log you in when starting Blender). You can clear it by simply logging out of your Sketchfab account through the **Log Out** button.

### Log in as a member of an Organization

If you are a member of an organization, you can select the organization you belong to in the "Sketchfab for Teams" dropdown.

Doing so will allow you to browse, import and export models from and to projects within your organization.

## Import a model from Sketchfab

Once logged in, you should be able to easily import any downloadable model from Sketchfab. 

To do so, just run a search query and adapt the search options in the **Filters** menu.

Note that **PRO** users can use the **My Models** checkbox to import any published model from their own library (even the private ones).

![results](https://user-images.githubusercontent.com/52042414/64158308-84063300-ce38-11e9-9d4d-1b17d0b0c828.jpg)

Clicking the **Search Results** thumbnail will allow you to navigate through the models available for download, and selecting one model will allow you to inspect it before import.

![license](https://user-images.githubusercontent.com/52042414/64158307-84063300-ce38-11e9-89dd-04c37859bb6b.jpg)

Please note that all downloadable models are licensed under specific licenses: make sure to follow the different [Creative Commons licensing schemes](https://help.sketchfab.com/hc/en-us/articles/201368589-Downloading-Models#licenses).


## Export a model to Sketchfab

Exporting should also be straightforward.

You can choose to either export the currently selected model(s) or all visible models, and can also choose to set some model properties, such as its title, description and tags.

You can also choose to keep the exported model as a draft (unchecking the checkbox will directly publish the model), but only **PRO** users can set their models as Private, and optionnaly protect them with a password.

![export](https://user-images.githubusercontent.com/52042414/64161913-b61a9380-ce3e-11e9-89fa-7e15426cfff0.jpg)

### A note on material support

Not all Blender materials and shaders will get correctly exported to Sketchfab.

The best material support comes with the **Principled BSDF** node, having either parameters or image textures plugged into the following channels:

* Base Color
* Roughness
* Metallic
* Normal map
* Alpha
* Emission

Note that the export does not support UVs transformation (through the Mapping node), and that Opacity and Backface Culling parameters should be set in the **Options** tab of the material's Properties panel in order to be directly activated in Sketchfab's 3D settings. 

Here is an example of a compatible node graph with backface culling and alpha mode correctly set (Blender 2.80 - Eevee renderer):

![graph](https://user-images.githubusercontent.com/52042414/64164529-b4070380-ce43-11e9-8602-995b083ac722.jpg)


## Known Issues

If none of the following description matches your problem, please feel free to [report an issue](#report-an-issue).

### Animation (import and export)

Although simple skeletal or keyframed animations should work fine, more complex animations could cause unexpected behaviour.

There is no "quick fix" for those kinds of behaviours, which are actively being worked on on our side.

### Import

Here is a list of known issues on import, as well as some possible fixes. 

Please note that the materials are being converted from Sketchfab to Cycles in Blender 2.79, and Eevee in Blender 2.80. If a material looks wrong, using the **Node editor** could therefore help you fixing possible issues.

#### Empty scene after import

Scale can vary a lot between different models, and models origins are not always intuitively centered. 

The imported models will always be selected after import, and you can try to scale them in order to make them visible (most often, the model will need to be scaled down).

If it's not enough, try to select a mesh in the Outliner view and use numpad '.' (**View to selected** operator) to center the view on it.

#### Transparency

Some models are using refraction on Sketchfab (for glass, ice, water...), which is not supported by glTF and ends up being converted to regular transparency.

In Blender **Node Editor**, refraction can be achieved by tweaking the **IOR** and **Transmission** inputs of the Principled BSDF node, or by mixing a **Refraction BSDF** shader with the original material.

#### Weird seams or normals

Tangent space import is not working yet so you might experience rendering issue on some models with normal maps.

#### Single color model (wrong backface culling)

Backface culling is not well supported on import yet.

It is often used on Sketchfab to create models with outlines (as on [this model](https://sketchfab.com/models/71436ab009684265a2fda0e469f77752) for instance) by duplicating the object, scaling it up, flipping its normals and making the material single sided. 

You can reproduce this behaviour in Blender:

* Blender 2.79: 
	* For the 3D view (not rendered), check the **Backface culling** checkbox in the **Properties Panel** (shortcut **N**), under the **Shading** dropdown.
	* For the rendered view (in Cycles), follow the instructions on [this StackOverflow answer](https://blender.stackexchange.com/a/2083).
* Blender 2.80: In the node editor **Properties Panel**, under the **Options** tab and **Settings** dropdown, make sure to have the **Backface Culling** option toggled on.

#### Unexpected colors (vertex colors)

If your model displays strange color artifacts which don't seem to be caused by textures, you can try checking the model's vertex colors information (**Properties** area -> **Object data** tab -> **Vertex Colors** layer), and delete the data if present.

Vertex colors are indeed always exported in glTF files (to allow edition), and always loaded in Blender. It is possible that this data is corrupted or useless - but disabled on Sketchfab - explaining why the online render looked fine.

### Export

#### Transparency

Some transparency settings might not be processed correctly, and just using a **Transparent BSDF** shader or linking a texture to the **Alpha** input of a **Principled BSDF** node might not be sufficient: try to set the opacity settings in the **Properties Panel** of the Node editor, under the **Options** tab by setting the **Blend Mode** to **Alpha Clip** or **Alpha Blend**.

#### High Resolution textures

In some very specific cases, the processing of your model can crash due to "heavy" textures. 

If your model does not process correctly in Sketchfab and that you are using multiple high resolution textures (for instance materials with 16k textures or multiple 8k textures), you can either try to reduce the original images size or upload your model without texture and add them later in Sketchfab's 3D settings.

#### Texture Colorspace

As of now, textures colorspace set in Blender are not automatically converted to Sketchfab, and although normal maps, roughness, metalness and occlusion textures should be processed correctly, setting a diffuse texture's colorspace in Blender as "Non-Color Data" or a metalness map as "Color" (sRGB in 2.80) will be ignored.


## Report an issue

If you feel like you've encountered a bug not listed in the [known issues](#known-issues), or that the addon lacks an important feature, you can contact us through [Sketchfab's Help Center](https://help.sketchfab.com/hc/en-us/requests/new?type=exporters&subject=Blender+Plugin) (or directly from the addon through the **Report an issue** button).

To help us track a possible error, please try to append the logs of Blender's console in your message:
 
* On Windows, it is available through the menu **Window** -> **Toggle system console**
* On OSX or Linux systems, you can access this data by [starting Blender from the command line](https://docs.blender.org/manual/en/dev/render/workflows/command_line.html). Outputs will then be printed in the shell from which you launched Blender.

## Addon development

To prepare a development version of the addon, you'll first have to clone this repository and update the [Khronos glTF IO](https://github.com/KhronosGroup/glTF-Blender-IO) submodule:
```sh
git clone https://github.com/sketchfab/blender-plugin.git
cd blender-plugin/
git submodule update --init --recursive
```

You'll then need (only once) to patch the code from the Khronos submodule with the command:
```sh
./build.sh --patch
```

The final releases can then be built by executing build.sh without arguments:
```
./build.sh
```