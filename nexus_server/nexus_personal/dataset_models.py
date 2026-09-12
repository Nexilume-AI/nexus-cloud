"""Personal host explicitly installs only the core Dataset schema."""


def register_models():
    # Deliberately no pricing table, purchase model, API-key field or IAM shim.
    # Missing configuration is an error; it never defaults to this module.
    pass
