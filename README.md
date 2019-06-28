Sketchfab Addon for Blender
======================================
Based on [Blender glTF 2.0 Importer and Exporter](https://github.com/KhronosGroup/glTF-Blender-IO) from [Khronos Group](https://github.com/KhronosGroup)

*Imports glTF assets from Sketchfab into Blender using Sketchfab download API*

Installation & Usage
------------

You can find the documentation and installation instructions on the [latest release page](https://github.com/sketchfab/blender-plugin/releases).

Note for OSX Blender users
------------

The addon can have issues to check if it is up-to-date. It's due to openssl using a protocol not alowed anymore on gihtub urls, for security concerns.
One possible fix is to update openssl (or the ssl client your using).
Version check is not mandatory to use the addon, but you might miss some important updates.

If you are in this case, be sure to check (on a regular basis) if you're using the latest version of the addon by comparing your version with the one available on https://sketchfab.com/importers/

Development
------------

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

Report an issue
---------------

#### The issue can be a limit of the addon
First, please have a look at the [limits on the release documentation](https://github.com/sketchfab/glTF-Blender-IO/releases/latest) to see if the issue is a know limit of the addon.

#### Report us the issue
You can also report through [this link](https://help.sketchfab.com/hc/en-us/requests/new?type=exporters&subject=Blender+Plugin).

It's also possible to do it directly from the addon, with the **Report an issue** button.
