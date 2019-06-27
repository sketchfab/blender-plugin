Sketchfab Plugin
======================================
Based on [Blender glTF 2.0 Importer and Exporter](https://github.com/KhronosGroup/glTF-Blender-IO) from [Khronos Group](https://github.com/KhronosGroup)

*Import glTF assets from Sketchfab into Blender using Sketchfab download API*



Version
-------

Sketchfab Plugin v1.0.0 for Blender

#### Note from OSX Blender users
The plugin can have issues to check if it is up-to-date. It's due to openssl using a protocol not alowed anymore on gihtub urls, for security concerns.
One possible fix is to update openssl (or the ssl client your using).
Version check is not mandatory to use the plugin, but you might miss some important updates.

If you are in this case, be sure to check (on a regular basis) if you're using the latest version of the plugin by comparing your version with the one available on https://sketchfab.com/importers/


Installation
------------

You can find the instructions in the [release page](https://github.com/sketchfab/glTF-Blender-IO/releases/latest)

To prepare a development version of the plugin, clone the repo and run [build.sh](build.sh) with the **--patch** flag to patch the Khronos gltf code:

```sh
git clone -b chore/gltf-code-uniformization_D3D-4952 --recursive git@github.com:sketchfab/blender-plugin.git
cd blender-plugin
./build.sh --patch
```
For the next releases, just run ```./build.sh``` in the repository directory.


Report an issue
---------------

#### The issue can be a limit of the plugin
First, please have a look at the [limits on the release documentation](https://github.com/sketchfab/glTF-Blender-IO/releases/latest) to see if the issue is a know limit of the plugin.

#### Report us the issue
You can also report through [this link](https://help.sketchfab.com/hc/en-us/requests/new?type=exporters&subject=Blender+Plugin).

It's also possible to do it directly from the plugin, with the **Report an issue** button.
