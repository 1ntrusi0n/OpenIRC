"""Optional native Qt administration console. Never imported by the server core."""


def launch(data_dir, debug=False):
    from .main_window import run_gui
    return run_gui(data_dir, debug)
