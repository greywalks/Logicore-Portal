from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_gunicorn_uses_one_threaded_worker_for_process_local_session_state():
    for config_path in (ROOT / "Procfile", ROOT / "render.yaml"):
        config = config_path.read_text()
        assert "--worker-class gthread" in config
        assert "--workers 1" in config
        assert "--threads 8" in config
        assert "--workers 2" not in config
