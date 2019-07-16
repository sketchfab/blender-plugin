#!/bin/bash
# Creates dev and release (.zip) versions of the Blender plugin
#
# Usage:
#   git clone -b chore/gltf-code-uniformization_D3D-4952 --recursive git@github.com:sketchfab/blender-plugin.git
#   cd blender-plugin/
#   ./build.sh --patch    (AFTER CLONING ONLY)
#   or
#   ./build.sh            (FOR SUBSEQUENT RELEASES)
#
# Make a symlink in blender from Powershell:
# cmd /c mklink /d 'C:/Users/Norgeotloic/AppData/Roaming/Blender Foundation/Blender/2.79/scripts/addons/io_sketchfab_plugin' C:\Users\Norgeotloic\Documents\blender-plugin/

# Get the plugin version
version=$(cat addons/io_sketchfab_plugin/__init__.py | grep "'version': " | grep -o '(.*)' | tr -d '() ' | sed 's/,/-/g')

# If requested, apply the patch on Khronos' submodule (glTF-Blender-IO/)
if [[ $* == *--patch* ]]
then
  echo "Trying to apply khronos-gltf.patch"
  cd glTF-Blender-IO/
  git apply ../khronos-gltf.patch
  cp -r ./addons/io_scene_gltf2/io/ ../addons/io_sketchfab_plugin/io/
  cd ../addons/io_sketchfab_plugin/io/
  sed -i 's/io_scene_gltf2.io/./g' ./*/*.py
  cd ../../../
else
  # Create the ZIP files for release
  mkdir -p releases
  rm -rf releases/*
  cp -r addons/io_sketchfab_plugin/ releases/sketchfab-plugin-$version/
  cd releases/
  zip -r -q sketchfab-plugin-$version.zip sketchfab-plugin-$version/
  cd ..
  echo "Releases available in $(pwd)/releases/"
fi

