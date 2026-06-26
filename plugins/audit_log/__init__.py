# Minimal audit_log placeholder plugin to prevent load errors
# This plugin registers no hooks and does nothing.
# It satisfies the expected import path for the previously bundled audit_log plugin.
# If a real audit_log plugin is added later, this file can be replaced.

def register_hooks(ctx):
    # No hooks to register
    return []
