"""Optional native Qt administration console. Never imported by the server core."""


def launch(data_dir, debug=False, *, start_server=False):
    from .main_window import run_gui
    return run_gui(data_dir, debug, start_server=start_server)
