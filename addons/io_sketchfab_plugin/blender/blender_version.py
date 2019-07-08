import bpy

class Version:
    """Adjusts functions according to the differences between 2.79 and 2.8"""

    #Render engine
    ENGINE = "CYCLES" if bpy.app.version < (2, 80, 0) else "BLENDER_EEVEE"

    # Selection / Deselection
    def select(obj):
        if bpy.app.version < (2, 80, 0):
            obj.select = True
        else:
            obj.select_set(True)
    def deselect(obj):
        if bpy.app.version < (2, 80, 0):
            obj.select = False
        else:
            obj.select_set(False)

    # Object linking
    def link(scene, obj):
        if bpy.app.version < (2, 80, 0):
            bpy.data.scenes[scene].objects.link(obj)
        else:
            bpy.data.scenes[scene].collection.objects.link(obj)

    # Active object
    def get_active_object():
        if bpy.app.version < (2, 80, 0):
            return bpy.context.scene.objects.active
        else:
            return bpy.context.view_layer.objects.active
    def set_active_object(obj):
        if bpy.app.version < (2, 80, 0):
            bpy.context.scene.objects.active = obj
            #self.select(obj)
        else:
            bpy.context.view_layer.objects.active = obj

    # Matrix multiplication
    def mat_mult(A, B):
        if bpy.app.version < (2, 80, 0):
            return A * B
        else:
            return A @ B

    # Setting colorspace
    def set_colorspace(texture):
        if bpy.app.version < (2, 80, 0):
            texture.color_space = 'NONE'
        else:
            if texture.image:
                texture.image.colorspace_settings.is_data = True

    # Setting the active scene
    def set_scene(scene):
        if bpy.app.version < (2, 80, 0):
            bpy.context.screen.scene = scene
        else:
            bpy.context.window.scene = scene
